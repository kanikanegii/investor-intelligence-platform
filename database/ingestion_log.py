from sqlalchemy import text

from database.postgres_sql import get_engine

# Ordered pipeline steps, used to derive a fixed-percentage progress bar for
# an in-flight ingestion (see routes/ingestion.py's status endpoint). Not a
# byte-level/granular progress -- each stage is one jump, e.g. 5 stages ==
# ~20% per completed stage.
STAGES = [
    "converting_pdf",
    "chunking_embedding",
    "uploading_to_search",
    "extracting_kpis",
    "saving",
]


def stage_percent(stage: str | None, status: str | None) -> int:
    """Map a (stage, status) pair to a 0-100 progress value for the UI."""
    if status == "succeeded":
        return 100
    if status == "failed":
        return 0
    if stage in STAGES:
        return round((STAGES.index(stage) / len(STAGES)) * 100)
    return 0


def get_ingestion_record(source_file: str) -> dict | None:
    """
    Look up the ingestion record for a source file, if any.

    Args:
        source_file: Original PDF filename.

    Returns:
        The record as a dict (pdf_hash, status, stage, etc.), or None if
        never ingested.
    """
    engine = get_engine()

    query = """
    SELECT source_file, pdf_hash, company, year, status, stage, error_message, ingested_at
    FROM ingested_documents
    WHERE source_file = :source_file
    """

    with engine.connect() as connection:
        result = connection.execute(text(query), {"source_file": source_file})
        row = result.first()

    return dict(row._mapping) if row else None


def start_ingestion(source_file: str, pdf_hash: str, company: str, year: str) -> None:
    """
    Record that ingestion has begun for a source file, before any pipeline
    stage has actually completed.

    Called at the very start of ingest_document(), so a status check made
    immediately after /upload returns 202 sees "processing" rather than
    "not_found" for the (often multi-second) gap before the first real
    stage transition.
    """
    engine = get_engine()

    query = """
    INSERT INTO ingested_documents (source_file, pdf_hash, company, year, status, stage, error_message, ingested_at)
    VALUES (:source_file, :pdf_hash, :company, :year, 'processing', :stage, NULL, CURRENT_TIMESTAMP)
    ON CONFLICT (source_file) DO UPDATE SET
        pdf_hash = EXCLUDED.pdf_hash,
        company = EXCLUDED.company,
        year = EXCLUDED.year,
        status = EXCLUDED.status,
        stage = EXCLUDED.stage,
        error_message = EXCLUDED.error_message,
        ingested_at = EXCLUDED.ingested_at
    """

    with engine.begin() as connection:
        connection.execute(
            text(query),
            {
                "source_file": source_file,
                "pdf_hash": pdf_hash,
                "company": company,
                "year": year,
                "stage": STAGES[0],
            },
        )


def update_stage(source_file: str, stage: str) -> None:
    """
    Advance the in-flight progress marker for a source file already started
    via start_ingestion(). Lightweight, targeted UPDATE -- doesn't touch
    status/error_message, called once per pipeline stage transition.
    """
    engine = get_engine()

    query = """
    UPDATE ingested_documents
    SET stage = :stage
    WHERE source_file = :source_file
    """

    with engine.begin() as connection:
        connection.execute(text(query), {"source_file": source_file, "stage": stage})


def record_ingestion(
    source_file: str,
    pdf_hash: str,
    company: str,
    year: str,
    status: str,
    error_message: str | None = None,
) -> None:
    """
    Upsert the terminal ingestion record for a source file.

    Args:
        source_file: Original PDF filename (primary key).
        pdf_hash: sha256 of the PDF bytes, used to detect content changes.
        company: Parsed company name/ticker.
        year: Parsed fiscal year.
        status: "succeeded" or "failed".
        error_message: Failure detail, if status is "failed".
    """
    engine = get_engine()

    # stage mirrors status on the terminal call -- stage_percent() maps
    # status == "succeeded"/"failed" directly to 100/0 regardless of the
    # stage value, but setting it here too keeps the column meaningful if
    # ever inspected directly (e.g. in the DB) rather than left on
    # whatever the last in-flight stage happened to be.
    query = """
    INSERT INTO ingested_documents (source_file, pdf_hash, company, year, status, stage, error_message, ingested_at)
    VALUES (:source_file, :pdf_hash, :company, :year, :status, :status, :error_message, CURRENT_TIMESTAMP)
    ON CONFLICT (source_file) DO UPDATE SET
        pdf_hash = EXCLUDED.pdf_hash,
        company = EXCLUDED.company,
        year = EXCLUDED.year,
        status = EXCLUDED.status,
        stage = EXCLUDED.stage,
        error_message = EXCLUDED.error_message,
        ingested_at = EXCLUDED.ingested_at
    """

    with engine.begin() as connection:
        connection.execute(
            text(query),
            {
                "source_file": source_file,
                "pdf_hash": pdf_hash,
                "company": company,
                "year": year,
                "status": status,
                "error_message": error_message,
            },
        )


def get_active_documents(company: str, year: str) -> list[str]:
    """
    List source files currently considered the live filing for company+year.

    Used to find prior filings to supersede when a new one for the same
    company+year is ingested (see ingestion/ingest_documents.py).

    Args:
        company: Parsed company name/ticker.
        year: Parsed fiscal year.

    Returns:
        Source filenames with status = 'succeeded' for this company+year.
    """
    engine = get_engine()

    query = """
    SELECT source_file
    FROM ingested_documents
    WHERE company = :company AND year = :year AND status = 'succeeded'
    """

    with engine.connect() as connection:
        result = connection.execute(text(query), {"company": company, "year": year})
        return [row.source_file for row in result]


def mark_superseded(source_file: str, superseded_by: str) -> None:
    """
    Flag a previously-succeeded ingestion record as superseded.

    Args:
        source_file: The older filing being superseded.
        superseded_by: The newer filing's source_file that replaced it.
    """
    engine = get_engine()

    query = """
    UPDATE ingested_documents
    SET status = 'superseded', superseded_by = :superseded_by
    WHERE source_file = :source_file
    """

    with engine.begin() as connection:
        connection.execute(text(query), {"source_file": source_file, "superseded_by": superseded_by})
