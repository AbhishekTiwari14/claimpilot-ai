import logging
import math
from collections.abc import Callable
from time import monotonic, sleep
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from claimpilot.config import Settings

if TYPE_CHECKING:
    from langchain_groq import ChatGroq

logger = logging.getLogger(__name__)
Output = TypeVar("Output", bound=BaseModel)
MAX_RETRY_DELAY = 5.0


class LLMError(RuntimeError):
    def __init__(
        self, code: str, *, retryable: bool = False, retry_after: float | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


class LLMService:
    """Generate validated structured values with a single configured model."""

    def __init__(
        self,
        settings: Settings,
        *,
        chat_model: "ChatGroq | None" = None,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        self._settings = settings
        self._chat_model = chat_model
        self._wait = wait

    def structured(
        self,
        schema: type[Output],
        *,
        system_prompt: str,
        user_payload: str,
        operation: str,
        offline_result: Callable[[], dict] | None = None,
        validate: Callable[[Output], None] | None = None,
    ) -> Output:
        start = monotonic()
        provider = self._settings.claimpilot_llm_provider
        model = self._settings.groq_model if provider == "groq" else "offline-fixture"
        status = "internal_error"
        retries = 0
        usage = {}
        try:
            if provider == "offline":
                if offline_result is None:
                    raise LLMError("offline_input_unsupported")
                parsed = offline_result()
            else:
                chat_model = self._get_chat_model()
                model = chat_model.model_name
                runnable = chat_model.with_structured_output(
                    schema, method="json_schema", strict=True, include_raw=True
                )
                messages = [("system", system_prompt), ("human", user_payload)]
                while True:
                    try:
                        response = self._invoke(runnable, messages)
                        break
                    except LLMError as error:
                        if not error.retryable or retries >= self._settings.claimpilot_llm_max_retries:
                            raise
                        delay = error.retry_after
                        if delay is None:
                            delay = 0.25 * 2**retries
                        if delay > MAX_RETRY_DELAY:
                            raise
                        self._wait(delay)
                        retries += 1

                raw = response["raw"]
                usage = raw.usage_metadata or {}
                if (
                    response["parsing_error"] is not None
                    or raw.response_metadata.get("finish_reason") not in (None, "stop")
                ):
                    raise LLMError("invalid_output")
                parsed = response["parsed"]

            try:
                result = schema.model_validate(parsed)
                if validate is not None:
                    validate(result)
            except ValueError:
                raise LLMError("invalid_output") from None

            status = "success"
            return result
        except LLMError as error:
            status = error.code
            raise
        finally:
            latency_ms = round((monotonic() - start) * 1000, 1)
            logger.info(
                "llm_execution operation=%s provider=%s model=%s status=%s latency_ms=%s "
                "retries=%s input_tokens=%s output_tokens=%s total_tokens=%s",
                operation,
                provider,
                model,
                status,
                latency_ms,
                retries,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("total_tokens"),
            )

    def _get_chat_model(self) -> "ChatGroq":
        if self._chat_model is not None:
            return self._chat_model
        key = self._settings.groq_api_key
        if key is None or not key.get_secret_value().strip():
            raise LLMError("missing_credentials")
        try:
            from langchain_groq import ChatGroq
        except ModuleNotFoundError:
            raise LLMError("missing_provider_dependencies") from None

        self._chat_model = ChatGroq(
            model=self._settings.groq_model,
            api_key=key,
            timeout=self._settings.claimpilot_llm_timeout_seconds,
            max_retries=0,
            max_tokens=2048,
        )
        return self._chat_model

    @staticmethod
    def _invoke(runnable, messages):
        from groq import (
            APIConnectionError,
            APIResponseValidationError,
            APIStatusError,
            APITimeoutError,
            GroqError,
        )
        from langsmith import tracing_context

        # The SDK's HTTP debug records can contain the full request body.
        logging.getLogger("groq._base_client").setLevel(logging.WARNING)
        try:
            with tracing_context(enabled=False):
                return runnable.invoke(messages)
        except APITimeoutError:
            raise LLMError("timeout", retryable=True) from None
        except APIConnectionError:
            raise LLMError("unavailable", retryable=True) from None
        except APIResponseValidationError:
            raise LLMError("invalid_output") from None
        except APIStatusError as error:
            if error.status_code == 429:
                retry_after = _retry_after_seconds(error.response.headers.get("retry-after"))
                raise LLMError("rate_limited", retryable=True, retry_after=retry_after) from None
            if error.status_code in (401, 403):
                raise LLMError("authentication") from None
            if error.status_code == 408:
                raise LLMError("timeout", retryable=True) from None
            if error.status_code >= 500:
                raise LLMError("unavailable", retryable=True) from None
            details = error.body.get("error") if isinstance(error.body, dict) else None
            if isinstance(details, dict) and details.get("code") == "json_validate_failed":
                raise LLMError("invalid_output") from None
            raise LLMError("provider_error") from None
        except GroqError:
            raise LLMError("provider_error") from None


def _retry_after_seconds(header: str | None) -> float | None:
    if header is None:
        return None
    try:
        seconds = float(header)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None
