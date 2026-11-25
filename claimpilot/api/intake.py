from hmac import compare_digest
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from claimpilot.agents.intake import extract_claim
from claimpilot.schemas.conversation import (
    ClaimView, Claimant, ConfirmationRequest, ConversationHistory, IntakeTurn, MessageRequest,
)
from claimpilot.services.intake import apply_turn, immediate_safety_concern
from claimpilot.services.llm import LLMError, MODEL_LIMIT_MESSAGES
from claimpilot.services.photo_analysis import photo_follow_ups
from claimpilot.storage.intake import IntakeError
from claimpilot.storage.photos import PhotoStore

router = APIRouter(prefix="/api/v1", tags=["claim intake"])
bearer = HTTPBearer(auto_error=False)
RequestKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[!-~]+$")
]


def current_claimant(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Claimant:
    if credentials is not None:
        token = credentials.credentials.encode()
        for claimant in request.app.state.settings.claimpilot_claimants:
            if compare_digest(token, claimant.token.get_secret_value().encode()):
                return Claimant(tenant_id=claimant.tenant_id, claimant_id=claimant.claimant_id)
    raise IntakeError(401, "unauthorized", "A valid claimant credential is required.")


def intake_store(request: Request) -> PhotoStore:
    return request.app.state.intake_store


Actor = Annotated[Claimant, Depends(current_claimant)]
Store = Annotated[PhotoStore, Depends(intake_store)]


def with_photo_questions(store: PhotoStore, actor: Claimant, claim: ClaimView) -> ClaimView:
    analyses = store.analyses(actor, claim.claim_id, completed_only=True)
    return claim.model_copy(update={"photo_follow_ups": photo_follow_ups(claim, analyses)})


def turn_with_photo_questions(store: PhotoStore, actor: Claimant, turn: IntakeTurn) -> IntakeTurn:
    return turn.model_copy(update={"claim": with_photo_questions(store, actor, turn.claim)})


@router.post("/conversations", status_code=201)
def start_conversation(actor: Actor, store: Store, key: RequestKey, response: Response) -> IntakeTurn:
    turn = store.start(actor, key)
    response.headers["Location"] = f"/api/v1/conversations/{turn.claim.conversation_id}"
    return turn


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: UUID, payload: MessageRequest, actor: Actor, store: Store,
    key: RequestKey, request: Request,
) -> IntakeTurn:
    safety_concern = immediate_safety_concern(payload.message)
    pending = store.reserve(actor, conversation_id, key, payload, immediate_safety=safety_concern)
    if isinstance(pending, IntakeTurn):
        return turn_with_photo_questions(store, actor, pending)
    try:
        extraction = None
        if not safety_concern:
            extraction = extract_claim(
                payload.message, request.app.state.llm, pending_field=pending.state.pending_field
            )
        reply = apply_turn(
            pending.state, pending.message_id, payload.message, extraction, safety_concern=safety_concern
        )
    except LLMError as error:
        if replay := store.record_failure(actor, pending, error.code):
            return turn_with_photo_questions(store, actor, replay)
        if message := MODEL_LIMIT_MESSAGES.get(error.code):
            raise IntakeError(429, error.code, f"{message} Your message was saved.") from None
        raise IntakeError(
            503, "extraction_unavailable", "Your message was saved. Retry with the original request key."
        ) from None
    return turn_with_photo_questions(store, actor, store.finish(actor, pending, reply))


@router.get("/conversations/{conversation_id}")
def conversation_history(
    conversation_id: UUID, actor: Actor, store: Store,
    after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ConversationHistory:
    return store.history(actor, conversation_id, after, limit)


@router.get("/claims/{claim_id}")
def get_claim(claim_id: UUID, actor: Actor, store: Store) -> ClaimView:
    return with_photo_questions(store, actor, store.claim(actor, claim_id))


@router.post("/claims/{claim_id}/confirm")
def confirm_claim(
    claim_id: UUID, payload: ConfirmationRequest, actor: Actor, store: Store, key: RequestKey
) -> IntakeTurn:
    return store.confirm(actor, claim_id, key, payload)
