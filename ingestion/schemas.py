import hashlib

from pydantic import BaseModel


class PageMarkdown(BaseModel):
    """A single page of a PDF, converted to markdown."""
    page_number: int
    text: str


class ChunkMetadata(BaseModel):
    """Provenance for a single chunk: where it came from and where it lives downstream."""
    chunk_id: str
    source_file: str
    company: str
    year: str
    page_start: int
    page_end: int
    chunk_index: int
    section: str | None = None
    content_hash: str
    # Full parent page text, denormalized onto every chunk from that page.
    # Used by rag/context_compression.py::merge_to_parent_pages for
    # hierarchical/auto-merging retrieval (expand a small matched chunk back
    # out to its full page context before it reaches the LLM).
    page_text: str


class DocumentChunk(BaseModel):
    """A chunk of text plus its provenance metadata."""
    metadata: ChunkMetadata
    content: str


def make_chunk_id(source_file: str, page_start: int, chunk_index: int) -> str:
    """Deterministic chunk id, used as the Azure Search document key for idempotent upserts."""
    raw = f"{source_file}:{page_start}:{chunk_index}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def content_hash(content: str) -> str:
    """Hash of chunk text, used to detect unchanged chunks across re-ingestion runs."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
