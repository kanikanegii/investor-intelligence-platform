import azure.core.exceptions
import openai
from tenacity import retry, retry_if_exception, retry_if_exception_type, stop_after_attempt, wait_exponential

RETRYABLE_EXCEPTIONS = (
    azure.core.exceptions.ServiceRequestError,
    azure.core.exceptions.ServiceResponseError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)

with_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
    reraise=True,
)


def _is_retryable_content_filter_error(exc: BaseException) -> bool:
    """
    True only for Azure OpenAI's jailbreak/prompt-shield false positives, not
    genuine bad requests. Confirmed empirically: identical document content
    (same pdf_hash) triggered `content_filter` on one ingestion and not
    another -- this is Azure's classifier being probabilistic on borderline
    input, not a deterministic property of the prompt, so retrying the exact
    same request has a real chance of succeeding. Other BadRequestErrors
    (e.g. a genuinely malformed request) are not retryable -- retrying those
    would just fail identically every time.
    """
    return (
        isinstance(exc, openai.BadRequestError)
        and getattr(exc, "code", None) == "content_filter"
    )


with_content_filter_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retryable_content_filter_error),
    reraise=True,
)
