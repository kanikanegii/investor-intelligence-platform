import os


def _base_url() -> str:
    """Azure AI Foundry's unified OpenAI-compatible v1 endpoint.

    Same pattern as llm/azure_openai.py::get_openai_client — this endpoint
    shape doesn't take a date-versioned `api-version` query param the way
    the older Azure-specific SDKs/langchain classes require, so plain
    OpenAI-compatible clients (ChatOpenAI/OpenAIEmbeddings) work here
    without AZURE_OPENAI_API_VERSION.
    """
    return os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/") + "/openai/v1/"


def get_judge_llm():
    """
    Return a RAGAS-compatible LLM wrapper for scoring evaluation metrics.

    Deliberately points at a deployment separate from AZURE_OPENAI_CHAT_DEPLOYMENT
    (the extraction model) via AZURE_OPENAI_JUDGE_DEPLOYMENT, to avoid
    self-preference bias: an LLM tends to rate its own outputs more favorably
    than an independent judge would.
    """
    from ragas.llms import LangchainLLMWrapper
    from langchain_openai import ChatOpenAI

    judge = ChatOpenAI(
        base_url=_base_url(),
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        model=os.environ["AZURE_OPENAI_JUDGE_DEPLOYMENT"],
    )
    return LangchainLLMWrapper(judge)


def get_judge_embeddings():
    """Return a RAGAS-compatible embeddings wrapper, used by metrics that need
    semantic similarity (e.g. answer_relevancy)."""
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from langchain_openai import OpenAIEmbeddings

    embeddings = OpenAIEmbeddings(
        base_url=_base_url(),
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        model=os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"],
    )
    return LangchainEmbeddingsWrapper(embeddings)
