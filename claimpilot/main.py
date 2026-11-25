from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from claimpilot.config import Settings


class HealthResponse(BaseModel):
    status: Literal["ok"]


app = FastAPI(title="ClaimPilot AI")
app.state.settings = Settings()


@app.get("/health", tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")
