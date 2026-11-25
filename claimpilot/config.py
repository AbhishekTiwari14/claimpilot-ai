from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from claimpilot.schemas.conversation import Claimant
from claimpilot.schemas.knowledge import Operator

ModelName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_./-]{0,127}$")]


class ClaimantCredential(Claimant):
    token: SecretStr = Field(min_length=1)


class OperatorCredential(Operator):
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
    claimpilot_kb_operators: list[OperatorCredential] = Field(default_factory=list)
    claimpilot_kb_chroma: Path = Path("data/runtime/chroma")
    claimpilot_kb_model_cache: Path = Path("data/runtime/embeddings")
    claimpilot_kb_max_bytes: int = Field(default=5 * 1024 * 1024, ge=256, le=16 * 1024 * 1024)
    claimpilot_kb_chunk_size: int = Field(default=800, ge=300, le=1600)
    claimpilot_kb_chunk_overlap: int = Field(default=150, ge=0, le=500)
    claimpilot_kb_batch_size: int = Field(default=24, ge=1, le=64)
    claimpilot_kb_top_k: int = Field(default=5, ge=1, le=12)
    claimpilot_agent_max_iterations: int = Field(default=10, ge=1, le=16)
    claimpilot_agent_max_tool_calls: int = Field(default=12, ge=1, le=24)
    claimpilot_agent_tool_timeout_seconds: float = Field(default=5, gt=0, le=30)
    claimpilot_agent_deadline_seconds: float = Field(default=100, gt=0, le=300)

    @model_validator(mode="after")
    def distinct_knowledge_credentials(self) -> "Settings":
        operators = [actor.token.get_secret_value() for actor in self.claimpilot_kb_operators]
        claimants = {actor.token.get_secret_value() for actor in self.claimpilot_claimants}
        if len(set(operators)) != len(operators) or claimants.intersection(operators):
            raise ValueError("Knowledge operator credentials must be distinct from all other credentials")
        if self.claimpilot_kb_chunk_overlap >= self.claimpilot_kb_chunk_size:
            raise ValueError("Knowledge chunk overlap must be smaller than chunk size")
        return self
