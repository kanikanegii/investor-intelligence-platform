"""
End-to-end diagnostic runner.

Exercises every stage of the real pipeline against your actual Azure/Postgres
backend (no mocks) and prints a per-stage status line as it goes, followed by
a pass/fail summary. Intended for verifying "is everything actually wired up
correctly" after a change — not a substitute for evaluation/run_eval.py
(which measures retrieval *quality*, not plumbing correctness).

Usage:
    python -m debug.e2e_check [--pdf path/to/file.pdf] [--question "..."]

If --pdf is omitted, the first PDF found in data/raw_pdfs is used.
"""

import argparse
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import AzureOpenAIEmbeddings

from common.logging_config import configure_logging
from database.create_table import create_tables
from database.ingestion_log import get_ingestion_record
from database.metrics import get_metrics
from database.postgres_sql import create_database
from database.save_metrics import save_metrics
from ingestion.ingest_documents import parse_company_year
from ingestion.pdf_to_markdown import PDFToMarkdownConverter
from ingestion.semantic_chunker import chunk_pages
from llm.azure_openai import get_openai_client
from rag.context_compression import compress_chunk, merge_to_parent_pages
from rag.hyde import generate_hypothetical_answer
from rag.kpi_extractor_rag import extract_financial_metrics
from rag.query_expansion import expand_query
from rag.reranker import rerank
from vectorstore.azure_ai_search import AzureAISearchVectorStore, Retriever
from vectorstore.create_index import create_index

logger = logging.getLogger(__name__)

_REQUIRED_ENV_VARS = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_CHAT_DEPLOYMENT",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_API_KEY",
    "AZURE_SEARCH_INDEX_NAME",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DATABASE",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
]

_DEFAULT_PDF_DIR = Path(__file__).resolve().parents[1] / "data" / "raw_pdfs"


@dataclass
class StageResult:
    name: str
    passed: bool
    detail: str
    seconds: float


@dataclass
class Report:
    results: list[StageResult] = field(default_factory=list)


def run_stage(report: Report, name: str, fn) -> object | None:
    """Run one diagnostic stage, print its status line, record the result.

    A failure here is caught and reported rather than raised, so one broken
    stage doesn't prevent the rest of the pipeline from being checked.
    """
    start = time.monotonic()
    try:
        result = fn()
        elapsed = time.monotonic() - start
        report.results.append(StageResult(name, True, "ok", elapsed))
        print(f"[PASS] {name} ({elapsed:.2f}s)")
        return result
    except Exception as exc:
        elapsed = time.monotonic() - start
        report.results.append(StageResult(name, False, f"{type(exc).__name__}: {exc}", elapsed))
        print(f"[FAIL] {name} ({elapsed:.2f}s): {type(exc).__name__}: {exc}")
        return None


def check_environment(report: Report) -> None:
    print("\n=== 1. Environment variables ===")
    missing = [name for name in _REQUIRED_ENV_VARS if not os.getenv(name)]
    for name in _REQUIRED_ENV_VARS:
        status = "PASS" if os.getenv(name) else "FAIL"
        print(f"[{status}] {name}")
    report.results.append(
        StageResult("environment variables", not missing, f"missing: {missing}" if missing else "ok", 0.0)
    )


def check_infra(report: Report) -> None:
    print("\n=== 2. Infrastructure (Postgres + Azure AI Search) ===")
    run_stage(report, "create_database", create_database)
    run_stage(report, "create_tables", create_tables)
    run_stage(
        report,
        "create_index",
        lambda: create_index(
            endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
            api_key=os.environ["AZURE_SEARCH_API_KEY"],
            index_name=os.environ["AZURE_SEARCH_INDEX_NAME"],
        ),
    )


def check_ingestion(report: Report, pdf_path: Path, embeddings, vector_store) -> tuple[str, str] | None:
    print(f"\n=== 3. Ingestion pipeline ({pdf_path.name}) ===")
    company, year = parse_company_year(pdf_path)
    print(f"    parsed company={company!r} year={year!r}")

    converter = PDFToMarkdownConverter()
    pages = run_stage(report, "convert_pdf_pages", lambda: converter.convert_pdf_pages(str(pdf_path)))
    if not pages:
        return None
    print(f"    {len(pages)} page(s) converted")

    chunks = run_stage(
        report,
        "chunk_pages",
        lambda: chunk_pages(pages=pages, embeddings=embeddings, source_file=pdf_path.name, company=company, year=year),
    )
    if not chunks:
        return None
    print(f"    {len(chunks)} chunk(s) generated (sample chunk_id={chunks[0].metadata.chunk_id})")

    uploaded = run_stage(report, "upload_chunks", lambda: vector_store.upload_chunks(chunks=chunks, embeddings=embeddings))
    if uploaded is not None:
        print(f"    {uploaded} chunk(s) uploaded")

    record = run_stage(report, "get_ingestion_record", lambda: get_ingestion_record(pdf_path.name))
    if record:
        print(f"    ingestion log: status={record.get('status')}")

    return company, year


def check_retrieval_stages(report: Report, retriever: Retriever, question: str, company: str, year: str) -> None:
    print(f"\n=== 4. Retrieval pipeline stage-by-stage (question: {question!r}) ===")

    queries = run_stage(report, "expand_query", lambda: [question] + expand_query(question))
    if queries:
        print(f"    {len(queries)} query variant(s): {queries}")
    else:
        queries = [question]

    hyde_text = run_stage(report, "generate_hypothetical_answer (HyDE)", lambda: generate_hypothetical_answer(question))
    if hyde_text:
        print(f"    hypothetical answer preview: {hyde_text[:120]!r}")

    def _retrieve_all():
        candidates = {}
        for variant in queries:
            for chunk in retriever.invoke(query=variant, company=company, year=year, top_k=10, vector_query_text=hyde_text):
                existing = candidates.get(chunk.chunk_id)
                if existing is None or chunk.score > existing.score:
                    candidates[chunk.chunk_id] = chunk
        return list(candidates.values())

    pool = run_stage(report, "retriever.invoke (per variant, deduped)", _retrieve_all)
    if not pool:
        return
    print(f"    {len(pool)} unique candidate chunk(s) after dedup")

    reranked = run_stage(report, "rerank (cross-encoder)", lambda: rerank(question, pool, top_k=5))
    if reranked is None:
        return
    print(f"    {len(reranked)} chunk(s) after reranking, top score chunk_id={reranked[0].chunk_id if reranked else None}")

    merged = run_stage(report, "merge_to_parent_pages (auto-merge)", lambda: merge_to_parent_pages(reranked))
    if merged is None:
        return
    print(f"    {len(merged)} chunk(s) after auto-merging to parent pages")

    def _compress_all():
        return [compress_chunk(question, chunk) for chunk in merged]

    compressed = run_stage(report, "compress_chunk (context compression)", _compress_all)
    if compressed:
        for original, result in zip(merged, compressed):
            print(f"    chunk {result.chunk_id}: {len(original.content)} chars -> {len(result.content)} chars after compression")


def check_extraction(report: Report, retriever: Retriever, company: str, year: str) -> object | None:
    print(f"\n=== 5. Full KPI extraction ({company} {year}) ===")
    metrics = run_stage(
        report,
        "extract_financial_metrics",
        lambda: extract_financial_metrics(retriever=retriever, company=company, year=int(year) if year.isdigit() else None),
    )
    if metrics is None:
        return None

    flat = metrics.to_flat_dict()
    citation_map = metrics.citation_map()
    for field_name, value in flat.items():
        n_citations = len(citation_map.get(field_name, []))
        print(f"    {field_name}: {value!r} ({n_citations} citation(s))")

    return metrics


def check_persistence(report: Report, company: str, year: str, metrics) -> None:
    print(f"\n=== 6. Persistence round-trip ===")
    if metrics is None:
        print("    skipped (no metrics to save)")
        return

    run_stage(report, "save_metrics", lambda: save_metrics(company=company, year=int(year) if year.isdigit() else None, metrics=metrics))

    def _check_saved():
        rows = get_metrics()
        matching = [r for r in rows if r.get("company") == company and str(r.get("year")) == str(year)]
        if not matching:
            raise RuntimeError(f"No row found for {company} {year} after save_metrics")
        return matching[0]

    row = run_stage(report, "get_metrics (round-trip)", _check_saved)
    if row:
        print(f"    round-trip OK: revenue={row.get('revenue')!r}")


def check_chat(report: Report, retriever: Retriever, question: str) -> None:
    print(f"\n=== 7. Chat-style question answering ===")
    from rag.advanced_retrieval import advanced_retrieve
    from common.prompt_safety import TRUST_BOUNDARY_INSTRUCTION, neutralize_tag_escapes, wrap_untrusted

    def _chat():
        docs = advanced_retrieve(retriever=retriever, question=question)
        context = "\n\n".join(neutralize_tag_escapes(doc.content) for doc in docs)
        user_prompt = f"{TRUST_BOUNDARY_INSTRUCTION}\n\n{wrap_untrusted(context)}\n\nQuestion: {question}"
        client = get_openai_client()
        response = client.chat.completions.create(
            model=os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"],
            messages=[
                {"role": "system", "content": "You are a financial analyst assistant. Answer only from context."},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content

    answer = run_stage(report, "chat completion", _chat)
    if answer:
        print(f"    answer preview: {answer[:200]!r}")


def check_auth_enforcement(report: Report, token: str | None) -> None:
    """
    Exercise /upload and /chat through the real FastAPI app (not internal
    function calls, unlike every other stage here) to confirm the
    require_role() dependency is actually wired on both routes.

    Always checks the reject paths (no token, garbage token). If --token is
    given (a real Analyst.Read-bearing bearer token), also confirms it's
    accepted rather than rejected.
    """
    print("\n=== 8. Auth enforcement (/api/upload, /api/chat) ===")
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)

    def _expect_status(response, expected: int) -> int:
        if response.status_code != expected:
            raise RuntimeError(f"expected {expected}, got {response.status_code}: {response.text[:200]}")
        return response.status_code

    run_stage(
        report,
        "upload rejects missing token (401)",
        lambda: _expect_status(
            client.post("/api/upload", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")}), 401
        ),
    )
    run_stage(
        report,
        "chat rejects missing token (401)",
        lambda: _expect_status(client.post("/api/chat", json={"question": "test"}), 401),
    )
    run_stage(
        report,
        "chat rejects malformed token (401)",
        lambda: _expect_status(
            client.post("/api/chat", headers={"Authorization": "Bearer not-a-real-token"}, json={"question": "test"}), 401
        ),
    )

    if not token:
        print("    [SKIP] chat accepts valid token — no --token given")
        return

    def _chat_with_valid_token():
        response = client.post(
            "/api/chat", headers={"Authorization": f"Bearer {token}"}, json={"question": "What was revenue?"}
        )
        if response.status_code in (401, 403):
            raise RuntimeError(f"valid token was rejected: {response.status_code}: {response.text[:200]}")
        return response.status_code

    run_stage(report, "chat accepts valid Analyst.Read token", _chat_with_valid_token)


def print_summary(report: Report) -> bool:
    print("\n=== Summary ===")
    all_passed = True
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        if not result.passed:
            all_passed = False
        print(f"[{status}] {result.name} ({result.seconds:.2f}s) — {result.detail}")

    total = len(report.results)
    passed = sum(1 for r in report.results if r.passed)
    print(f"\n{passed}/{total} stages passed.")
    return all_passed


def main() -> None:
    configure_logging()
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", default=None, help="PDF to ingest (default: first found in data/raw_pdfs)")
    parser.add_argument("--question", default="What was the company's total revenue?", help="Question for retrieval/chat checks")
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token with Analyst.Read (see auth/entra.py) to also verify the valid-token path on /chat",
    )
    parser.add_argument(
        "--skip-ingestion",
        action="store_true",
        help=(
            "Skip re-chunking/re-embedding/re-uploading the PDF (stage 3) and go straight to "
            "retrieval/extraction/persistence/chat, assuming it's already indexed from a prior "
            "run. Avoids repeated embedding API calls while iterating on later stages."
        ),
    )
    args = parser.parse_args()

    report = Report()

    check_environment(report)
    check_infra(report)

    pdf_path = Path(args.pdf) if args.pdf else next(iter(_DEFAULT_PDF_DIR.glob("*.pdf")), None)
    if pdf_path is None:
        print(f"\nNo PDF found in {_DEFAULT_PDF_DIR} and none given via --pdf — skipping ingestion/retrieval/extraction/chat checks.")
        check_auth_enforcement(report, args.token)
        all_passed = print_summary(report)
        raise SystemExit(0 if all_passed else 1)

    embeddings = AzureOpenAIEmbeddings(
        model=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
    )
    vector_store = AzureAISearchVectorStore(
        endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
        api_key=os.getenv("AZURE_SEARCH_API_KEY"),
        index_name=os.getenv("AZURE_SEARCH_INDEX_NAME"),
    )
    retriever = Retriever(vector_store.client, embeddings)

    if args.skip_ingestion:
        print(f"\n=== 3. Ingestion pipeline (SKIPPED via --skip-ingestion) ===")
        company, year = parse_company_year(pdf_path)
        print(f"    parsed company={company!r} year={year!r} (assuming already indexed)")
    else:
        company_year = check_ingestion(report, pdf_path, embeddings, vector_store)
        if company_year is None:
            print_summary(report)
            return
        company, year = company_year

    check_retrieval_stages(report, retriever, args.question, company, year)
    metrics = check_extraction(report, retriever, company, year)
    check_persistence(report, company, year, metrics)
    check_chat(report, retriever, args.question)
    check_auth_enforcement(report, args.token)

    all_passed = print_summary(report)
    raise SystemExit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
