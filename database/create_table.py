import logging

from sqlalchemy import text

from database.postgres_sql import get_engine

logger = logging.getLogger(__name__)


def create_tables() -> None:
    engine = get_engine()

    financial_metrics_query = """
    CREATE TABLE IF NOT EXISTS financial_metrics (
        id SERIAL PRIMARY KEY,
        company VARCHAR(100),
        year VARCHAR(10),
        revenue TEXT,
        net_income TEXT,
        operating_income TEXT,
        cash_flow TEXT,
        total_assets TEXT,
        total_liabilities TEXT,
        risk_factors TEXT,
        growth_drivers TEXT,
        citations JSONB,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """

    # Tracks per-source-file ingestion state for idempotent re-ingestion:
    # a matching pdf_hash means the file was already processed successfully,
    # so ingest_document() can skip re-running the pipeline.
    ingested_documents_query = """
    CREATE TABLE IF NOT EXISTS ingested_documents (
        source_file VARCHAR(255) PRIMARY KEY,
        pdf_hash VARCHAR(64) NOT NULL,
        company VARCHAR(100),
        year VARCHAR(10),
        status VARCHAR(20) NOT NULL,
        error_message TEXT,
        ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """

    # ADD COLUMN IF NOT EXISTS is safe against existing deployments where
    # financial_metrics predates the citations column: CREATE TABLE IF NOT EXISTS
    # above is a no-op once the table already exists, so the migration handles
    # the upgrade path explicitly.
    add_citations_column_query = """
    ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS citations JSONB;
    """

    # Tracks which older filing (if any) a newer ingestion for the same
    # company+year superseded. status also takes the value 'superseded' in
    # addition to 'succeeded'/'failed' (see database/ingestion_log.py).
    add_superseded_by_column_query = """
    ALTER TABLE ingested_documents ADD COLUMN IF NOT EXISTS superseded_by VARCHAR(255);
    """

    # Tracks which pipeline step an in-flight ingestion is currently on, so
    # the dashboard can show real stage-based progress instead of a fake
    # ticking animation (see database/ingestion_log.py's STAGES list).
    add_stage_column_query = """
    ALTER TABLE ingested_documents ADD COLUMN IF NOT EXISTS stage VARCHAR(30);
    """

    with engine.begin() as connection:
        connection.execute(text(financial_metrics_query))
        connection.execute(text(add_citations_column_query))
        connection.execute(text(ingested_documents_query))
        connection.execute(text(add_superseded_by_column_query))
        connection.execute(text(add_stage_column_query))

    logger.info("financial_metrics and ingested_documents tables created.")