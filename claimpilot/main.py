from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from claimpilot.api.intake import router
from claimpilot.config import Settings
from claimpilot.services.llm import LLMService
from claimpilot.storage.intake import IntakeError, IntakeStore


class HealthResponse(BaseModel):
    status: Literal["ok"]


def create_app(settings: Settings | None = None, *, llm: LLMService | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    store = IntakeStore(settings.claimpilot_intake_db)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        store.initialize()
        yield

    app = FastAPI(title="ClaimPilot AI", lifespan=lifespan)
    app.state.settings = settings
    app.state.intake_store = store
    app.state.llm = llm if llm is not None else LLMService(settings)
    app.include_router(router)

    @app.exception_handler(IntakeError)
    async def intake_error(_: Request, error: IntakeError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else None
        return JSONResponse(
            status_code=error.status, headers=headers,
            content={"code": error.code, "message": str(error), "request_id": str(uuid4())},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "code": "invalid_request", "message": "The request did not match the API contract.",
            "request_id": str(uuid4()),
        })

    @app.get("/health", tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return app


app = create_app()
