from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

from claimpilot.schemas.conversation import Claimant

ModelName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_./-]{0,127}$")]


class ClaimantCredential(Claimant):
    token: SecretStr = Field(min_length=1)


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
    claimpilot_intake_db: Path = Path("data/runtime/intake.sqlite3")
    claimpilot_claimants: list[ClaimantCredential] = Field(default_factory=list)
    claimpilot_photo_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    claimpilot_photo_max_dimension: int = Field(default=6000, ge=1, le=10000)
    claimpilot_photo_max_pixels: int = Field(default=16_000_000, ge=1, le=30_000_000)
    claimpilot_photo_output_edge: int = Field(default=1024, ge=64, le=1536)
    claimpilot_photos_per_claim: int = Field(default=30, ge=1, le=100)
