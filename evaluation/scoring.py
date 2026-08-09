import json
from datetime import datetime, timezone
from pathlib import Path

from evaluation.judge import get_judge_embeddings, get_judge_llm

_REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def extract_scores(result) -> dict[str, float]:
    """Pull aggregate per-metric scores out of a ragas EvaluationResult.

    ragas' public surface for this has shifted across versions. As of
    ragas 0.4.3, dict(result) raises KeyError (its __iter__/__getitem__
    are row-indexed, not metric-keyed) -- _repr_dict (the aggregate view
    __repr__ itself uses) is the reliable one, so it's tried first, with
    dict(result) kept as a fallback for older versions where the reverse
    was true.
    """
    try:
        return {metric: float(value) for metric, value in result._repr_dict.items()}
    except (AttributeError, TypeError, ValueError):
        return {metric: float(value) for metric, value in dict(result).items()}


def save_report(data: dict, prefix: str = "eval") -> Path:
    """Write a timestamped JSON report to evaluation/reports/."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = _REPORTS_DIR / f"{prefix}_{timestamp}.json"
    report_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return report_path


def score_response(question: str, answer: str, retrieved_contexts: list[str]) -> dict[str, float]:
    """
    Reference-free RAGAS scoring for a single live response.

    Only faithfulness and answer_relevancy are used here (not
    context_precision/context_recall, which need a ground-truth reference
    answer -- unavailable for arbitrary live questions, only for the golden
    dataset in evaluation/run_eval.py).
    """
    from ragas import EvaluationDataset, evaluate
    from ragas.metrics import answer_relevancy, faithfulness

    dataset = EvaluationDataset.from_list([{
        "user_input": question,
        "response": answer,
        "retrieved_contexts": retrieved_contexts,
    }])

    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy],
        llm=get_judge_llm(),
        embeddings=get_judge_embeddings(),
    )
    return extract_scores(result)
