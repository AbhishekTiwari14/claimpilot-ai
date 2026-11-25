from base64 import b64encode
import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from claimpilot.config import Settings

if TYPE_CHECKING:
    from langchain_core.messages import AIMessage
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
        self.retries = 0


@dataclass(frozen=True)
class ToolTurn:
    message: "AIMessage"
    retries: int


def retry_delay(error: LLMError, retries: int, max_retries: int) -> float | None:
    if not error.retryable or retries >= max_retries:
        return None
    delay = error.retry_after if error.retry_after is not None else 0.25 * 2**retries
    return delay if delay <= MAX_RETRY_DELAY else None


class LLMService:
    """Generate validated structured values with a single configured model."""

    def __init__(
        self,
        settings: Settings,
        *,
        chat_model: "ChatGroq | None" = None,
        vision_model: "ChatGroq | None" = None,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        self._settings = settings
        self._chat_model = chat_model
        self._vision_model = vision_model
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
        images: list[tuple[str, bytes]] | None = None,
    ) -> Output:
        start = monotonic()
        provider = self._settings.claimpilot_llm_provider
        vision = images is not None
        model = self._settings.groq_vision_model if vision else self._settings.groq_model
        if provider == "offline":
            model = "offline-fixture"
        status = "internal_error"
        retries = 0
        usage = {}
        try:
            if images is not None and not 1 <= len(images) <= 3:
                raise LLMError("invalid_image_count")
            if provider == "offline":
                if offline_result is None:
                    raise LLMError("offline_input_unsupported")
                parsed = offline_result()
            else:
                chat_model = self._get_chat_model(vision=vision)
                model = chat_model.model_name
                if images is not None:
                    runnable = chat_model.with_structured_output(schema, method="json_mode", include_raw=True)
                    system_prompt += "\nSchema:\n" + json.dumps(schema.model_json_schema())
                    content = [{"type": "text", "text": user_payload}]
                    for photo_id, data in images:
                        content.extend([
                            {"type": "text", "text": f"Image identifier: {photo_id}"},
                            {"type": "image_url", "image_url": {
                                "url": "data:image/jpeg;base64," + b64encode(data).decode("ascii"),
                            }},
                        ])
                    messages = [("system", system_prompt), ("human", content)]
                else:
                    runnable = chat_model.with_structured_output(
                        schema, method="json_schema", strict=True, include_raw=True
                    )
                    messages = [("system", system_prompt), ("human", user_payload)]
                while True:
                    try:
                        response = self._invoke(runnable, messages)
                        break
                    except LLMError as error:
                        delay = retry_delay(error, retries, self._settings.claimpilot_llm_max_retries)
                        if delay is None:
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
                parsed = raw.content if vision else response["parsed"]

            try:
                if vision and provider == "groq":
                    if not isinstance(parsed, str):
                        raise ValueError("Expected JSON text")
                    result = schema.model_validate_json(parsed)
                else:
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

    def tool_turn(
        self, messages: list, tools: list, *, offline_turn: Callable[[list], "AIMessage"],
        tool_choice: str = "auto",
    ) -> ToolTurn:
        """Invoke one native tool turn, leaving authorization and execution to the caller."""
        start = monotonic()
        provider = self._settings.claimpilot_llm_provider
        model = "offline-fixture" if provider == "offline" else self._settings.groq_model
        retries = 0
        usage = {}
        status = "internal_error"
        try:
            if provider == "offline":
                message = offline_turn(messages)
            else:
                from langchain_core.utils.function_calling import convert_to_openai_tool

                chat = self._get_chat_model(vision=False)
                options = {
                    "parallel_tool_calls": False, "tool_choice": tool_choice,
                    "disable_tool_validation": True, "temperature": 0.2,
                }
                if chat.model_name in ("openai/gpt-oss-20b", "openai/gpt-oss-120b"):
                    options["reasoning_effort"] = "low"
                definitions = [convert_to_openai_tool(tool) for tool in tools]
                for tool, definition in zip(tools, definitions, strict=True):
                    # LangChain drops the closed-object constraint from StructuredTool schemas.
                    if tool.args_schema.model_config.get("extra") == "forbid" and not tool.args_schema.model_fields:
                        definition["function"]["parameters"] = tool.args_schema.model_json_schema()
                runnable = chat.bind_tools(definitions, **options)
                while True:
                    try:
                        message = self._invoke(runnable, messages)
                        break
                    except LLMError as error:
                        if error.code == "invalid_tool_output":
                            error.retryable = True
                        delay = retry_delay(error, retries, self._settings.claimpilot_llm_max_retries)
                        if delay is None:
                            raise
                        self._wait(delay)
                        retries += 1
            usage = message.usage_metadata or {}
            if message.response_metadata.get("finish_reason") not in (None, "stop", "tool_calls"):
                raise LLMError("invalid_output")
            status = "success"
            return ToolTurn(message, retries)
        except LLMError as error:
            status = error.code
            error.retries = retries
            raise
        finally:
            logger.info(
                "llm_execution operation=contractor-tools-v1 provider=%s model=%s status=%s "
                "latency_ms=%s retries=%s input_tokens=%s output_tokens=%s total_tokens=%s",
                provider, model, status, round((monotonic() - start) * 1000, 1), retries,
                usage.get("input_tokens"), usage.get("output_tokens"), usage.get("total_tokens"),
            )

    def _get_chat_model(self, *, vision: bool) -> "ChatGroq":
        cached = self._vision_model if vision else self._chat_model
        if cached is not None:
            return cached
        key = self._settings.groq_api_key
        if key is None or not key.get_secret_value().strip():
            raise LLMError("missing_credentials")
        try:
            from langchain_groq import ChatGroq
        except ModuleNotFoundError:
            raise LLMError("missing_provider_dependencies") from None

        model = ChatGroq(
            model=self._settings.groq_vision_model if vision else self._settings.groq_model,
            api_key=key,
            timeout=self._settings.claimpilot_llm_timeout_seconds,
            max_retries=0,
            max_tokens=2048,
        )
        if vision:
            self._vision_model = model
        else:
            self._chat_model = model
        return model

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
            if isinstance(details, dict) and details.get("code") == "tool_use_failed":
                raise LLMError("invalid_tool_output") from None
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
