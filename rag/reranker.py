import logging

from vectorstore.azure_ai_search import RetrievedChunk

logger = logging.getLogger(__name__)

_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # small, fast, CPU-friendly

_model = None  # lazy-loaded singleton, avoids reloading per call


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder
        logger.info("Loading cross-encoder model %s", _MODEL_NAME)
        _model = CrossEncoder(_MODEL_NAME)
    return _model


def rerank(question: str, candidates: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """
    Re-score candidates against the original question using a cross-encoder.

    A cross-encoder jointly attends over (question, passage) pairs and is
    more accurate than bi-encoder cosine similarity, but too slow to run over
    an entire corpus — hence applying it only to the already-narrowed
    candidate pool produced by hybrid+semantic retrieval (and multi-query/
    HyDE expansion), not the full index.

    Args:
        question: The original question (not query variants — reranking
            should judge relevance to what the user actually asked).
        candidates: Deduplicated candidate pool from retrieval.
        top_k: Number of chunks to keep after reranking.

    Returns:
        Up to top_k candidates, most relevant first.
    """
    if not candidates:
        return []

    pairs = [(question, candidate.content) for candidate in candidates]
    scores = _get_model().predict(pairs)

    ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
    return [chunk for chunk, _ in ranked[:top_k]]
