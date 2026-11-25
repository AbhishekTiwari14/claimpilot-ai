from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import UUID, uuid4

from claimpilot.schemas.conversation import Claimant
from claimpilot.schemas.knowledge import Document, DocumentInput, Operator, Scope, Section
from claimpilot.storage.intake import IntakeError, IntakeStore

KNOWLEDGE_SCHEMA = """
CREATE TABLE knowledge_documents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    document_type TEXT NOT NULL CHECK(document_type IN ('policy','operations')),
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    subject_claimant_id TEXT NOT NULL,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    format TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    document_version INTEGER NOT NULL,
    sections_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','indexing','ready','failed')),
    generation INTEGER NOT NULL DEFAULT 0,
    config_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE UNIQUE INDEX knowledge_dedup ON knowledge_documents(
    tenant_id,document_type,policy_id,policy_version,subject_claimant_id,content_hash
) WHERE deleted_at IS NULL;
CREATE INDEX knowledge_scope ON knowledge_documents(tenant_id,document_type,policy_id,policy_version,status);
CREATE TABLE knowledge_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(id),
    generation INTEGER NOT NULL,
    page INTEGER,
    section TEXT,
    text TEXT NOT NULL
);
CREATE INDEX knowledge_chunks_doc ON knowledge_chunks(document_id,generation);
CREATE TABLE claim_policy_bindings (
    claim_id TEXT PRIMARY KEY REFERENCES claims(id),
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    operator_id TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class IndexWork:
    doc: Document
    sections: list[Section]


class KnowledgeStore(IntakeStore):
    @staticmethod
    def _operator(actor: Operator) -> Claimant:
        return Claimant(tenant_id=actor.tenant_id, claimant_id=f"knowledge:{actor.operator_id}")

    @staticmethod
    def _document(db, actor: Operator, doc_id: UUID):
        row = db.execute(
            "SELECT * FROM knowledge_documents WHERE tenant_id=? AND id=? AND deleted_at IS NULL",
            (actor.tenant_id, str(doc_id)),
        ).fetchone()
        if row is None:
            raise IntakeError(404, "document_not_found", "Document not found.")
        return row

    @staticmethod
    def _metadata(row) -> Document:
        fields = (
            "id", "title", "filename", "format", "content_hash", "document_type",
            "document_version", "generation", "status", "error_code", "created_at",
        )
        return Document.model_validate({
            **{field: row[field] for field in fields},
            **{field: row[field] or None for field in ("policy_id", "policy_version", "subject_claimant_id")},
            "indexing_config": json.loads(row["config_json"]) if row["config_json"] else None,
        })

    def upload(
        self, actor: Operator, key: str, metadata: DocumentInput, filename: str, format_: str,
        data: bytes, sections: list[Section],
    ) -> Document:
        digest = sha256(data).hexdigest()
        scope = (
            actor.tenant_id, metadata.document_type, metadata.policy_id or "",
            metadata.policy_version or "", metadata.subject_claimant_id or "",
        )
        with self._connection(write=True) as db:
            saved, request_id = self._request(
                db, self._operator(actor), "knowledge:upload", key,
                json.dumps([metadata.model_dump(), filename, digest], sort_keys=True),
            )
            if saved is not None:
                return self._metadata(self._document(db, actor, UUID(saved["response_json"])))
            row = db.execute(
                """SELECT id FROM knowledge_documents WHERE tenant_id=? AND document_type=? AND
                   policy_id=? AND policy_version=? AND subject_claimant_id=? AND content_hash=?
                   AND deleted_at IS NULL""",
                (*scope, digest),
            ).fetchone()
            doc_id = UUID(row["id"]) if row else uuid4()
            if row is None:
                db.execute(
                    """INSERT INTO knowledge_documents (id,tenant_id,document_type,policy_id,
                       policy_version,subject_claimant_id,title,filename,format,content_hash,
                       document_version,sections_json,status,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)""",
                    (str(doc_id), *scope, metadata.title, filename, format_, digest,
                     metadata.document_version, json.dumps([s.model_dump() for s in sections]),
                     datetime.now(timezone.utc).isoformat()),
                )
            db.execute("UPDATE idempotency_requests SET response_json=? WHERE id=?", (str(doc_id), request_id))
            return self._metadata(self._document(db, actor, doc_id))

    def documents(self, actor: Operator, *, limit: int = 100, offset: int = 0) -> list[Document]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT * FROM knowledge_documents WHERE tenant_id=? AND deleted_at IS NULL
                   ORDER BY created_at DESC,id LIMIT ? OFFSET ?""",
                (actor.tenant_id, limit, offset),
            )
            return [self._metadata(row) for row in rows]

    def document(self, actor: Operator, doc_id: UUID) -> Document:
        with self._connection() as db:
            return self._metadata(self._document(db, actor, doc_id))

    def rebuild_ids(self, actor: Operator, key: str, config: dict) -> list[UUID]:
        with self._connection(write=True) as db:
            saved, request_id = self._request(
                db, self._operator(actor), "knowledge:rebuild", key, json.dumps(config, sort_keys=True),
            )
            if saved is not None:
                return [UUID(value) for value in json.loads(saved["response_json"])]
            rows = db.execute(
                "SELECT id FROM knowledge_documents WHERE tenant_id=? AND deleted_at IS NULL ORDER BY id",
                (actor.tenant_id,),
            )
            ids = [row["id"] for row in rows]
            db.execute("UPDATE idempotency_requests SET response_json=? WHERE id=?", (json.dumps(ids), request_id))
            return [UUID(value) for value in ids]

    def reserve_index(
        self, actor: Operator, doc_id: UUID, key: str, config: dict, *, force: bool,
    ) -> Document | IndexWork:
        with self._connection(write=True) as db:
            row = self._document(db, actor, doc_id)
            config_json = json.dumps(config, sort_keys=True)
            saved, request_id = self._request(
                db, self._operator(actor), f"knowledge:index:{doc_id}", key,
                json.dumps([config, force], sort_keys=True),
            )
            if saved is not None:
                if int(saved["response_json"]) != row["generation"] or row["status"] != "indexing":
                    return self._metadata(row)
            else:
                generation = row["generation"]
                if force or row["status"] != "ready" or row["config_json"] != config_json:
                    generation += 1
                    db.execute(
                        """UPDATE knowledge_documents SET status='indexing',generation=?,
                           config_json=?,error_code=NULL WHERE id=?""",
                        (generation, config_json, str(doc_id)),
                    )
                db.execute("UPDATE idempotency_requests SET response_json=? WHERE id=?", (str(generation), request_id))
                row = self._document(db, actor, doc_id)
                if row["status"] != "indexing":
                    return self._metadata(row)
            return IndexWork(
                self._metadata(row),
                [Section.model_validate(entry) for entry in json.loads(row["sections_json"])],
            )

    def finish_index(
        self, actor: Operator, doc: Document, chunks: list[tuple[str, Section]], error: str | None,
    ) -> Document | None:
        with self._connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM knowledge_documents WHERE id=? AND tenant_id=? AND deleted_at IS NULL",
                (str(doc.id), actor.tenant_id),
            ).fetchone()
            if row is None:
                return None
            if row["generation"] != doc.generation or row["status"] != "indexing":
                return self._metadata(row)
            if error is None:
                db.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (str(doc.id),))
                db.executemany(
                    """INSERT INTO knowledge_chunks (chunk_id,document_id,generation,page,section,text)
                       VALUES (?,?,?,?,?,?)""",
                    [(key, str(doc.id), doc.generation, part.page, part.section, part.text) for key, part in chunks],
                )
            db.execute(
                "UPDATE knowledge_documents SET status=?,error_code=? WHERE id=?",
                ("failed" if error else "ready", error, str(doc.id)),
            )
            return self._metadata(self._document(db, actor, doc.id))

    def delete(self, actor: Operator, doc_id: UUID, key: str) -> None:
        with self._connection(write=True) as db:
            saved, request_id = self._request(
                db, self._operator(actor), f"knowledge:delete:{doc_id}", key, "{}",
            )
            if saved is not None:
                return
            self._document(db, actor, doc_id)
            db.execute("PRAGMA secure_delete=ON")
            db.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (str(doc_id),))
            db.execute(
                "UPDATE knowledge_documents SET deleted_at=?,sections_json='[]' WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), str(doc_id)),
            )
            db.execute("UPDATE idempotency_requests SET response_json='null' WHERE id=?", (request_id,))

    def scoped_generations(self, tenant: str, scope: Scope, config: dict, subject: str) -> list[str]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT id,generation FROM knowledge_documents WHERE tenant_id=? AND document_type=?
                   AND policy_id=? AND policy_version=? AND subject_claimant_id IN ('',?)
                   AND config_json=? AND status='ready' AND deleted_at IS NULL""",
                (tenant, scope.document_type, scope.policy_id or "", scope.policy_version or "",
                 subject, json.dumps(config, sort_keys=True)),
            )
            return [f"{row['id']}:{row['generation']}" for row in rows]

    def scoped_chunks(self, tenant: str, scope: Scope, config: dict, subject: str, keys: list[str]):
        if not keys:
            return {}
        markers = ",".join("?" for _ in keys)
        with self._connection() as db:
            rows = db.execute(
                f"""SELECT c.*,d.filename,d.title,d.document_version,d.content_hash,d.document_type,
                    d.policy_id,d.policy_version FROM knowledge_chunks c
                    JOIN knowledge_documents d ON d.id=c.document_id AND d.generation=c.generation
                    WHERE d.tenant_id=? AND d.document_type=? AND d.policy_id=? AND d.policy_version=?
                      AND d.subject_claimant_id IN ('',?) AND d.config_json=?
                      AND d.status='ready' AND d.deleted_at IS NULL AND c.chunk_id IN ({markers})""",
                (tenant, scope.document_type, scope.policy_id or "", scope.policy_version or "",
                 subject, json.dumps(config, sort_keys=True), *keys),
            )
            return {row["chunk_id"]: row for row in rows}

    @staticmethod
    def _confirmed_claim(db, actor: Operator, claim_id: UUID):
        row = db.execute(
            "SELECT * FROM claims WHERE id=? AND tenant_id=?", (str(claim_id), actor.tenant_id)
        ).fetchone()
        if row is None:
            raise IntakeError(404, "not_found", "Claim not found.")
        state = IntakeStore._view(row)
        if state.status != "intake_completed" or not state.confirmed_at:
            raise IntakeError(409, "claim_unconfirmed", "Assessment requires confirmed claim facts.")
        facts = {key: item.value for key, item in state.facts.items() if item.status == "customer_confirmed"}
        return row, facts

    def bind_policy(self, actor: Operator, claim_id: UUID, key: str, version: str) -> Scope:
        with self._connection(write=True) as db:
            row, facts = self._confirmed_claim(db, actor, claim_id)
            policy = facts.get("policy_id")
            saved, request_id = self._request(db, self._operator(actor), f"knowledge:bind:{claim_id}", key, version)
            if saved:
                return Scope.model_validate_json(saved["response_json"])
            found = db.execute(
                """SELECT 1 FROM knowledge_documents WHERE tenant_id=? AND document_type='policy'
                   AND policy_id=? AND policy_version=? AND subject_claimant_id IN ('',?)
                   AND deleted_at IS NULL LIMIT 1""",
                (actor.tenant_id, policy, version, row["claimant_id"]),
            ).fetchone()
            if not found:
                raise IntakeError(404, "policy_not_found", "No accessible policy version matches the confirmed policy ID.")
            db.execute(
                """INSERT INTO claim_policy_bindings VALUES (?,?,?,?) ON CONFLICT(claim_id)
                   DO UPDATE SET policy_id=excluded.policy_id,policy_version=excluded.policy_version,
                   operator_id=excluded.operator_id""",
                (str(claim_id), policy, version, actor.operator_id),
            )
            scope = Scope(document_type="policy", policy_id=policy, policy_version=version)
            db.execute("UPDATE idempotency_requests SET response_json=? WHERE id=?", (scope.model_dump_json(), request_id))
            return scope

    def claim_policy(self, actor: Operator, claim_id: UUID):
        with self._connection() as db:
            row, facts = self._confirmed_claim(db, actor, claim_id)
            link = db.execute("SELECT * FROM claim_policy_bindings WHERE claim_id=?", (str(claim_id),)).fetchone()
            scope = (
                Scope(document_type="policy", policy_id=link["policy_id"], policy_version=link["policy_version"])
                if link and facts.get("policy_id") == link["policy_id"] else None
            )
            return scope, row["claimant_id"], facts
