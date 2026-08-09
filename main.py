import logging
import os
from contextlib import asynccontextmanager

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient as AsyncSearchClient
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_openai import AzureOpenAIEmbeddings
from dotenv import load_dotenv

from common.logging_config import configure_logging
from database.metrics import get_metrics
from database.postgres_sql import create_database
from database.create_table import create_tables
from llm.azure_openai import get_async_openai_client
from storage.blob_storage import get_blob_service_client
from vectorstore.create_index import create_index
from routes.health import router as health_router
from routes.dashboard import router as dashboard_router
from routes.ingestion import router as ingestion_router
from routes.chat import router as chat_router

load_dotenv()
configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    App-lifetime setup/teardown.

    Database/index creation stays synchronous (startup-only, doesn't need
    to be fast). The Azure OpenAI and AI Search clients are created here
    -- once -- and reused for every /api/chat request via app.state,
    instead of each request opening its own fresh connection pool (real,
    measurable latency, not just a style preference: every request would
    otherwise pay a full TCP/TLS handshake before even starting real work).
    Ingestion's own retrieval/extraction still uses separate sync clients
    (it runs via BackgroundTasks, off the request/response critical path,
    so there's no equivalent per-request cost to avoid there) -- but the
    Blob Storage client below is shared with the upload route directly,
    since that request-time write benefits from the same connection reuse.
    """
    create_database()
    create_tables()

    try:
        create_index(
            endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
            api_key=os.getenv("AZURE_SEARCH_API_KEY"),
            index_name=os.getenv("AZURE_SEARCH_INDEX_NAME")
        )
    except Exception as e:
        logger.warning("Could not create vector index: %s", e)

    app.state.async_openai_client = get_async_openai_client()
    app.state.async_search_client = AsyncSearchClient(
        endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
        index_name=os.getenv("AZURE_SEARCH_INDEX_NAME"),
        credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_API_KEY")),
    )
    app.state.embeddings = AzureOpenAIEmbeddings(
        model=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
    )
    app.state.blob_service_client = get_blob_service_client()

    yield

    await app.state.async_openai_client.close()
    await app.state.async_search_client.close()
    await app.state.blob_service_client.close()


app = FastAPI(
    title="AI-Powered Investor Intelligence Platform",
    version="1.0.0",
    lifespan=lifespan,
)


app.include_router(
    health_router,
    tags=["Health"]
)

app.include_router(
    dashboard_router,
    prefix="/api",
    tags=["Dashboard"]
)

app.include_router(
    ingestion_router,
    prefix="/api",
    tags=["Ingestion"]
)

app.include_router(
    chat_router,
    prefix="/api",
    tags=["Chat"]
)

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

templates = Jinja2Templates(
    directory="templates"
)


@app.get("/")
def dashboard(request: Request):
    """
    Render dashboard UI.
    """
    metrics = get_metrics()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "metrics": metrics,
            "total_companies": len(metrics),
            "total_reports": len(metrics),
            "azure_client_id": os.environ["AZURE_CLIENT_ID"],
            "azure_tenant_id": os.environ["AZURE_TENANT_ID"],
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )
