from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid4

from claimpilot.schemas.contractors import AgentTrace, Contractor, Proposal, RunView, Trade
from claimpilot.schemas.conversation import ClaimState
from claimpilot.schemas.knowledge import Operator
from claimpilot.schemas.photos import DamageReport, PhotoObservation
from claimpilot.storage.intake import IntakeError
from claimpilot.storage.knowledge import KnowledgeStore

CONTRACTOR_SCHEMA = """
CREATE TABLE contractors (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id,id)
);
CREATE TABLE contractor_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    claim_version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','completed')),
    trace_json TEXT
);
CREATE TABLE dispatch_proposals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES contractor_runs(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    version INTEGER NOT NULL,
    proposal_json TEXT NOT NULL,
    claim_fingerprint TEXT NOT NULL,
    contractor_fingerprints TEXT NOT NULL,
    UNIQUE(claim_id,version)
);
CREATE INDEX contractor_runs_claim ON contractor_runs(tenant_id,claim_id);
"""
SEED = Path(__file__).resolve().parents[2] / "data" / "contractors" / "synthetic.json"


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def eligible(contractor: Contractor, zip_code: str, trade: Trade, now: datetime) -> bool:
    hour = now.astimezone(timezone.utc).hour
    return (
        contractor.active and contractor.available and contractor.capacity > 0
        and contractor.credentialed and contractor.insured
        and contractor.opens_utc <= hour < contractor.closes_utc
        and zip_code in contractor.zip_codes and trade in contractor.specializations
    )


@dataclass(frozen=True)
class ClaimSnapshot:
    claim: ClaimState
    facts: dict
    photos: list[PhotoObservation]
    binding: bool
    fingerprint: str


class ContractorStore(KnowledgeStore):
    def __init__(self, path: Path, *, clock=None) -> None:
        super().__init__(path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    def seed(self, tenants: set[str]) -> None:
        if not tenants:
            return
        records = [
            Contractor.model_validate(record) for record in json.loads(SEED.read_text(encoding="utf-8"))
        ]
        with self._connection(write=True) as db:
            db.executemany(
                "INSERT OR IGNORE INTO contractors VALUES (?,?,?)",
                [(tenant, c.id, c.model_dump_json()) for tenant in sorted(tenants) for c in records],
            )

    def contractors(self, actor: Operator) -> list[Contractor]:
        with self._connection() as db:
            records = db.execute(
                "SELECT record_json FROM contractors WHERE tenant_id=? ORDER BY id", (actor.tenant_id,)
            )
            return [Contractor.model_validate_json(row["record_json"]) for row in records]

    @staticmethod
    def _contractor(db, tenant: str, contractor_id: str) -> Contractor:
        row = db.execute(
            "SELECT record_json FROM contractors WHERE tenant_id=? AND id=?", (tenant, contractor_id)
        ).fetchone()
        if row is None:
            raise IntakeError(404, "contractor_not_found", "Contractor not found.")
        return Contractor.model_validate_json(row["record_json"])

    def contractor(self, actor: Operator, contractor_id: str) -> Contractor:
        with self._connection() as db:
            return self._contractor(db, actor.tenant_id, contractor_id)

    @staticmethod
    def _snapshot(db, actor: Operator, claim_id: UUID) -> ClaimSnapshot:
        row = db.execute(
            "SELECT state_json,claimant_id FROM claims WHERE tenant_id=? AND id=?", (actor.tenant_id, str(claim_id))
        ).fetchone()
        if row is None:
            raise IntakeError(404, "not_found", "Claim not found.")
        claim = ClaimState.model_validate_json(row["state_json"])
        facts = {key: item.value for key, item in claim.facts.items() if item.status == "customer_confirmed"}
        analyses = db.execute(
            """SELECT id,result_json FROM photo_analyses WHERE claim_id=? AND status='completed'
               ORDER BY rowid DESC LIMIT 20""",
            (str(claim_id),),
        ).fetchall()
        binding = db.execute(
            "SELECT policy_id,policy_version FROM claim_policy_bindings WHERE claim_id=?", (str(claim_id),)
        ).fetchone()
        policy_documents = db.execute(
            """SELECT id,generation FROM knowledge_documents WHERE tenant_id=? AND document_type='policy'
               AND policy_id=? AND policy_version=? AND subject_claimant_id IN ('',?)
               AND deleted_at IS NULL AND status='ready' ORDER BY id""",
            (actor.tenant_id, binding["policy_id"], binding["policy_version"], row["claimant_id"]),
        ).fetchall() if binding else []
        seen = {}
        for analysis in analyses:
            for observation in DamageReport.model_validate_json(analysis["result_json"]).observations:
                seen.setdefault(observation.image_id, observation)
        fingerprint = digest(json.dumps([
            claim.version, claim.status, facts, [entry["id"] for entry in analyses],
            list(binding) if binding else None, [list(doc) for doc in policy_documents],
        ], sort_keys=True))
        return ClaimSnapshot(claim, facts, list(seen.values()), binding is not None, fingerprint)

    def snapshot(self, actor: Operator, claim_id: UUID) -> ClaimSnapshot:
        with self._connection() as db:
            return self._snapshot(db, actor, claim_id)

    @staticmethod
    def _run(db, actor: Operator, run_id: UUID, claim_id: UUID):
        row = db.execute(
            "SELECT * FROM contractor_runs WHERE tenant_id=? AND id=? AND claim_id=?",
            (actor.tenant_id, str(run_id), str(claim_id)),
        ).fetchone()
        if row is None:
            raise IntakeError(404, "agent_run_not_found", "Recommendation run not found.")
        return row

    def reserve_run(self, actor: Operator, claim_id: UUID, key: str, expected_version: int, *, provider: str, model: str):
        with self._connection(write=True) as db:
            snapshot = self._snapshot(db, actor, claim_id)
            saved, request_id = self._request(
                db, self._operator(actor), f"contractor:run:{claim_id}", key, str(expected_version),
            )
            if saved is not None:
                run_id = UUID(saved["response_json"])
                row = self._run(db, actor, run_id, claim_id)
                if row["status"] == "completed":
                    return self._run_view(db, actor, row, snapshot)
                if row["provider"] != provider or row["model"] != model:
                    raise IntakeError(409, "provider_changed", "Use a new key for a different provider.")
            else:
                run_id = uuid4()
                if snapshot.claim.version != expected_version:
                    raise IntakeError(409, "stale_version", "Refresh the claim before requesting a recommendation.")
                db.execute(
                    """INSERT INTO contractor_runs (id,tenant_id,claim_id,provider,model,claim_version,status)
                       VALUES (?,?,?,?,?,?,'running')""",
                    (str(run_id), actor.tenant_id, str(claim_id), provider, model, expected_version),
                )
                db.execute(
                    "UPDATE idempotency_requests SET response_json=? WHERE id=?", (str(run_id), request_id)
                )
            return run_id, snapshot

    @staticmethod
    def _requirements_active(db, actor: Operator, proposal: Proposal) -> bool:
        if proposal.requirements is None:
            return False
        if proposal.requirements.provenance == "synthetic_default":
            return not proposal.requirements.sources
        return bool(proposal.requirements.sources) and all(db.execute(
            """SELECT 1 FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id
               AND c.generation=d.generation WHERE c.chunk_id=? AND d.tenant_id=?
               AND d.document_type='operations' AND d.status='ready' AND d.deleted_at IS NULL""",
            (source.chunk_id, actor.tenant_id),
        ).fetchone() is not None for source in proposal.requirements.sources)

    def _invalidate(self, db, actor: Operator, proposal: Proposal, snapshot: ClaimSnapshot,
                    claim_fingerprint: str, fingerprints: dict[str, str], *, now=None) -> Proposal:
        if proposal.status != "awaiting_approval":
            return proposal
        from claimpilot.services.contractor_urgency import service_zip

        zip_code = service_zip(str(snapshot.facts.get("property_location", "")))
        now = now if now is not None else self.now()
        stale = (
            snapshot.fingerprint != claim_fingerprint
            or snapshot.claim.version != proposal.claim_version
            or now >= proposal.created_at + timedelta(minutes=15)
            or not self._requirements_active(db, actor, proposal)
            or not zip_code or not proposal.required_specialization
        )
        if not stale:
            for contractor_id, fingerprint in fingerprints.items():
                try:
                    contractor = self._contractor(db, actor.tenant_id, contractor_id)
                except IntakeError:
                    stale = True
                    break
                if (digest(contractor.model_dump_json()) != fingerprint
                    or not eligible(contractor, zip_code, proposal.required_specialization, now)):
                    stale = True
                    break
        if stale:
            return proposal.model_copy(update={
                "status": "requires_intervention", "stale": True,
                "explanation": "The claim, contractor, operating window, or cited requirements changed. Re-evaluation is required.",
            })
        return proposal

    def _proposal(self, db, actor: Operator, row, snapshot: ClaimSnapshot, *, now=None) -> Proposal:
        stored = Proposal.model_validate_json(row["proposal_json"])
        current = self._invalidate(db, actor, stored, snapshot, row["claim_fingerprint"],
                                   json.loads(row["contractor_fingerprints"]), now=now)
        if current.stale and not stored.stale:
            db.execute("UPDATE dispatch_proposals SET proposal_json=? WHERE id=? AND proposal_json=?",
                       (current.model_dump_json(), row["id"], row["proposal_json"]))
        return current

    def _run_view(self, db, actor: Operator, row, snapshot: ClaimSnapshot) -> RunView:
        proposal = db.execute("SELECT * FROM dispatch_proposals WHERE run_id=?", (row["id"],)).fetchone()
        return RunView(
            id=row["id"], claim_id=row["claim_id"], status=row["status"], provider=row["provider"], model=row["model"],
            proposal=self._proposal(db, actor, proposal, snapshot) if proposal else None,
        )

    def finish_run(self, actor: Operator, proposal: Proposal, trace: AgentTrace, snapshot: ClaimSnapshot,
               fingerprints: dict[str, str]) -> RunView:
        with self._connection(write=True) as db:
            row = self._run(db, actor, proposal.run_id, proposal.claim_id)
            current = self._snapshot(db, actor, proposal.claim_id)
            if row["status"] == "completed":
                return self._run_view(db, actor, row, current)
            version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM dispatch_proposals WHERE claim_id=?",
                (str(proposal.claim_id),),
            ).fetchone()[0]
            proposal.version = version
            proposal = self._invalidate(db, actor, proposal, current, snapshot.fingerprint, fingerprints)
            trace.recommendation_status = proposal.status
            trace.decision_summary = proposal.explanation
            db.execute(
                "INSERT INTO dispatch_proposals VALUES (?,?,?,?,?,?,?)",
                (str(proposal.id), str(proposal.run_id), str(proposal.claim_id), proposal.version,
                 proposal.model_dump_json(), snapshot.fingerprint, json.dumps(fingerprints)),
            )
            db.execute("UPDATE contractor_runs SET status='completed',trace_json=? WHERE id=?",
                       (trace.model_dump_json(), str(trace.run_id)))
            return self._run_view(db, actor, self._run(db, actor, proposal.run_id, proposal.claim_id), current)

    def run(self, actor: Operator, claim_id: UUID, run_id: UUID) -> RunView:
        with self._connection() as db:
            row = self._run(db, actor, run_id, claim_id)
            return self._run_view(db, actor, row, self._snapshot(db, actor, claim_id))

    def trace(self, actor: Operator, claim_id: UUID, run_id: UUID) -> AgentTrace:
        with self._connection() as db:
            row = self._run(db, actor, run_id, claim_id)
            return AgentTrace.model_validate_json(row["trace_json"]) if row["trace_json"] else AgentTrace(run_id=run_id)

    def proposals(self, actor: Operator, claim_id: UUID) -> list[Proposal]:
        with self._connection() as db:
            snapshot = self._snapshot(db, actor, claim_id)
            rows = db.execute(
                "SELECT * FROM dispatch_proposals WHERE claim_id=? ORDER BY version DESC LIMIT 50",
                (str(claim_id),),
            )
            return [self._proposal(db, actor, row, snapshot) for row in rows]
