from uuid import UUID

from fastapi import APIRouter, Request, Response
from python_multipart.exceptions import MultipartParseError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from claimpilot.api.intake import Actor, RequestKey, Store
from claimpilot.schemas.photos import AnalysisView, AnalyzePhotos, PhotoMetadata
from claimpilot.services.llm import LLMError
from claimpilot.services.photo_analysis import analyze_photos
from claimpilot.services.photo_images import PhotoImageError, normalize_photo
from claimpilot.storage.intake import IntakeError
from claimpilot.storage.photos import AnalysisAttempt

router = APIRouter(prefix="/api/v1/claims/{claim_id}", tags=["claim photos"])
MULTIPART_OVERHEAD_BYTES = 16 * 1024


class MultipartTooLarge(MultiPartException):
    pass


async def read_photo(request: Request, max_bytes: int) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise IntakeError(415, "invalid_upload_type", "Send one photo as multipart form data.")

    async def bounded_stream():
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_bytes + MULTIPART_OVERHEAD_BYTES:
                raise MultipartTooLarge("Upload exceeds limit")
            yield chunk

    try:
        form = await MultiPartParser(
            request.headers, bounded_stream(), max_files=1, max_fields=0
        ).parse()
    except MultipartTooLarge:
        raise IntakeError(413, "photo_too_large", "Photo exceeds the upload limit.") from None
    except (MultiPartException, MultipartParseError, ValueError):
        raise IntakeError(422, "invalid_photo_upload", "Provide one file in the photo field.") from None
    try:
        photo = form.get("photo")
        if not isinstance(photo, UploadFile):
            raise IntakeError(422, "invalid_photo_upload", "Provide one file in the photo field.")
        data = await photo.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise IntakeError(413, "photo_too_large", "Photo exceeds the upload limit.")
        return data
    finally:
        await form.close()


@router.post(
    "/photos", status_code=201,
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["photo"],
        "properties": {"photo": {"type": "string", "format": "binary"}},
    }}}}},
)
async def upload_photo(
    claim_id: UUID, actor: Actor, store: Store, key: RequestKey, request: Request, response: Response,
) -> PhotoMetadata:
    await run_in_threadpool(store.claim, actor, claim_id)
    settings = request.app.state.settings
    data = await read_photo(request, settings.claimpilot_photo_max_bytes)
    try:
        normalized = await run_in_threadpool(
            normalize_photo, data, max_bytes=settings.claimpilot_photo_max_bytes,
            max_dimension=settings.claimpilot_photo_max_dimension,
            max_pixels=settings.claimpilot_photo_max_pixels,
            output_edge=settings.claimpilot_photo_output_edge,
        )
    except PhotoImageError as error:
        status = 413 if error.code != "invalid_photo" else 422
        raise IntakeError(status, error.code, "Upload a valid JPEG, PNG, or WebP within the limits.") from None
    metadata = await run_in_threadpool(
        store.upload, actor, claim_id, key, normalized, settings.claimpilot_photos_per_claim
    )
    response.headers["Location"] = f"/api/v1/claims/{claim_id}/photos/{metadata.id}"
    return metadata


@router.get("/photos")
def list_photos(claim_id: UUID, actor: Actor, store: Store) -> list[PhotoMetadata]:
    return store.list_photos(actor, claim_id)


@router.get("/photos/{photo_id}")
def get_photo(claim_id: UUID, photo_id: UUID, actor: Actor, store: Store) -> PhotoMetadata:
    return store.metadata(actor, claim_id, photo_id)


@router.get("/photos/{photo_id}/content", response_class=Response, responses={
    200: {"content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}}},
})
def get_photo_content(claim_id: UUID, photo_id: UUID, actor: Actor, store: Store) -> Response:
    return Response(
        store.content(actor, claim_id, photo_id), media_type="image/jpeg",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.delete("/photos/{photo_id}", status_code=204)
def delete_photo(claim_id: UUID, photo_id: UUID, actor: Actor, store: Store, key: RequestKey) -> Response:
    store.delete(actor, claim_id, photo_id, key)
    return Response(status_code=204)


@router.post("/photo-analyses", responses={503: {"model": AnalysisView}, 409: {"model": AnalysisView}})
def create_analysis(
    claim_id: UUID, payload: AnalyzePhotos, actor: Actor, store: Store,
    key: RequestKey, request: Request, response: Response,
) -> AnalysisView:
    settings = request.app.state.settings
    live = settings.claimpilot_llm_provider == "groq"
    attempt = store.reserve_analysis(
        actor, claim_id, key, payload,
        provider="groq" if live else "offline_fixture",
        model=settings.groq_vision_model if live else "offline-fixture",
    )
    if isinstance(attempt, AnalysisAttempt):
        try:
            report = analyze_photos(attempt, payload.context, request.app.state.llm)
        except LLMError as error:
            result = store.finish_analysis(actor, attempt, error_code=error.code)
        else:
            result = store.finish_analysis(actor, attempt, report=report)
    else:
        result = attempt
    response.headers["Location"] = f"/api/v1/claims/{claim_id}/photo-analyses/{result.id}"
    response.status_code = {"failed": 503, "invalidated": 409, "pending": 202}.get(result.status, 200)
    return result


@router.get("/photo-analyses")
def list_analyses(claim_id: UUID, actor: Actor, store: Store) -> list[AnalysisView]:
    return store.analyses(actor, claim_id)


@router.get("/photo-analyses/{analysis_id}")
def get_analysis(claim_id: UUID, analysis_id: UUID, actor: Actor, store: Store) -> AnalysisView:
    return store.analysis(actor, claim_id, analysis_id)
