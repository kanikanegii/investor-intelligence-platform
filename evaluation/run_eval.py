import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from langchain_openai import AzureOpenAIEmbeddings

from common.logging_config import configure_logging
from vectorstore.azure_ai_search import AzureAISearchVectorStore, Retriever

from evaluation.golden_dataset import load_golden_dataset
from evaluation.ragas_harness import build_ragas_dataset, run_ragas_eval

logger = logging.getLogger(__name__)

_THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds.yaml"
_REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _load_thresholds() -> dict[str, float]:
    return yaml.safe_load(_THRESHOLDS_PATH.read_text(encoding="utf-8"))


def _passes_thresholds(scores: dict[str, float], thresholds: dict[str, float]) -> bool:
    passed = True
    for metric, minimum in thresholds.items():
        actual = scores.get(metric)
        if actual is None:
            logger.warning("Metric %s not present in results, skipping threshold check", metric)
            continue
        if actual < minimum:
            logger.error("Metric %s = %.3f is below threshold %.3f", metric, actual, minimum)
            passed = False
        else:
            logger.info("Metric %s = %.3f (threshold %.3f) OK", metric, actual, minimum)
    return passed


def _extract_scores(result) -> dict[str, float]:
    """Pull aggregate per-metric scores out of a ragas EvaluationResult.

    ragas' public surface for this has shifted across versions; try the
    documented dict-like access first and fall back to the private
    _repr_dict attribute (used by __repr__) if that's unavailable.
    """
    try:
        return {metric: float(value) for metric, value in dict(result).items()}
    except (TypeError, ValueError):
        return {metric: float(value) for metric, value in result._repr_dict.items()}


def _save_report(scores: dict[str, float]) -> Path:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = _REPORTS_DIR / f"eval_{timestamp}.json"
    report_path.write_text(json.dumps(scores, indent=2), encoding="utf-8")
    return report_path


def main() -> None:
    configure_logging()
    load_dotenv()

    examples = load_golden_dataset()
    logger.info("Loaded %d golden examples", len(examples))

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

    dataset = build_ragas_dataset(examples, retriever)
    result = run_ragas_eval(dataset)
    scores = _extract_scores(result)
    report_path = _save_report(scores)

    print(result)
    logger.info("Report saved to %s", report_path)

    thresholds = _load_thresholds()
    if not _passes_thresholds(scores, thresholds):
        logger.error("One or more metrics fell below threshold — failing.")
        sys.exit(1)

    logger.info("All metrics passed thresholds.")
    sys.exit(0)


if __name__ == "__main__":
    main()
