from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    claimpilot_llm_provider: Literal["offline", "groq"] = "offline"
    groq_model: str = "openai/gpt-oss-20b"
    groq_api_key: SecretStr | None = None
