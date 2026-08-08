from sqlalchemy import text

from database.postgres_sql import get_engine


def get_ingestion_record(source_file: str) -> dict | None:
    """
    Look up the ingestion record for a source file, if any.

    Args:
        source_file: Original PDF filename.

    Returns:
        The record as a dict (pdf_hash, status, etc.), or None if never ingested.
    """
    engine = get_engine()

    query = """
    SELECT source_file, pdf_hash, company, year, status, error_message, ingested_at
    FROM ingested_documents
    WHERE source_file = :source_file
    """

    with engine.connect() as connection:
        result = connection.execute(text(query), {"source_file": source_file})
        row = result.first()

    return dict(row._mapping) if row else None


def record_ingestion(
    source_file: str,
    pdf_hash: str,
    company: str,
    year: str,
    status: str,
    error_message: str | None = None,
) -> None:
    """
    Upsert the ingestion record for a source file.

    Args:
        source_file: Original PDF filename (primary key).
        pdf_hash: sha256 of the PDF bytes, used to detect content changes.
        company: Parsed company name/ticker.
        year: Parsed fiscal year.
        status: "succeeded" or "failed".
        error_message: Failure detail, if status is "failed".
    """
    engine = get_engine()

    query = """
    INSERT INTO ingested_documents (source_file, pdf_hash, company, year, status, error_message, ingested_at)
    VALUES (:source_file, :pdf_hash, :company, :year, :status, :error_message, CURRENT_TIMESTAMP)
    ON CONFLICT (source_file) DO UPDATE SET
        pdf_hash = EXCLUDED.pdf_hash,
        company = EXCLUDED.company,
        year = EXCLUDED.year,
        status = EXCLUDED.status,
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
