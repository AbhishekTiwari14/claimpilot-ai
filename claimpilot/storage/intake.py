import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from claimpilot.schemas.conversation import (
    ClaimState, ClaimView, Claimant, ConfirmationRequest, ConversationHistory,
    ConversationMessage, IntakeTurn, MessageRequest,
)
from claimpilot.services.intake import COMPLETED_REPLY, WELCOME, confirm_state

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    claimant_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    pending_message_id TEXT,
    processing_error TEXT
);
CREATE TABLE IF NOT EXISTS claim_messages (
    sequence INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    role TEXT NOT NULL CHECK(role IN ('customer', 'assistant')),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claim_message_history ON claim_messages(claim_id, sequence);
CREATE TABLE IF NOT EXISTS idempotency_requests (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    claimant_id TEXT NOT NULL,
    route TEXT NOT NULL,
    request_key TEXT NOT NULL,
    digest TEXT NOT NULL,
    message_id TEXT REFERENCES claim_messages(id),
    response_json TEXT,
    UNIQUE(tenant_id, claimant_id, route, request_key)
);
"""

PHOTO_SCHEMA = """
CREATE TABLE claim_photos (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT,
    original_sha256 TEXT,
    data BLOB,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX unique_claim_photo ON claim_photos(claim_id, sha256) WHERE data IS NOT NULL;
CREATE TABLE photo_analyses (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    photo_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed', 'invalidated')),
    provider TEXT NOT NULL CHECK(provider IN ('offline_fixture', 'groq')),
    model TEXT NOT NULL,
    attempts INTEGER NOT NULL CHECK(attempts >= 1),
    result_json TEXT,
    error_code TEXT
);
CREATE INDEX analyses_by_claim ON photo_analyses(claim_id);
CREATE TABLE photo_analysis_images (
    analysis_id TEXT NOT NULL REFERENCES photo_analyses(id),
    photo_id TEXT NOT NULL REFERENCES claim_photos(id),
    PRIMARY KEY (analysis_id, photo_id)
);
CREATE INDEX analyses_by_photo ON photo_analysis_images(photo_id);
"""


class IntakeError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class PendingTurn:
    request_id: int
    state: ClaimState
    message_id: UUID


class IntakeStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connection(self, *, write: bool = False):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("PRAGMA user_version = 1")
                version = 1
            if version == 1:
                for statement in PHOTO_SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("PRAGMA user_version = 2")
                version = 2
            if version == 2:
                from claimpilot.storage.knowledge import KNOWLEDGE_SCHEMA

                for statement in KNOWLEDGE_SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("PRAGMA user_version = 3")
                version = 3
            if version == 3:
                from claimpilot.storage.contractors import CONTRACTOR_SCHEMA

                for statement in CONTRACTOR_SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("PRAGMA user_version = 4")
            elif version != 4:
                raise RuntimeError("Unsupported ClaimPilot database version")

    @staticmethod
    def _claim(db, actor: Claimant, *, claim_id=None, conversation_id=None):
        row = db.execute(
            """SELECT * FROM claims WHERE tenant_id = ? AND claimant_id = ?
               AND (id = ? OR conversation_id = ?)""",
            (actor.tenant_id, actor.claimant_id, claim_id, conversation_id),
        ).fetchone()
        if row is None:
            raise IntakeError(404, "not_found", "Intake record not found.")
        return row

    @staticmethod
    def _view(row) -> ClaimView:
        view = ClaimView.model_validate_json(row["state_json"])
        view.pending_message_id = UUID(row["pending_message_id"]) if row["pending_message_id"] else None
        view.processing_error = row["processing_error"]
        return view

    @staticmethod
    def _request(db, actor: Claimant, route: str, key: str, body: str):
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        values = (actor.tenant_id, actor.claimant_id, route, key)
        row = db.execute(
            """SELECT * FROM idempotency_requests
               WHERE tenant_id = ? AND claimant_id = ? AND route = ? AND request_key = ?""",
            values,
        ).fetchone()
        if row is not None:
            if row["digest"] != digest:
                raise IntakeError(409, "idempotency_conflict", "Use a new key for a different request.")
            return row, None
        cursor = db.execute(
            """INSERT INTO idempotency_requests (tenant_id, claimant_id, route, request_key, digest)
               VALUES (?, ?, ?, ?, ?)""",
            (*values, digest),
        )
        return None, cursor.lastrowid

    @staticmethod
    def _append(db, claim_id: UUID, role, text: str) -> ConversationMessage:
        message_id = uuid4()
        created_at = datetime.now(timezone.utc)
        cursor = db.execute(
            """INSERT INTO claim_messages (id, claim_id, role, text, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(message_id), str(claim_id), role, text, created_at.isoformat()),
        )
        return ConversationMessage(
            id=message_id, sequence=cursor.lastrowid, role=role, text=text, created_at=created_at
        )

    @staticmethod
    def _save_result(db, request_id: int, turn: IntakeTurn) -> IntakeTurn:
        db.execute(
            "UPDATE idempotency_requests SET response_json = ? WHERE id = ?",
            (turn.model_dump_json(exclude_computed_fields=True), request_id),
        )
        return turn

    def start(self, actor: Claimant, key: str) -> IntakeTurn:
        with self._connection(write=True) as db:
            existing, request_id = self._request(db, actor, "conversations:start", key, "{}")
            if existing is not None:
                return IntakeTurn.model_validate_json(existing["response_json"])
            claim = ClaimView(claim_id=uuid4(), conversation_id=uuid4())
            db.execute(
                """INSERT INTO claims (id, conversation_id, tenant_id, claimant_id, state_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (str(claim.claim_id), str(claim.conversation_id), actor.tenant_id,
                 actor.claimant_id, claim.model_dump_json(exclude_computed_fields=True)),
            )
            reply = self._append(db, claim.claim_id, "assistant", WELCOME)
            return self._save_result(db, request_id, IntakeTurn(claim=claim, reply=reply))

    def claim(self, actor: Claimant, claim_id: UUID) -> ClaimView:
        with self._connection() as db:
            return self._view(self._claim(db, actor, claim_id=str(claim_id)))

    def history(
        self, actor: Claimant, conversation_id: UUID, after: int, limit: int
    ) -> ConversationHistory:
        with self._connection() as db:
            claim = self._claim(db, actor, conversation_id=str(conversation_id))
            rows = db.execute(
                """SELECT id, sequence, role, text, created_at FROM claim_messages
                   WHERE claim_id = ? AND sequence > ? ORDER BY sequence LIMIT ?""",
                (claim["id"], after, limit + 1),
            ).fetchall()
            return ConversationHistory(
                conversation_id=conversation_id, claim_id=claim["id"],
                messages=[ConversationMessage.model_validate(dict(row)) for row in rows[:limit]],
                next_after=rows[limit - 1]["sequence"] if len(rows) > limit else None,
            )

    def reserve(
        self, actor: Claimant, conversation_id: UUID, key: str, payload: MessageRequest,
        *, immediate_safety: bool = False,
    ) -> IntakeTurn | PendingTurn:
        with self._connection(write=True) as db:
            row = self._claim(db, actor, conversation_id=str(conversation_id))
            existing, request_id = self._request(
                db, actor, f"conversations:{conversation_id}:messages", key, payload.model_dump_json()
            )
            state = ClaimState.model_validate_json(row["state_json"])
            if existing is not None:
                if existing["response_json"]:
                    return IntakeTurn.model_validate_json(existing["response_json"])
                if row["pending_message_id"] != existing["message_id"]:
                    raise IntakeError(409, "stale_turn", "This turn is no longer pending.")
                return PendingTurn(existing["id"], state, UUID(existing["message_id"]))
            self._check_version(state, payload.expected_version)
            if state.status in ("intake_completed", "human_assistance_required"):
                raise IntakeError(409, "intake_closed", "This intake no longer accepts messages.")
            replacing_failure = payload.replace_failed_message and row["processing_error"] is not None
            if not immediate_safety and not replacing_failure:
                self._check_idle(row)
            message = self._append(db, state.claim_id, "customer", payload.message)
            db.execute(
                "UPDATE claims SET pending_message_id = ?, processing_error = NULL WHERE id = ?",
                (str(message.id), str(state.claim_id)),
            )
            db.execute(
                "UPDATE idempotency_requests SET message_id = ? WHERE id = ?",
                (str(message.id), request_id),
            )
            return PendingTurn(request_id, state, message.id)

    def _pending_result(self, db, actor: Claimant, pending: PendingTurn) -> IntakeTurn | None:
        row = self._claim(db, actor, claim_id=str(pending.state.claim_id))
        request = db.execute(
            """SELECT response_json FROM idempotency_requests
               WHERE id = ? AND tenant_id = ? AND claimant_id = ? AND message_id = ?""",
            (pending.request_id, actor.tenant_id, actor.claimant_id, str(pending.message_id)),
        ).fetchone()
        if request and request["response_json"]:
            return IntakeTurn.model_validate_json(request["response_json"])
        if not request or row["pending_message_id"] != str(pending.message_id):
            raise IntakeError(409, "stale_turn", "This turn is no longer pending.")
        return None

    def finish(self, actor: Claimant, pending: PendingTurn, reply_text: str) -> IntakeTurn:
        with self._connection(write=True) as db:
            if replay := self._pending_result(db, actor, pending):
                return replay
            reply = self._append(db, pending.state.claim_id, "assistant", reply_text)
            claim = self._save_state(db, pending.state)
            return self._save_result(db, pending.request_id, IntakeTurn(claim=claim, reply=reply))

    def record_failure(self, actor: Claimant, pending: PendingTurn, code: str) -> IntakeTurn | None:
        with self._connection(write=True) as db:
            if replay := self._pending_result(db, actor, pending):
                return replay
            db.execute(
                "UPDATE claims SET processing_error = ? WHERE id = ?",
                (code, str(pending.state.claim_id)),
            )
            return None

    @staticmethod
    def _save_state(db, state: ClaimState) -> ClaimView:
        snapshot = state.model_dump_json(exclude_computed_fields=True)
        db.execute(
            """UPDATE claims SET state_json = ?, pending_message_id = NULL, processing_error = NULL
               WHERE id = ?""",
            (snapshot, str(state.claim_id)),
        )
        return ClaimView.model_validate_json(snapshot)

    @staticmethod
    def _check_version(state: ClaimState, version: int) -> None:
        if state.version != version:
            raise IntakeError(409, "stale_version", "Refresh the claim before submitting again.")

    @staticmethod
    def _check_idle(row) -> None:
        if row["pending_message_id"] is not None:
            raise IntakeError(409, "turn_pending", "Retry the pending message with its original key.")

    def confirm(
        self, actor: Claimant, claim_id: UUID, key: str, payload: ConfirmationRequest
    ) -> IntakeTurn:
        with self._connection(write=True) as db:
            row = self._claim(db, actor, claim_id=str(claim_id))
            existing, request_id = self._request(
                db, actor, f"claims:{claim_id}:confirm", key, payload.model_dump_json()
            )
            if existing is not None:
                return IntakeTurn.model_validate_json(existing["response_json"])
            state = ClaimState.model_validate_json(row["state_json"])
            self._check_version(state, payload.expected_version)
            self._check_idle(row)
            if not payload.confirmed:
                raise IntakeError(422, "confirmation_required", "Confirm the current claim summary explicitly.")
            if state.status != "awaiting_customer_confirmation" or state.missing_fields or state.conflicts:
                raise IntakeError(409, "not_ready_to_confirm", "The claim summary is not ready for confirmation.")
            confirm_state(state)
            self._append(db, claim_id, "customer", "I confirm the claim summary.")
            reply = self._append(db, claim_id, "assistant", COMPLETED_REPLY)
            claim = self._save_state(db, state)
            return self._save_result(db, request_id, IntakeTurn(claim=claim, reply=reply))
