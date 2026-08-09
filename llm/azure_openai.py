import logging
import os
import re

import openai
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel
from dotenv import load_dotenv

from common.retry import with_content_filter_retry

load_dotenv()

logger = logging.getLogger(__name__)


def get_openai_client() -> OpenAI:
    """
    Create a Microsoft Foundry OpenAI-compatible client.

    Returns:
        Configured OpenAI client.
    """
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]

    return OpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=endpoint.rstrip("/") + "/openai/v1/",
    )


def get_async_openai_client() -> AsyncOpenAI:
    """
    Async counterpart of get_openai_client -- used by the chat request path
    (routes/chat.py and everything rag/advanced_retrieval.py calls), so a
    blocking network call never ties up the FastAPI event loop for the
    duration of a request. Ingestion/evaluation keep using the sync client
    above: they don't run inline in a request/response cycle (ingestion runs
    via a FastAPI BackgroundTask, already offloaded to a thread pool by
    Starlette; evaluation is a standalone CLI script), so making them async
    would add complexity without fixing anything real for those paths.
    """
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]

    return AsyncOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=endpoint.rstrip("/") + "/openai/v1/",
    )

def _build_messages(prompt: str, system_prompt: str) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]


def _parse_fallback_text(text: str, response_model: type[BaseModel]) -> BaseModel:
    """
    Parse a plain-text chat completion into `response_model`, for deployments
    that rejected structured-output mode. Pure string/pydantic logic, no I/O
    -- shared verbatim between the sync and async completion paths.
    """
    logger.debug("Fallback raw text response: %s", text)

    # Try to extract the first JSON object from the model output
    match = re.search(r"\{.*\}", text, re.S)
    json_text = match.group(0) if match else text

    try:
        # Handle explicit 'null' responses: return an empty model instance
        if isinstance(json_text, str) and json_text.strip() in ("null", "None", ""):
            # Prefer pydantic v2 `model_construct` (no validation) to build an empty model
            if hasattr(response_model, "model_construct"):
                return response_model.model_construct()
            # Fallback: attempt to validate empty dict (may fail if required fields exist)
            try:
                return response_model.model_validate({}) if hasattr(response_model, "model_validate") else response_model.parse_obj({})
            except Exception:
                raise RuntimeError("Model returned null and cannot construct an empty instance; please check the model schema or use a deployment that supports structured outputs.")

        # pydantic v2: model_validate_json; v1 fallback to parse_raw
        if hasattr(response_model, "model_validate_json"):
            return response_model.model_validate_json(json_text)
        return response_model.parse_raw(json_text)
    except Exception as e:
        raise RuntimeError(f"Failed to parse JSON fallback response: {e}\nRaw output:\n{text}") from e


def _is_structured_output_unsupported(exc: openai.BadRequestError) -> bool:
    msg = str(exc)
    return "response_format" in msg or "json_schema" in msg or "Structured Outputs" in msg


@with_content_filter_retry
def get_structured_completion(
    prompt: str,
    response_model: type[BaseModel],
    system_prompt: str,
    model: str | None = None
) -> BaseModel:
    """
    Generate structured output.

    Args:
        prompt: Input prompt.
        response_model: Pydantic response model.
        system_prompt: System role instructions for this specific call. This
            is caller-supplied rather than hardcoded here, since callers use
            this function for unrelated tasks (KPI extraction, query
            rewriting, HyDE, context compression) that each need their own
            framing — a single fixed persona would be wrong for most of them.
        model: Azure OpenAI deployment name.

    Returns:
        Parsed response model.
    """
    # Read deployment name from environment when not provided; do not fallback to a hardcoded name
    model = model or os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")

    client = get_openai_client()
    messages = _build_messages(prompt, system_prompt)

    # Prefer structured parsing when supported by the deployment
    try:
        response = client.responses.parse(
            model=model,
            input=messages,
            text_format=response_model,
        )
        logger.debug("Structured parsed output: %s", response.output_parsed)

        return response.output_parsed

    except openai.BadRequestError as exc:
        # Fallback: some Azure deployments/models don't support structured outputs.
        # Request a JSON-only response and parse it with pydantic.
        if _is_structured_output_unsupported(exc):
            fallback = client.chat.completions.create(model=model, messages=messages)
            return _parse_fallback_text(fallback.choices[0].message.content, response_model)

        # Re-raise if it's a different bad request
        raise


@with_content_filter_retry
async def get_structured_completion_async(
    prompt: str,
    response_model: type[BaseModel],
    system_prompt: str,
    model: str | None = None,
    client: AsyncOpenAI | None = None,
) -> BaseModel:
    """
    Async counterpart of get_structured_completion -- identical behavior,
    used by the chat request path so this LLM call never blocks the FastAPI
    event loop. See get_async_openai_client for why only this path needs it.

    Args:
        client: Reuse an existing AsyncOpenAI client (the app-lifetime one
            from main.py's lifespan, passed down through
            rag/advanced_retrieval.py) rather than opening a new connection
            pool per call. Falls back to constructing one only if a caller
            genuinely has no shared client available -- every call on the
            chat request path should be passing one.
    """
    model = model or os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")

    client = client or get_async_openai_client()
    messages = _build_messages(prompt, system_prompt)

    try:
        response = await client.responses.parse(
            model=model,
            input=messages,
            text_format=response_model,
        )
        logger.debug("Structured parsed output: %s", response.output_parsed)

        return response.output_parsed

    except openai.BadRequestError as exc:
        if _is_structured_output_unsupported(exc):
            fallback = await client.chat.completions.create(model=model, messages=messages)
            return _parse_fallback_text(fallback.choices[0].message.content, response_model)

        raise
