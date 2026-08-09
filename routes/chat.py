import logging
import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from auth.entra import require_role
from common.prompt_safety import TRUST_BOUNDARY_INSTRUCTION, neutralize_tag_escapes, wrap_untrusted
from evaluation.scoring import save_report, score_response
from rag.advanced_retrieval import advanced_retrieve_async
from vectorstore.azure_ai_search import AsyncRetriever

logger = logging.getLogger(__name__)

router = APIRouter()

_CHAT_SYSTEM_PROMPT = (
    "You are a financial analyst assistant answering questions about "
    "corporate filings on behalf of an investor-facing product. Answer only "
    "from the provided context. If the context does not contain enough "
    "information to answer, say so plainly rather than guessing or relying "
    "on outside knowledge of the company."
)


class ChatRequest(BaseModel):
    question: str
    company: str | None = None
    year: int | None = None


def _score_and_log_chat_response(question: str, answer: str, retrieved_contexts: list[str]) -> None:
    """
    Reference-free RAGAS scoring (faithfulness, answer_relevancy), run after
    the response has already been sent to the user -- this calls a judge LLM,
    so it must never block the user-facing request. Failures here are logged
    and otherwise ignored; they must never surface to the caller, since by
    the time this runs the response has already gone out.
    """
    try:
        scores = score_response(question, answer, retrieved_contexts)
        report_path = save_report(
            {"question": question, "answer": answer, "scores": scores},
            prefix="chat",
        )
        logger.info("Chat response scored (async): %s -> %s", scores, report_path)
    except Exception:
        logger.exception("Background chat scoring failed (does not affect the user-facing response)")


@router.post("/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    claims: dict = Depends(require_role("Analyst.Read")),
):
    try:
        # Reuse the app-lifetime clients created in main.py's lifespan
        # (persistent connection pools) instead of constructing new ones
        # per request -- see main.py's lifespan docstring for why that
        # matters for latency, not just style.
        state = http_request.app.state
        retriever = AsyncRetriever(state.async_search_client, state.embeddings)

        # Retrieve relevant context via the full advanced retrieval
        # pipeline (multi-query expansion, HyDE, cross-encoder rerank,
        # auto-merge to parent pages, compression) — same pipeline used
        # for KPI extraction, async variant (see advanced_retrieval.py).
        docs = await advanced_retrieve_async(
            retriever=retriever,
            question=request.question,
            company=request.company,
            year=str(request.year) if request.year else None,
            # HyDE dropped for chat latency -- a reasoned judgment call
            # (multi-query expansion already provides retrieval diversity),
            # NOT yet validated against the eval harness -- see
            # EVALUATION_AND_LATENCY.md for the open follow-up to actually
            # A/B this with real faithfulness/relevancy numbers. KPI
            # extraction (kpi_extractor_rag.py) keeps HyDE on -- that path
            # isn't request-latency-sensitive.
            use_hyde=False,
            openai_client=state.async_openai_client,
        )

        context = "\n\n".join(neutralize_tag_escapes(doc.content) for doc in docs)

        user_prompt = (
            f"{TRUST_BOUNDARY_INSTRUCTION}\n\n"
            f"{wrap_untrusted(context)}\n\n"
            f"Question: {request.question}"
        )

        response = await state.async_openai_client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
            messages=[
                {"role": "system", "content": _CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        answer = response.choices[0].message.content

        background_tasks.add_task(
            _score_and_log_chat_response,
            question=request.question,
            answer=answer,
            retrieved_contexts=[doc.content for doc in docs],
        )

        return {"answer": answer}
    except Exception:
        logger.exception("Chat request failed")
        raise HTTPException(status_code=500, detail="Chat request failed. Please try again.")
