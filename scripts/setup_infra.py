import logging
import os

from dotenv import load_dotenv

from common.logging_config import configure_logging
from database.create_table import create_tables
from database.postgres_sql import create_database
from vectorstore.create_index import create_index

logger = logging.getLogger(__name__)


def main() -> None:
    """Create the Postgres database/tables and the Azure AI Search index.

    Idempotent: safe to run repeatedly (CREATE ... IF NOT EXISTS / create_or_update_index).
    """
    configure_logging()
    load_dotenv()

    create_database()
    create_tables()
    create_index(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        api_key=os.environ["AZURE_SEARCH_API_KEY"],
        index_name=os.environ["AZURE_SEARCH_INDEX_NAME"],
    )
    logger.info("Infrastructure setup complete.")


if __name__ == "__main__":
    main()
