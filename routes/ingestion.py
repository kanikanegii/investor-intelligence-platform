import logging
import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, UploadFile
from langchain_openai import AzureOpenAIEmbeddings

from auth.entra import require_role
from database.ingestion_log import get_ingestion_record, stage_percent
from ingestion.ingest_documents import ingest_document
from storage.blob_storage import upload_pdf
from vectorstore.azure_ai_search import AzureAISearchVectorStore

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/upload", status_code=202)
async def upload_document(
    http_request: Request,
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    claims: dict = Depends(require_role("Ingestion.Write")),
):
    """
    Accept a PDF upload and process it in the background.

    Returns immediately with 202 Accepted; the actual conversion, chunking,
    embedding, extraction, and DB save happen asynchronously. Poll
    ingested_documents (via database.ingestion_log.get_ingestion_record) for
    completion status. Runs as a FastAPI BackgroundTask rather than blocking
    the request, since a full pipeline run can take well over the lifetime of
    a synchronous HTTP request.
    """
    logger.info("Upload accepted from %s (%s): %s", claims.get("name"), claims.get("oid"), file.filename)

    content = await file.read()

    # Local disk is what the pipeline actually reads from (pymupdf4llm needs
    # a real file path) -- but it's ephemeral, no PersistentVolumeClaim, so
    # it's lost on the next pod restart/redeploy. Blob Storage below is the
    # durable copy.
    upload_dir = Path("data/raw_pdfs")
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / file.filename
    file_path.write_bytes(content)

    try:
        blob_url = await upload_pdf(http_request.app.state.blob_service_client, file.filename, content)
        logger.info("Persisted %s to Blob Storage: %s", file.filename, blob_url)
    except Exception:
        # Durability nice-to-have, not a hard dependency -- ingestion should
        # still proceed off the local copy even if Blob Storage is briefly
        # unavailable. Logged clearly so a persistent failure is noticeable.
        logger.exception("Failed to persist %s to Blob Storage (continuing with local copy only)", file.filename)

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

    background_tasks.add_task(
        ingest_document,
        pdf_path=str(file_path),
        embeddings=embeddings,
        vector_store=vector_store,
    )

    return {
        "message": "Document accepted for processing",
        "file_name": file.filename,
        "status": "processing"
    }


@router.get("/upload/status/{file_name}")
async def upload_status(
    file_name: str,
    claims: dict = Depends(require_role("Ingestion.Write")),
):
    """
    Check the outcome of a previously submitted upload.

    /upload only reports that a file was *accepted*, not that ingestion
    actually succeeded -- the real pipeline runs in the background after
    that response is already sent. A caller that wants to know the real
    outcome (as opposed to just assuming success after some fixed delay,
    which is what the dashboard used to do) should poll this endpoint by
    file_name until status is "succeeded" or "failed". `percent` is a
    fixed-stage approximation (see database.ingestion_log.STAGES), not a
    byte-level/granular progress value.
    """
    record = get_ingestion_record(file_name)

    if record is None:
        return {"file_name": file_name, "status": "not_found", "percent": 0}

    return {
        "file_name": file_name,
        "status": record["status"],
        "stage": record["stage"],
        "percent": stage_percent(record["stage"], record["status"]),
        "company": record["company"],
        "year": record["year"],
        "error_message": record["error_message"],
        "ingested_at": record["ingested_at"],
    }
