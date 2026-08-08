import logging

from pydantic import BaseModel

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import QueryType, VectorizedQuery

from common.retry import with_retry
from ingestion.schemas import DocumentChunk

logger = logging.getLogger(__name__)

_UPLOAD_BATCH_SIZE = 100
_SEMANTIC_CONFIGURATION_NAME = "default-semantic-config"


class RetrievedChunk(BaseModel):
    """A chunk returned from retrieval, with full provenance for citations."""
    chunk_id: str
    content: str
    company: str
    year: str
    source_file: str
    page_start: int
    page_end: int
    page_text: str
    score: float


def _batched(items: list, batch_size: int):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def _escape_odata_literal(value: str) -> str:
    """
    Escape a value for safe interpolation into an OData string literal.

    OData (like SQL) delimits string literals with single quotes; the
    standard escape for a literal quote inside the value is to double it.
    Without this, request-controlled values (company/year on /chat) could
    break out of the intended literal and inject arbitrary filter clauses.
    """
    return value.replace("'", "''")


class AzureAISearchVectorStore:
    """Azure AI Search vector store."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        index_name: str
    ) -> None:
        self.client = SearchClient(
            endpoint=endpoint,
            index_name=index_name,
            credential=AzureKeyCredential(api_key)
        )

    def upload_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings,
    ) -> int:
        """
        Embed and upload chunks to Azure AI Search.

        Uses each chunk's deterministic chunk_id as the document key and
        merge_or_upload_documents, so re-ingesting the same document upserts
        instead of duplicating entries.

        Args:
            chunks: Chunks to upload, with provenance metadata attached.
            embeddings: Azure OpenAI embedding model.

        Returns:
            Number of chunks successfully uploaded.
        """
        if not chunks:
            return 0

        # One batched call instead of one embed_query() per chunk.
        vectors = with_retry(embeddings.embed_documents)([c.content for c in chunks])

        documents = [
            {
                "id": chunk.metadata.chunk_id,
                "company": chunk.metadata.company,
                "year": chunk.metadata.year,
                "source_file": chunk.metadata.source_file,
                "content": chunk.content,
                "content_vector": vector,
                "chunk_index": chunk.metadata.chunk_index,
                "page_start": chunk.metadata.page_start,
                "page_end": chunk.metadata.page_end,
                "section": chunk.metadata.section,
                "content_hash": chunk.metadata.content_hash,
                "page_text": chunk.metadata.page_text,
                "is_current": True,
            }
            for chunk, vector in zip(chunks, vectors)
        ]

        uploaded = 0
        for batch in _batched(documents, _UPLOAD_BATCH_SIZE):
            result = with_retry(self.client.merge_or_upload_documents)(batch)
            uploaded += sum(item.succeeded for item in result)

        logger.info("Uploaded %d/%d chunks.", uploaded, len(documents))
        return uploaded

    def get_indexed_chunk_ids(self, source_file: str) -> dict[str, str]:
        """
        Look up every currently-indexed chunk for `source_file`, with its content hash.

        Used to reconcile a re-ingested document against what's already in
        the index: chunks whose hash is unchanged can skip re-embedding,
        and chunk_ids no longer produced by the new version are orphaned
        (see ingestion/ingest_documents.py).

        Args:
            source_file: Original PDF filename.

        Returns:
            Mapping of chunk_id -> content_hash for every indexed chunk from
            this source file (empty dict if none indexed yet).
        """
        filter_expr = f"source_file eq '{_escape_odata_literal(source_file)}'"
        results = with_retry(self.client.search)(
            search_text="*",
            filter=filter_expr,
            select=["id", "content_hash"],
        )
        return {result["id"]: result.get("content_hash", "") for result in results}

    def mark_chunks_stale(self, chunk_ids: list[str]) -> int:
        """
        Flag the given chunk ids as no longer current.

        Chunks are kept in the index (not deleted) for audit/history;
        Retriever.invoke's default `is_current eq true` filter is what
        actually excludes them from retrieval.

        Args:
            chunk_ids: Ids to mark stale.

        Returns:
            Number of chunks updated.
        """
        updated = 0
        for batch in _batched(chunk_ids, _UPLOAD_BATCH_SIZE):
            partial_docs = [{"id": chunk_id, "is_current": False} for chunk_id in batch]
            result = with_retry(self.client.merge_documents)(partial_docs)
            updated += sum(item.succeeded for item in result)

        logger.info("Marked %d/%d chunks stale.", updated, len(chunk_ids))
        return updated

    def mark_source_file_stale(self, source_file: str) -> int:
        """
        Flag every indexed chunk from `source_file` as no longer current.

        Called when a newer filing for the same company+year is ingested
        (see ingestion/ingest_documents.py).

        Args:
            source_file: The superseded filing's original PDF filename.

        Returns:
            Number of chunks updated.
        """
        chunk_ids = list(self.get_indexed_chunk_ids(source_file).keys())
        return self.mark_chunks_stale(chunk_ids)


class Retriever:
    """Hybrid (vector + keyword) retriever over the Azure AI Search index."""

    def __init__(self, client: SearchClient, embeddings) -> None:
        self.client = client
        self.embeddings = embeddings

    def invoke(
        self,
        query: str,
        company: str | None = None,
        year: str | None = None,
        top_k: int = 20,
        vector_query_text: str | None = None,
    ) -> list[RetrievedChunk]:
        """
        Retrieve relevant chunks via hybrid vector + keyword + semantic search.

        Args:
            query: Natural-language query, drives the keyword and semantic
                reranking legs.
            company: Optional company filter.
            year: Optional fiscal year filter.
            top_k: Number of chunks to retrieve.
            vector_query_text: If given, embed this text for the vector leg
                instead of `query` (used by HyDE, which embeds a hypothetical
                answer rather than the question itself).

        Returns:
            Retrieved chunks with full provenance for downstream citations.
        """
        filter_clauses = ["is_current eq true"]
        if company and year:
            filter_clauses.append(f"company eq '{_escape_odata_literal(company)}'")
            filter_clauses.append(f"year eq '{_escape_odata_literal(year)}'")
        filter_expr = " and ".join(filter_clauses)

        embedding_text = vector_query_text or query
        vector_query = VectorizedQuery(
            vector=with_retry(self.embeddings.embed_query)(embedding_text),
            k_nearest_neighbors=top_k,
            fields="content_vector",
        )

        results = with_retry(self.client.search)(
            search_text=query,
            vector_queries=[vector_query],
            filter=filter_expr,
            top=top_k,
            query_type=QueryType.SEMANTIC,
            semantic_configuration_name=_SEMANTIC_CONFIGURATION_NAME,
        )

        return [
            RetrievedChunk(
                chunk_id=result["id"],
                content=result.get("content", ""),
                company=result.get("company", ""),
                year=result.get("year", ""),
                source_file=result.get("source_file", ""),
                page_start=result.get("page_start") or 0,
                page_end=result.get("page_end") or 0,
                page_text=result.get("page_text") or result.get("content", ""),
                score=result["@search.score"],
            )
            for result in results
        ]
