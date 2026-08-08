from pydantic import BaseModel

from llm.azure_openai import get_structured_completion

_SYSTEM_PROMPT = (
    "You are a retrieval-augmentation assistant. You generate short, "
    "plausible-sounding hypothetical passages that are used purely as a "
    "search signal for semantic retrieval — never shown to a user and never "
    "treated as a factual claim. Accuracy is not required; plausible style "
    "and phrasing similar to real annual-report language is what matters."
)


class HypotheticalAnswer(BaseModel):
    text: str


def generate_hypothetical_answer(question: str) -> str:
    """
    Generate a hypothetical answer to embed instead of the raw question (HyDE).

    Embedding a plausible, answer-shaped paragraph often retrieves better than
    embedding a short/vague question, since the source passages that actually
    contain the answer are closer in embedding space to answer-shaped text
    than to question-shaped text. The hypothetical answer is never shown to
    a user or trusted for content — it exists purely to steer the vector
    search leg (see Retriever.invoke's vector_query_text parameter).

    Args:
        question: The original user question or extraction query.

    Returns:
        A short hypothetical answer paragraph (2-4 sentences).
    """
    prompt = f"""Write a short (2-4 sentence) paragraph that could plausibly be
the answer to the question below, written in the style of a company's annual
report (SEC 10-K/10-Q). It does not need to be factually correct for any real
company — it exists only to guide a document search and will never be shown
to a user or treated as a factual statement.

Question: {question}"""

    response = get_structured_completion(
        prompt=prompt,
        response_model=HypotheticalAnswer,
        system_prompt=_SYSTEM_PROMPT,
    )
    return response.text
