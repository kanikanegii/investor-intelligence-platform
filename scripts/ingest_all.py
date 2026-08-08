import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from common.logging_config import configure_logging
from ingestion.ingest_documents import ingest_directory

logger = logging.getLogger(__name__)

_DEFAULT_INPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "raw_pdfs"


def main() -> None:
    """Ingest every PDF in data/raw_pdfs (or a given directory).

    Idempotent per-file: unchanged files (same content hash) already
    ingested successfully are skipped, see ingestion/ingest_documents.py.
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

    ingest_directory(args.input_dir)
    logger.info("Ingestion complete.")


if __name__ == "__main__":
    main()
