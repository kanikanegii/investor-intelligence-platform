import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from common.logging_config import configure_logging
from database.create_table import create_tables
from database.postgres_sql import create_database
from ingestion.ingest_documents import ingest_directory
from vectorstore.create_index import create_index

logger = logging.getLogger(__name__)

_DEFAULT_INPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "raw_pdfs"


def main() -> None:
    """Run the full ingestion pipeline end to end: infra setup, then ingestion.

    1. Create/verify the Postgres database, tables, and Azure AI Search index.
    2. Ingest every PDF in the input directory (convert -> chunk -> embed ->
       upload -> extract KPIs with citations -> save to Postgres), skipping
       any file already ingested with unchanged content.

    Evaluation is a separate, deliberate step — run it explicitly afterward
    via `python -m evaluation.run_eval` once you have ingested documents to
    evaluate against.
    """
    configure_logging()
    load_dotenv()

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "--input-dir",
        default=str(_DEFAULT_INPUT_DIR),
        help="Directory of PDFs to ingest (default: data/raw_pdfs)",
    )
    args = parser.parse_args()

    logger.info("Step 1/2: setting up infrastructure (database, tables, search index)")
    create_database()
    create_tables()
    create_index(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        api_key=os.environ["AZURE_SEARCH_API_KEY"],
        index_name=os.environ["AZURE_SEARCH_INDEX_NAME"],
    )

    logger.info("Step 2/2: ingesting documents from %s", args.input_dir)
    ingest_directory(args.input_dir)

    logger.info("Pipeline complete. Run `python -m evaluation.run_eval` to evaluate quality.")


if __name__ == "__main__":
    main()
