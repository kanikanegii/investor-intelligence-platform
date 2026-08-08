import logging

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from common.prompt_safety import TRUST_BOUNDARY_INSTRUCTION, neutralize_tag_escapes, wrap_untrusted
from llm.azure_openai import get_structured_completion
from rag.advanced_retrieval import advanced_retrieve
from vectorstore.azure_ai_search import Retriever, RetrievedChunk

load_dotenv()

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM_PROMPT = (
    "You are a meticulous financial analyst extracting structured data from "
    "SEC filings for an investor-facing product. Precision matters more than "
    "completeness: never state a figure that is not explicitly present in the "
    "provided context, never round or estimate, and never fill a field with "
    "a plausible-sounding value you were not given. When in doubt, return "
    "null rather than guess."
)


class Citation(BaseModel):
    """A resolved, verifiable citation: a [n] marker mapped back to its source chunk."""
    marker: int
    chunk_id: str
    source_file: str
    page: int


class MetricWithCitation(BaseModel):
    """An extracted value plus the citations that support it."""
    value: str | int | None = None
    citations: list[Citation] = Field(default_factory=list)


class RawMetricCitation(BaseModel):
    """LLM output shape: a value plus raw [n] marker integers (not yet resolved)."""
    value: str | int | None = None
    citation_markers: list[int] = Field(default_factory=list)


class FinancialMetricsRaw(BaseModel):
    """Structured completion response model — what the LLM actually fills in."""
    revenue: RawMetricCitation | None = None
    net_income: RawMetricCitation | None = None
    operating_income: RawMetricCitation | None = None
    cash_flow_from_operating_activities: RawMetricCitation | None = None
    total_assets: RawMetricCitation | None = None
    total_liabilities: RawMetricCitation | None = None
    top_risk_factors: list[RawMetricCitation] | None = None
    top_growth_drivers: list[RawMetricCitation] | None = None


class FinancialMetrics(BaseModel):
    """Extracted KPIs with resolved, verifiable citations per field."""
    revenue: MetricWithCitation | None = None
    net_income: MetricWithCitation | None = None
    operating_income: MetricWithCitation | None = None
    cash_flow_from_operating_activities: MetricWithCitation | None = None
    total_assets: MetricWithCitation | None = None
    total_liabilities: MetricWithCitation | None = None
    top_risk_factors: list[MetricWithCitation] | None = None
    top_growth_drivers: list[MetricWithCitation] | None = None

    def to_flat_dict(self) -> dict:
        """Adapter for save_metrics.py / dashboard: plain scalar values, no citation objects."""
        def value_of(metric: MetricWithCitation | None):
            return metric.value if metric else None

        def values_of(metrics: list[MetricWithCitation] | None):
            return [m.value for m in metrics] if metrics else []

        return {
            "revenue": value_of(self.revenue),
            "net_income": value_of(self.net_income),
            "operating_income": value_of(self.operating_income),
            "cash_flow_from_operating_activities": value_of(self.cash_flow_from_operating_activities),
            "total_assets": value_of(self.total_assets),
            "total_liabilities": value_of(self.total_liabilities),
            "top_risk_factors": values_of(self.top_risk_factors),
            "top_growth_drivers": values_of(self.top_growth_drivers),
        }

    def citation_map(self) -> dict:
        """Field name -> list of citation dicts, for persistence in the citations JSON column."""
        def citations_of(metric: MetricWithCitation | None):
            return [c.model_dump() for c in metric.citations] if metric else []

        def citations_of_list(metrics: list[MetricWithCitation] | None):
            return [c.model_dump() for m in (metrics or []) for c in m.citations]

        return {
            "revenue": citations_of(self.revenue),
            "net_income": citations_of(self.net_income),
            "operating_income": citations_of(self.operating_income),
            "cash_flow_from_operating_activities": citations_of(self.cash_flow_from_operating_activities),
            "total_assets": citations_of(self.total_assets),
            "total_liabilities": citations_of(self.total_liabilities),
            "top_risk_factors": citations_of_list(self.top_risk_factors),
            "top_growth_drivers": citations_of_list(self.top_growth_drivers),
        }


def retrieve_context(
    retriever: Retriever,
    company: str,
    year: int
) -> tuple[str, list[RetrievedChunk]]:
    """
    Retrieve broad financial context from the vector store as citation-numbered blocks.

    Args:
        retriever: Hybrid vector+keyword retriever.
        company: Company name/ticker.
        year: Fiscal year.

    Returns:
        A tuple of (context string with [n] markers, the retrieved chunks in
        the same order as the markers) — the chunk list is needed to resolve
        markers back to real citations after extraction.
    """
    query = f"""
    Annual report financial statements,
    income statement,
    balance sheet,
    cash flow statement,
    risks,
    growth drivers,
    financial performance
    for {company} fiscal year {year}
    """

    documents = advanced_retrieve(
        retriever=retriever,
        question=query,
        company=company,
        year=str(year),
    )

    blocks = [
        f"[{i}] ({doc.company} {doc.year}, p.{doc.page_start}): {neutralize_tag_escapes(doc.content)}"
        for i, doc in enumerate(documents, start=1)
    ]

    return "\n\n".join(blocks), documents


def build_extraction_prompt(
    company: str,
    year: int,
    context: str
) -> str:
    """
    Build the KPI extraction prompt.
    """
    return f"""Company: {company}
Year: {year}

{TRUST_BOUNDARY_INSTRUCTION}

{wrap_untrusted(context)}

Extract the following fields from the <context> content only: revenue, net
income, operating income, cash flow from operating activities, total assets,
total liabilities, top risk factors, top growth drivers.

Rules:
- Use only the provided context — never rely on outside knowledge of the company.
- If a field is not present in the context, its value must be null. Do not
  estimate, infer, or carry over a figure from a different fiscal year.
- Quote financial values exactly as they appear in the context (same units,
  same formatting) — do not reformat, round, or convert them.
- Risk factors and growth drivers: one concise sentence each, paraphrased
  from the context, not verbatim copy-paste of entire paragraphs.
- For every extracted value, list the citation_markers (integers) of the
  context block(s) it came from. Only use marker numbers that appear inside
  <context> — inventing a marker number is a critical error.
- If a field truly has no supporting block, its citation_markers must be an
  empty list — never attach a marker for a block that doesn't support it.
"""


def _resolve_citations(markers: list[int], documents: list[RetrievedChunk]) -> list[Citation]:
    """Resolve LLM-emitted [n] markers against the known retrieved chunks.

    This is what turns a bare integer the model could hallucinate into a
    verified citation: any marker outside the retrieved range is dropped.
    """
    resolved = []
    for marker in markers:
        if 1 <= marker <= len(documents):
            doc = documents[marker - 1]
            resolved.append(
                Citation(marker=marker, chunk_id=doc.chunk_id, source_file=doc.source_file, page=doc.page_start)
            )
        else:
            logger.warning("Dropping invalid citation marker %d (only %d chunks retrieved)", marker, len(documents))
    return resolved


def _resolve_metric(raw: RawMetricCitation | None, documents: list[RetrievedChunk]) -> MetricWithCitation | None:
    if raw is None:
        return None
    return MetricWithCitation(value=raw.value, citations=_resolve_citations(raw.citation_markers, documents))


def _resolve_metric_list(
    raw_list: list[RawMetricCitation] | None, documents: list[RetrievedChunk]
) -> list[MetricWithCitation] | None:
    if raw_list is None:
        return None
    return [_resolve_metric(raw, documents) for raw in raw_list]


def extract_financial_metrics(
    retriever: Retriever,
    company: str,
    year: int
) -> FinancialMetrics:
    """
    Extract KPIs using RAG, with each value's citations resolved to real chunks.
    """
    context, documents = retrieve_context(
        retriever=retriever,
        company=company,
        year=year
    )

    prompt = build_extraction_prompt(
        company=company,
        year=year,
        context=context
    )

    raw_metrics = get_structured_completion(
        prompt=prompt,
        response_model=FinancialMetricsRaw,
        system_prompt=_EXTRACTION_SYSTEM_PROMPT,
    )

    return FinancialMetrics(
        revenue=_resolve_metric(raw_metrics.revenue, documents),
        net_income=_resolve_metric(raw_metrics.net_income, documents),
        operating_income=_resolve_metric(raw_metrics.operating_income, documents),
        cash_flow_from_operating_activities=_resolve_metric(
            raw_metrics.cash_flow_from_operating_activities, documents
        ),
        total_assets=_resolve_metric(raw_metrics.total_assets, documents),
        total_liabilities=_resolve_metric(raw_metrics.total_liabilities, documents),
        top_risk_factors=_resolve_metric_list(raw_metrics.top_risk_factors, documents),
        top_growth_drivers=_resolve_metric_list(raw_metrics.top_growth_drivers, documents),
    )
