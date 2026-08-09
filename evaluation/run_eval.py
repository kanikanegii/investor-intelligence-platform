import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from langchain_openai import AzureOpenAIEmbeddings

from common.logging_config import configure_logging
from vectorstore.azure_ai_search import AzureAISearchVectorStore, Retriever

from evaluation.golden_dataset import load_golden_dataset
from evaluation.ragas_harness import build_ragas_dataset, run_ragas_eval
from evaluation.scoring import extract_scores, save_report

logger = logging.getLogger(__name__)

_THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds.yaml"


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
    scores = extract_scores(result)
    report_path = save_report(scores, prefix="eval")

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
