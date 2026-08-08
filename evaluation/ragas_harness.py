import logging

from rag.kpi_extractor_rag import FinancialMetrics, extract_financial_metrics, retrieve_context
from vectorstore.azure_ai_search import Retriever

from evaluation.golden_dataset import GoldenExample
from evaluation.judge import get_judge_embeddings, get_judge_llm

logger = logging.getLogger(__name__)


def _render_answer(metrics: FinancialMetrics) -> str:
    """Flatten extracted metrics into a prose answer string for RAGAS scoring."""
    flat = metrics.to_flat_dict()
    lines = [f"{key.replace('_', ' ')}: {value}" for key, value in flat.items() if value]
    return "\n".join(lines) if lines else "No information found."


def build_ragas_dataset(examples: list[GoldenExample], retriever: Retriever):
    """
    Run the real production retrieval + extraction path for each golden
    example and assemble a RAGAS EvaluationDataset.

    Exercises the actual retrieve_context/extract_financial_metrics functions
    (not a mock), so the eval reflects real production behavior.

    Args:
        examples: Golden Q&A examples to evaluate against.
        retriever: Hybrid retriever pointed at the live Azure AI Search index.

    Returns:
        A ragas.EvaluationDataset ready for evaluate().
    """
    from ragas import EvaluationDataset

    rows = []
    for example in examples:
        context, documents = retrieve_context(retriever, example.company, example.year)
        metrics = extract_financial_metrics(retriever, example.company, example.year)
        answer = _render_answer(metrics)

        rows.append(
            {
                "user_input": example.question,
                "response": answer,
                "retrieved_contexts": [doc.content for doc in documents],
                "reference": example.ground_truth,
            }
        )
        logger.info("Built eval row for: %s", example.question)

    return EvaluationDataset.from_list(rows)


def run_ragas_eval(dataset):
    """
    Run RAGAS metrics against a built dataset using the configured judge model.

    Args:
        dataset: A ragas.EvaluationDataset (see build_ragas_dataset).

    Returns:
        The ragas EvaluationResult.
    """
    from ragas import evaluate
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    return evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=get_judge_llm(),
        embeddings=get_judge_embeddings(),
    )
