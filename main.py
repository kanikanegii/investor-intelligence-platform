import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from common.logging_config import configure_logging
from database.metrics import get_metrics
from database.postgres_sql import create_database
from database.create_table import create_tables
from vectorstore.create_index import create_index
from routes.health import router as health_router
from routes.dashboard import router as dashboard_router
from routes.ingestion import router as ingestion_router
from routes.chat import router as chat_router

load_dotenv()
configure_logging()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI-Powered Investor Intelligence Platform",
    version="1.0.0"
)


@app.on_event("startup")
def startup_event():
    """
    Initialize database and vector index on app startup.
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
