from hmac import compare_digest
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from python_multipart.exceptions import MultipartParseError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from claimpilot.api.intake import RequestKey, bearer
from claimpilot.schemas.knowledge import (
    Document, DocumentInput, Evidence, GroundedAnswer, Operator, PolicyAssessment, PolicyBinding,
    Scope, SearchRequest,
)
from claimpilot.services.document_ingestion import extract_document
from claimpilot.services.knowledge_answers import assess_claim, grounded_answer
from claimpilot.services.knowledge_index import KnowledgeIndex
from claimpilot.storage.intake import IntakeError

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


class DocumentTooLarge(MultiPartException):
    pass


def current_operator(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Operator:
    if credentials is not None:
        supplied = credentials.credentials.encode()
        for operator in request.app.state.settings.claimpilot_kb_operators:
            if compare_digest(supplied, operator.token.get_secret_value().encode()):
                return Operator(tenant_id=operator.tenant_id, operator_id=operator.operator_id)
        for claimant in request.app.state.settings.claimpilot_claimants:
            if compare_digest(supplied, claimant.token.get_secret_value().encode()):
                raise IntakeError(403, "operator_required", "Knowledge access requires an operator credential.")
    raise IntakeError(401, "unauthorized", "A valid operator credential is required.")


def current_index(request: Request) -> KnowledgeIndex:
    return request.app.state.knowledge


Actor = Annotated[Operator, Depends(current_operator)]
Index = Annotated[KnowledgeIndex, Depends(current_index)]


async def read_document(request: Request, limit: int) -> tuple[DocumentInput, str, bytes]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise IntakeError(415, "invalid_upload_type", "Use multipart form data.")

    async def stream():
        size = 0
        async for part in request.stream():
            size += len(part)
            if size > limit + 16_384:
                raise DocumentTooLarge("Document exceeds limit")
            yield part

    try:
        form = await MultiPartParser(
            request.headers, stream(), max_files=1, max_fields=1, max_part_size=4_096,
        ).parse()
    except DocumentTooLarge:
        raise IntakeError(413, "document_too_large", "Document exceeds the upload limit.") from None
    except (MultiPartException, MultipartParseError, ValueError):
        raise IntakeError(422, "invalid_document_upload", "Use one document and JSON metadata.") from None
    try:
        document, raw_metadata = form.get("document"), form.get("metadata")
        if not isinstance(document, UploadFile) or not document.filename or not isinstance(raw_metadata, str):
            raise IntakeError(422, "invalid_document_upload", "Use one document and JSON metadata.")
        try:
            metadata = DocumentInput.model_validate_json(raw_metadata)
        except ValidationError:
            raise IntakeError(422, "invalid_document_metadata", "Invalid document classification or policy scope.") from None
        data = await document.read(limit + 1)
        if len(data) > limit:
            raise IntakeError(413, "document_too_large", "Document exceeds the upload limit.")
        return metadata, document.filename, data
    finally:
        await form.close()


@router.post("/knowledge/documents", status_code=201, openapi_extra={"requestBody": {
    "required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["document", "metadata"],
        "properties": {"document": {"type": "string", "format": "binary"}, "metadata": {"type": "string"}},
    }}},
}})
async def upload_document(
    actor: Actor, index: Index, key: RequestKey, request: Request, response: Response,
) -> Document:
    metadata, filename, data = await read_document(request, index.settings.claimpilot_kb_max_bytes)
    filename, kind, sections = await run_in_threadpool(extract_document, filename, data)
    doc = await run_in_threadpool(index.store.upload, actor, key, metadata, filename, kind, data, sections)
    if doc.status in ("pending", "indexing"):
        doc = await run_in_threadpool(index.build, actor, doc.id, f"upload:{doc.id}")
    response.headers["Location"] = f"/api/v1/knowledge/documents/{doc.id}"
    return doc


@router.get("/knowledge/documents")
def documents(
    actor: Actor, index: Index, limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[Document]:
    return index.store.documents(actor, limit=limit, offset=offset)


@router.get("/knowledge/documents/{document_id}")
def document(document_id: UUID, actor: Actor, index: Index) -> Document:
    return index.store.document(actor, document_id)


@router.post("/knowledge/documents/{document_id}/index")
def index_document(document_id: UUID, actor: Actor, index: Index, key: RequestKey) -> Document:
    return index.build(actor, document_id, f"manual:{key}", force=True)


@router.post("/knowledge/rebuild")
def rebuild(actor: Actor, index: Index, key: RequestKey) -> list[Document]:
    ids = index.store.rebuild_ids(actor, key, index.config)
    result = []
    for document_id in ids:
        try:
            result.append(index.build(actor, document_id, f"rebuild:{key}", force=True))
        except IntakeError as error:
            if error.status != 404:
                raise
    return result


@router.delete("/knowledge/documents/{document_id}", status_code=204)
def delete_document(document_id: UUID, actor: Actor, index: Index, key: RequestKey) -> Response:
    index.delete(actor, document_id, key)
    return Response(status_code=204)


@router.post("/knowledge/search")
def search(payload: SearchRequest, actor: Actor, index: Index) -> list[Evidence]:
    return index.search(actor.tenant_id, payload)


@router.post("/knowledge/playground/query")
def ask(payload: SearchRequest, actor: Actor, index: Index, request: Request) -> GroundedAnswer:
    return grounded_answer(index, request.app.state.llm, actor, payload)


@router.post("/claims/{claim_id}/policy-binding")
def bind(claim_id: UUID, payload: PolicyBinding, actor: Actor, index: Index, key: RequestKey) -> Scope:
    return index.store.bind_policy(actor, claim_id, key, payload.policy_version)


@router.post("/claims/{claim_id}/policy-assessment")
def assess(claim_id: UUID, actor: Actor, index: Index, request: Request) -> PolicyAssessment:
    return assess_claim(index, request.app.state.llm, actor, claim_id)
