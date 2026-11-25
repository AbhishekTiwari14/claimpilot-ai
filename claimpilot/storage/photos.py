from dataclasses import dataclass
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from claimpilot.schemas.conversation import Claimant
from claimpilot.schemas.photos import AnalysisView, AnalyzePhotos, DamageReport, PhotoMetadata
from claimpilot.services.photo_images import NormalizedPhoto
from claimpilot.storage.intake import IntakeError, IntakeStore


@dataclass(frozen=True)
class AnalysisAttempt:
    analysis: AnalysisView
    images: list[tuple[str, bytes]]
    original_hashes: list[str]
    claim_context: dict[str, str | list[str]]


class PhotoStore(IntakeStore):
    def upload(
        self, actor: Claimant, claim_id: UUID, key: str, image: NormalizedPhoto, max_photos: int
    ) -> PhotoMetadata:
        with self._connection(write=True) as db:
            self._claim(db, actor, claim_id=str(claim_id))
            saved, request_id = self._request(
                db, actor, f"claims:{claim_id}:photos", key, image.original_sha256
            )
            if saved is not None:
                return self._metadata(self._photo(db, claim_id, UUID(saved["response_json"])))
            row = db.execute(
                """SELECT id, claim_id, width, height, size_bytes, created_at
                   FROM claim_photos WHERE claim_id = ? AND sha256 = ? AND data IS NOT NULL""",
                (str(claim_id), image.sha256),
            ).fetchone()
            if row is None:
                count = db.execute(
                    "SELECT COUNT(*) FROM claim_photos WHERE claim_id = ? AND data IS NOT NULL",
                    (str(claim_id),),
                ).fetchone()[0]
                if count >= max_photos:
                    raise IntakeError(409, "photo_limit_reached", "The claim photo limit was reached.")
                photo_id = uuid4()
                db.execute(
                    """INSERT INTO claim_photos
                       (id, claim_id, width, height, size_bytes, sha256, original_sha256, data, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(photo_id), str(claim_id), image.width, image.height, len(image.data),
                     image.sha256, image.original_sha256, image.data, datetime.now(timezone.utc).isoformat()),
                )
                row = self._photo(db, claim_id, photo_id)
            db.execute(
                "UPDATE idempotency_requests SET response_json = ? WHERE id = ?",
                (row["id"], request_id),
            )
            return self._metadata(row)

    @staticmethod
    def _metadata(row) -> PhotoMetadata:
        return PhotoMetadata.model_validate({
            field: row[field] for field in ("id", "claim_id", "width", "height", "size_bytes", "created_at")
        })

    @staticmethod
    def _photo(db, claim_id: UUID, photo_id: UUID, *, include_data: bool = False):
        if include_data:
            sql = """SELECT id, claim_id, width, height, size_bytes, created_at, original_sha256, data
                     FROM claim_photos WHERE claim_id = ? AND id = ? AND data IS NOT NULL"""
        else:
            sql = """SELECT id, claim_id, width, height, size_bytes, created_at
                     FROM claim_photos WHERE claim_id = ? AND id = ? AND data IS NOT NULL"""
        row = db.execute(sql, (str(claim_id), str(photo_id))).fetchone()
        if row is None:
            raise IntakeError(404, "photo_not_found", "Photo not found.")
        return row

    def list_photos(self, actor: Claimant, claim_id: UUID) -> list[PhotoMetadata]:
        with self._connection() as db:
            self._claim(db, actor, claim_id=str(claim_id))
            rows = db.execute(
                """SELECT id, claim_id, width, height, size_bytes, created_at
                   FROM claim_photos WHERE claim_id = ? AND data IS NOT NULL ORDER BY created_at, id""",
                (str(claim_id),),
            )
            return [self._metadata(row) for row in rows]

    def content(self, actor: Claimant, claim_id: UUID, photo_id: UUID) -> bytes:
        with self._connection() as db:
            self._claim(db, actor, claim_id=str(claim_id))
            return self._photo(db, claim_id, photo_id, include_data=True)["data"]

    def metadata(self, actor: Claimant, claim_id: UUID, photo_id: UUID) -> PhotoMetadata:
        with self._connection() as db:
            self._claim(db, actor, claim_id=str(claim_id))
            return self._metadata(self._photo(db, claim_id, photo_id))

    def delete(self, actor: Claimant, claim_id: UUID, photo_id: UUID, key: str) -> None:
        with self._connection(write=True) as db:
            self._claim(db, actor, claim_id=str(claim_id))
            saved, request_id = self._request(db, actor, f"claims:{claim_id}:photos:{photo_id}:delete", key, "{}")
            if saved is not None:
                return
            self._photo(db, claim_id, photo_id)
            db.execute("PRAGMA secure_delete = ON")
            db.execute(
                "UPDATE claim_photos SET data = NULL, sha256 = NULL, original_sha256 = NULL WHERE id = ?",
                (str(photo_id),),
            )
            db.execute(
                """UPDATE photo_analyses SET status = 'invalidated', result_json = NULL, error_code = NULL
                   WHERE claim_id = ? AND id IN (
                      SELECT analysis_id FROM photo_analysis_images WHERE photo_id = ?
                   )""",
                (str(claim_id), str(photo_id)),
            )
            db.execute(
                "UPDATE idempotency_requests SET response_json = 'null' WHERE id = ?",
                (request_id,),
            )

    @staticmethod
    def _analysis_view(row) -> AnalysisView:
        fields = ("id", "claim_id", "status", "provider", "model", "attempts", "error_code")
        return AnalysisView.model_validate({
            **{field: row[field] for field in fields},
            "photo_ids": json.loads(row["photo_ids_json"]),
            "result": DamageReport.model_validate_json(row["result_json"]) if row["result_json"] else None,
        })

    @staticmethod
    def _analysis(db, claim_id: UUID, analysis_id: UUID):
        row = db.execute(
            "SELECT * FROM photo_analyses WHERE claim_id = ? AND id = ?",
            (str(claim_id), str(analysis_id)),
        ).fetchone()
        if row is None:
            raise IntakeError(404, "analysis_not_found", "Photo analysis not found.")
        return row

    def analysis(self, actor: Claimant, claim_id: UUID, analysis_id: UUID) -> AnalysisView:
        with self._connection() as db:
            self._claim(db, actor, claim_id=str(claim_id))
            return self._analysis_view(self._analysis(db, claim_id, analysis_id))

    def analyses(self, actor: Claimant, claim_id: UUID, *, completed_only: bool = False) -> list[AnalysisView]:
        with self._connection() as db:
            self._claim(db, actor, claim_id=str(claim_id))
            rows = db.execute(
                """SELECT * FROM photo_analyses WHERE claim_id = ? AND (? = 0 OR status = 'completed')
                   ORDER BY rowid DESC LIMIT 20""",
                (str(claim_id), completed_only),
            )
            return [self._analysis_view(row) for row in rows]

    def reserve_analysis(
        self, actor: Claimant, claim_id: UUID, key: str, payload: AnalyzePhotos, *,
        provider: str, model: str,
    ) -> AnalysisView | AnalysisAttempt:
        with self._connection(write=True) as db:
            claim = self._view(self._claim(db, actor, claim_id=str(claim_id)))
            saved, request_id = self._request(
                db, actor, f"claims:{claim_id}:photo-analyses", key, payload.model_dump_json()
            )
            if saved is not None:
                analysis_id = UUID(saved["response_json"])
                row = self._analysis(db, claim_id, analysis_id)
                if row["status"] in ("completed", "invalidated"):
                    return self._analysis_view(row)
                if row["provider"] != provider or row["model"] != model:
                    raise IntakeError(409, "analysis_provider_changed", "Use a new key to change the analysis model.")
                if row["status"] == "failed":
                    db.execute(
                        """UPDATE photo_analyses SET status = 'pending', attempts = attempts + 1,
                           error_code = NULL WHERE id = ?""",
                        (str(analysis_id),),
                    )
            else:
                analysis_id = uuid4()
                db.execute(
                    """INSERT INTO photo_analyses (id, claim_id, photo_ids_json, status, provider, model, attempts)
                       VALUES (?, ?, ?, 'pending', ?, ?, 1)""",
                    (str(analysis_id), str(claim_id), json.dumps([str(value) for value in payload.photo_ids]),
                     provider, model),
                )
                for photo_id in payload.photo_ids:
                    self._photo(db, claim_id, photo_id)
                    db.execute(
                        "INSERT INTO photo_analysis_images (analysis_id, photo_id) VALUES (?, ?)",
                        (str(analysis_id), str(photo_id)),
                    )
                db.execute(
                    "UPDATE idempotency_requests SET response_json = ? WHERE id = ?",
                    (str(analysis_id), request_id),
                )
            view = self._analysis_view(self._analysis(db, claim_id, analysis_id))
            photos = [self._photo(db, claim_id, photo_id, include_data=True) for photo_id in view.photo_ids]
            allowed_fields = ("damage_type", "affected_areas", "suspected_cause", "ongoing_hazards")
            context = {field: claim.facts[field].value for field in allowed_fields if field in claim.facts}
            return AnalysisAttempt(
                view, [(row["id"], row["data"]) for row in photos],
                [row["original_sha256"] for row in photos], context,
            )

    def finish_analysis(
        self, actor: Claimant, attempt: AnalysisAttempt, *,
        report: DamageReport | None = None, error_code: str | None = None,
    ) -> AnalysisView:
        analysis = attempt.analysis
        with self._connection(write=True) as db:
            self._claim(db, actor, claim_id=str(analysis.claim_id))
            db.execute(
                """UPDATE photo_analyses SET status = ?, result_json = ?, error_code = ?
                   WHERE id = ? AND claim_id = ? AND status = 'pending' AND attempts = ?""",
                ("completed" if report is not None else "failed",
                 report.model_dump_json() if report is not None else None, error_code,
                 str(analysis.id), str(analysis.claim_id), analysis.attempts),
            )
            return self._analysis_view(self._analysis(db, analysis.claim_id, analysis.id))
