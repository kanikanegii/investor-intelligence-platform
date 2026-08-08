import re

from langchain_experimental.text_splitter import SemanticChunker

from dotenv import load_dotenv
load_dotenv()

from ingestion.schemas import ChunkMetadata, DocumentChunk, PageMarkdown, content_hash, make_chunk_id

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def _extract_section(text: str) -> str | None:
    """Best-effort leading markdown heading for a chunk, or None if absent."""
    match = _HEADING_RE.search(text)
    return match.group(1).strip() if match else None


def chunk_pages(
    pages: list[PageMarkdown],
    embeddings,
    source_file: str,
    company: str,
    year: str,
) -> list[DocumentChunk]:
    """
    Semantically chunk each page independently, preserving page-level provenance.

    A semantic breakpoint that would ideally span two pages gets split at the
    page boundary instead. This trade-off buys deterministic page attribution
    (needed for citations) without fragile character-offset recovery against a
    flattened document. Acceptable for filings where tables/statements rarely
    span exactly one page mid-sentence.

    Args:
        pages: Per-page markdown, in document order.
        embeddings: Azure OpenAI embedding model, used by SemanticChunker to
            detect breakpoints.
        source_file: Original PDF filename, stored on every chunk's metadata.
        company: Company ticker/name, stored on every chunk's metadata.
        year: Fiscal year, stored on every chunk's metadata.

    Returns:
        List of DocumentChunk with a document-wide monotonic chunk_index.
    """
    splitter = SemanticChunker(
        embeddings=embeddings,
        breakpoint_threshold_type="percentile"
    )

    chunks: list[DocumentChunk] = []
    chunk_index = 0

    for page in pages:
        for doc in splitter.create_documents([page.text]):
            chunks.append(
                DocumentChunk(
                    metadata=ChunkMetadata(
                        chunk_id=make_chunk_id(source_file, page.page_number, chunk_index),
                        source_file=source_file,
                        company=company,
                        year=year,
                        page_start=page.page_number,
                        page_end=page.page_number,
                        chunk_index=chunk_index,
                        section=_extract_section(doc.page_content),
                        content_hash=content_hash(doc.page_content),
                        page_text=page.text,
                    ),
                    content=doc.page_content,
                )
            )
            chunk_index += 1

    return chunks
