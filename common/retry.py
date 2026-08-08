import azure.core.exceptions
import openai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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
