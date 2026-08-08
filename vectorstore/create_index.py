import logging

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile
)

logger = logging.getLogger(__name__)


def create_index(
    endpoint: str,
    api_key: str,
    index_name: str,
    embedding_dimensions: int = 1536
) -> None:
    """
    Create Azure AI Search index.

    Args:
        endpoint: Azure AI Search endpoint.
        api_key: Azure AI Search API key.
        index_name: Index name.
        embedding_dimensions: Embedding dimensions.
    """
    client = SearchIndexClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(api_key)
    )

    fields = [
        # chunk_id is the deterministic key (see ingestion/schemas.py::make_chunk_id) that
        # makes re-ingestion idempotent: merge_or_upload_documents() upserts by this key
        # instead of duplicating chunks on every run.
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="company", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="year", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_file", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="content", type=SearchFieldDataType.String, searchable=True),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=embedding_dimensions,
            vector_search_profile_name="vector-profile"
        ),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
        SimpleField(name="page_start", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="page_end", type=SearchFieldDataType.Int32, filterable=True),
        SearchField(name="section", type=SearchFieldDataType.String, searchable=True),
        SimpleField(name="content_hash", type=SearchFieldDataType.String, filterable=True),
        # Full parent page text, stored/retrievable only (not searchable) so it
        # doesn't get double-weighted in keyword relevance scoring alongside
        # `content`. Used for hierarchical/auto-merging retrieval.
        SimpleField(name="page_text", type=SearchFieldDataType.String),
        # False once a newer filing for the same company+year supersedes this
        # chunk's source document (see Retriever.invoke and
        # AzureAISearchVectorStore.mark_source_file_stale). Retrieval filters
        # on this by default so stale filings don't surface in /chat or
        # extraction.
        SimpleField(name="is_current", type=SearchFieldDataType.Boolean, filterable=True),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(name="hnsw-config")
        ],
        profiles=[
            VectorSearchProfile(
                name="vector-profile",
                algorithm_configuration_name="hnsw-config"
            )
        ]
    )

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="default-semantic-config",
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name="content")]
                ),
            )
        ]
    )

    index = SearchIndex(
        name=index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search
    )

    client.create_or_update_index(index)

    logger.info("Index '%s' created successfully.", index_name)
