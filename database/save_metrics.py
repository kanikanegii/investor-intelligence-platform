import json
import logging

from sqlalchemy import text

from database.postgres_sql import get_engine

logger = logging.getLogger(__name__)


def save_metrics(
    company: str,
    year: int,
    metrics
) -> None:
    """
    Save extracted financial metrics to PostgreSQL, including per-field citations.

    Args:
        company: Company name.
        year: Fiscal year.
        metrics: A rag.kpi_extractor_rag.FinancialMetrics instance.
    """
    engine = get_engine()

    query = """
    INSERT INTO financial_metrics (
        company,
        year,
        revenue,
        net_income,
        operating_income,
        cash_flow,
        total_assets,
        total_liabilities,
        risk_factors,
        growth_drivers,
        citations
    )
    VALUES (
        :company,
        :year,
        :revenue,
        :net_income,
        :operating_income,
        :cash_flow,
        :total_assets,
        :total_liabilities,
        :risk_factors,
        :growth_drivers,
        CAST(:citations AS JSONB)
    )
    """

    def as_lines(value) -> str:
        if isinstance(value, list):
            return "\n".join(value)
        return value or ""

    flat = metrics.to_flat_dict()

    params = {
        "company": company,
        "year": str(year),
        "revenue": flat.get("revenue"),
        "net_income": flat.get("net_income"),
        "operating_income": flat.get("operating_income"),
        "cash_flow": flat.get("cash_flow_from_operating_activities"),
        "total_assets": flat.get("total_assets"),
        "total_liabilities": flat.get("total_liabilities"),
        "risk_factors": as_lines(flat.get("top_risk_factors")),
        "growth_drivers": as_lines(flat.get("top_growth_drivers")),
        "citations": json.dumps(metrics.citation_map()),
    }

    with engine.begin() as connection:
        connection.execute(text(query), params)

    logger.info("Successfully saved metrics for %s %s", company, year)