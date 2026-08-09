import logging
from concurrent.futures import ThreadPoolExecutor

from rag.context_compression import compress_chunk, merge_to_parent_pages
from rag.hyde import generate_hypothetical_answer
from rag.query_expansion import expand_query
from rag.reranker import rerank
from vectorstore.azure_ai_search import RetrievedChunk, Retriever

logger = logging.getLogger(__name__)


def advanced_retrieve(
    retriever: Retriever,
    question: str,
    company: str | None = None,
    year: str | None = None,
    top_k: int = 8,
    candidate_k: int = 10,
    use_query_expansion: bool = True,
    use_hyde: bool = True,
    use_reranking: bool = True,
    use_auto_merge: bool = True,
    use_compression: bool = True,
) -> list[RetrievedChunk]:
    """
    Full advanced retrieval pipeline.

    Stages: expand the question into several phrasings (multi-query) ->
    retrieve per phrasing via hybrid + semantic search, embedding a HyDE
    hypothetical answer instead of the raw question for the vector leg ->
    dedupe the merged candidate pool -> cross-encoder rerank against the
    original question -> auto-merge to full parent pages -> LLM-compress
    each final chunk down to the sentences relevant to the question.

    Each stage after initial retrieval is independently toggleable, so the
    RAGAS harness (evaluation/ragas_harness.py) can A/B each technique's
    contribution to retrieval quality.

    Args:
        retriever: Hybrid vector+keyword+semantic retriever.
        question: The user question or extraction query.
        company: Optional company filter.
        year: Optional fiscal year filter.
        top_k: Final number of chunks returned.
        candidate_k: Chunks retrieved per query variant, before merge/rerank.
        use_query_expansion: Retrieve for LLM-generated paraphrases too.
        use_hyde: Embed a hypothetical answer instead of the raw question.
        use_reranking: Apply cross-encoder reranking to the candidate pool.
        use_auto_merge: Expand top chunks to their full parent page.
        use_compression: LLM-compress each final chunk to relevant sentences.

    Returns:
        Up to top_k chunks, ready to be formatted into citation-numbered
        context blocks (see rag/kpi_extractor_rag.py::retrieve_context).
    """
    # Query expansion and HyDE are both independent LLM calls that only
    # depend on the original question -- run them concurrently rather than
    # paying their latency back to back.
    with ThreadPoolExecutor(max_workers=2) as executor:
        expansion_future = executor.submit(expand_query, question) if use_query_expansion else None
        hyde_future = executor.submit(generate_hypothetical_answer, question) if use_hyde else None

        queries = [question]
        if expansion_future is not None:
            queries += expansion_future.result()
            logger.info("Expanded query into %d variants", len(queries))

        vector_query_text = hyde_future.result() if hyde_future is not None else None

    # Each query variant's retrieval is an independent network call (embed +
    # search) -- run them concurrently instead of one at a time.
    with ThreadPoolExecutor(max_workers=len(queries)) as executor:
        results = executor.map(
            lambda variant: retriever.invoke(
                query=variant,
                company=company,
                year=year,
                top_k=candidate_k,
                vector_query_text=vector_query_text,
            ),
            queries,
        )

        candidates: dict[str, RetrievedChunk] = {}
        for chunks in results:
            for chunk in chunks:
                existing = candidates.get(chunk.chunk_id)
                if existing is None or chunk.score > existing.score:
                    candidates[chunk.chunk_id] = chunk

    pool = list(candidates.values())
    logger.info("Deduplicated candidate pool: %d chunks", len(pool))

    if use_reranking:
        pool = rerank(question, pool, top_k=top_k)
    else:
        pool = sorted(pool, key=lambda c: c.score, reverse=True)[:top_k]

    if use_auto_merge:
        pool = merge_to_parent_pages(pool)
        logger.info("After auto-merge to parent pages: %d chunks", len(pool))

    if use_compression and pool:
        # Each chunk's compression is an independent LLM call -- run them
        # concurrently rather than one at a time (this was the single
        # largest sequential cost in the whole pipeline: up to top_k
        # separate round trips, back to back).
        with ThreadPoolExecutor(max_workers=len(pool)) as executor:
            pool = list(executor.map(lambda chunk: compress_chunk(question, chunk), pool))

    return pool
