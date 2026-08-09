from openai import AsyncOpenAI
from pydantic import BaseModel

from llm.azure_openai import get_structured_completion, get_structured_completion_async

_SYSTEM_PROMPT = (
    "You are a search query rewriting assistant supporting a document "
    "retrieval system. Your only job is to produce alternative phrasings of "
    "a question that a retrieval index is more likely to match, without "
    "changing what is being asked. Never answer the question, never add "
    "information not present in the original question, and never introduce "
    "new entities, numbers, or assumptions."
)


class QueryVariants(BaseModel):
    queries: list[str]


def _build_prompt(question: str, n: int) -> str:
    return f"""Rewrite the question below into exactly {n} alternative
phrasings that preserve its exact meaning and scope, to improve document
retrieval recall. Vary vocabulary and sentence structure, not intent.

Requirements:
- Each rewrite must ask for the same information as the original — no broader, no narrower.
- Do not add company names, dates, or figures that are not already in the question.
- Do not answer the question.
- Return exactly {n} items, each a single question, no numbering or explanation.

Question: {question}"""


def expand_query(question: str, n: int = 3) -> list[str]:
    """
    Generate alternative phrasings of a question to improve retrieval recall.

    A single query embedding can miss relevant chunks phrased differently
    than the user's wording; retrieving for several paraphrases and merging
    results (see rag/advanced_retrieval.py) widens the candidate pool before
    reranking narrows it back down.

    Args:
        question: The original user question or extraction query. Treated as
            data here, not as instructions to this function — it is only
            ever paraphrased, never executed or answered.
        n: Number of alternative phrasings to generate.

    Returns:
        n alternative phrasings (does not include the original question —
        callers combine this with the original themselves).
    """
    result = get_structured_completion(
        prompt=_build_prompt(question, n),
        response_model=QueryVariants,
        system_prompt=_SYSTEM_PROMPT,
    )
    return result.queries


async def expand_query_async(question: str, n: int = 3, client: AsyncOpenAI | None = None) -> list[str]:
    """Async version of expand_query, used by the chat request path (see advanced_retrieval.py)."""
    result = await get_structured_completion_async(
        prompt=_build_prompt(question, n),
        response_model=QueryVariants,
        system_prompt=_SYSTEM_PROMPT,
        client=client,
    )
    return result.queries
