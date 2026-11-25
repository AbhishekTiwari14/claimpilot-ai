from typing import Annotated, Literal

from pydantic import Field, SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

ModelName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_./-]{0,127}$")]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    claimpilot_llm_provider: Literal["offline", "groq"] = "offline"
    groq_model: ModelName = "openai/gpt-oss-20b"
    groq_vision_model: ModelName = "qwen/qwen3.8-27b"
    groq_api_key: SecretStr | None = None
    claimpilot_llm_timeout_seconds: float = Field(default=12, gt=0, le=120)
    claimpilot_llm_max_retries: int = Field(default=1, ge=0, le=3)
