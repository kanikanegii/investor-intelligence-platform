import hashlib
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import AzureOpenAIEmbeddings

from database.ingestion_log import get_active_documents, get_ingestion_record, mark_superseded, record_ingestion
from database.save_metrics import save_metrics
from ingestion.pdf_to_markdown import PDFToMarkdownConverter
from ingestion.semantic_chunker import chunk_pages
from rag.kpi_extractor_rag import extract_financial_metrics
from vectorstore.azure_ai_search import AzureAISearchVectorStore, Retriever

load_dotenv()

logger = logging.getLogger(__name__)


def parse_company_year(pdf_file: Path) -> tuple[str, str]:
    """Parse company and year from a PDF filename.

    Supports names like `2024_Apple.pdf` and `2024_AnnualReport_Apple.pdf`.
    """
    stem = pdf_file.stem
    parts = stem.split("_")

    if parts and parts[0].isdigit():
        year = parts[0]
        company = parts[-1]
    elif len(parts) >= 2:
        company = parts[0]
        year = parts[1]
    else:
        company = stem
        year = ""

    return company, year


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ingest_document(
    pdf_path: str,
    embeddings,
    vector_store: AzureAISearchVectorStore,
    force: bool = False,
) -> None:
    """
    Ingest a single PDF document: convert, chunk, embed, upload, extract, save.

    Idempotent: if this exact file (by content hash) was already ingested
    successfully, the pipeline is skipped unless force=True. Each stage is
    wrapped so a failure is logged and recorded rather than raised unhandled
    (important since this typically runs inside a FastAPI BackgroundTask,
    where an unhandled exception would otherwise vanish silently).

    Args:
        pdf_path: Path to the source PDF.
        embeddings: Azure OpenAI embedding model.
        vector_store: Target Azure AI Search vector store.
        force: Re-ingest even if an unchanged version was already processed.
    """
    pdf_file = Path(pdf_path)
    company, year = parse_company_year(pdf_file)
    pdf_hash = _hash_file(pdf_file)

    if not force:
        existing = get_ingestion_record(pdf_file.name)
        if existing and existing["status"] == "succeeded" and existing["pdf_hash"] == pdf_hash:
            logger.info("Skipping %s: already ingested (unchanged content).", pdf_file.name)
            return

    logger.info("Ingesting %s as company=%r, year=%r", pdf_file.name, company, year)

    try:
        converter = PDFToMarkdownConverter()
        pages = converter.convert_pdf_pages(pdf_path)
        # Also write the flat markdown file for human inspection/debugging.
        converter.convert_pdf(pdf_path=pdf_path, output_dir="data/markdown")

        chunks = chunk_pages(
            pages=pages,
            embeddings=embeddings,
            source_file=pdf_file.name,
            company=company,
            year=year,
        )

        logger.info("Generated %d chunks for %s", len(chunks), pdf_file.name)

        # Reconcile against whatever's already indexed for this exact
        # filename: skip re-embedding chunks whose content is unchanged,
        # and mark stale any old chunk_id no longer produced (e.g. the
        # document shrank and some pages/sections were removed).
        existing_chunks = vector_store.get_indexed_chunk_ids(pdf_file.name)
        new_chunk_ids = {c.metadata.chunk_id for c in chunks}
        orphaned_ids = existing_chunks.keys() - new_chunk_ids
        changed_chunks = [
            c for c in chunks
            if existing_chunks.get(c.metadata.chunk_id) != c.metadata.content_hash
        ]

        vector_store.upload_chunks(chunks=changed_chunks, embeddings=embeddings)

        if orphaned_ids:
            vector_store.mark_chunks_stale(list(orphaned_ids))
            logger.info("Removed %d orphaned chunk(s) from %s", len(orphaned_ids), pdf_file.name)

        for old_source_file in get_active_documents(company, year):
            if old_source_file == pdf_file.name:
                continue
            vector_store.mark_source_file_stale(old_source_file)
            mark_superseded(old_source_file, superseded_by=pdf_file.name)
            logger.info("Superseded %s with %s", old_source_file, pdf_file.name)

        retriever = Retriever(vector_store.client, embeddings)
        metrics = extract_financial_metrics(
            retriever=retriever,
            company=company,
            year=int(year) if year.isdigit() else None,
        )

        if metrics:
            save_metrics(
                company=company,
                year=int(year) if year.isdigit() else None,
                metrics=metrics,
            )

        record_ingestion(
            source_file=pdf_file.name,
            pdf_hash=pdf_hash,
            company=company,
            year=year,
            status="succeeded",
        )
    except Exception as exc:
        logger.exception("Ingestion failed for %s", pdf_file.name)
        record_ingestion(
            source_file=pdf_file.name,
            pdf_hash=pdf_hash,
            company=company,
            year=year,
            status="failed",
            error_message=str(exc),
        )


def ingest_directory(input_dir: str) -> None:
    """
    Ingest all PDFs from a directory.
    """
    embeddings = AzureOpenAIEmbeddings(
        model=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
    )

    vector_store = AzureAISearchVectorStore(
        endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
        api_key=os.getenv("AZURE_SEARCH_API_KEY"),
        index_name=os.getenv("AZURE_SEARCH_INDEX_NAME")
    )

    pdf_files = list(Path(input_dir).glob("*.pdf"))

    logger.info("Found %d PDF(s)", len(pdf_files))

    for pdf_file in pdf_files:
        ingest_document(
            pdf_path=str(pdf_file),
            embeddings=embeddings,
            vector_store=vector_store
        )
