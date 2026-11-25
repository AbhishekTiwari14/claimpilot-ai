from datetime import timedelta
import json
from uuid import UUID, uuid4

from claimpilot.storage.intake import IntakeError
from claimpilot.storage.workflows import WorkflowStore

MAX_ATTEMPTS = 3
DISPATCH_MIGRATION = (
    "ALTER TABLE workflow_decisions RENAME TO workflow_decisions_v5",
    """CREATE TABLE workflow_decisions (
        workflow_id TEXT NOT NULL, proposal_id TEXT PRIMARY KEY REFERENCES dispatch_proposals(id),
        reviewer_id TEXT NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
        reason TEXT NOT NULL, decided_at TEXT NOT NULL
    )""",
    "INSERT INTO workflow_decisions SELECT * FROM workflow_decisions_v5",
    "DROP TABLE workflow_decisions_v5",
    """CREATE TABLE dispatches (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, claim_id TEXT NOT NULL REFERENCES claims(id),
        workflow_id TEXT NOT NULL, proposal_id TEXT NOT NULL UNIQUE REFERENCES workflow_decisions(proposal_id),
        proposal_version INTEGER NOT NULL, claim_version INTEGER NOT NULL, contractor_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('awaiting_acknowledgement','accepted','rejected','timed_out')),
        sent_at TEXT NOT NULL, acknowledgement_deadline TEXT NOT NULL, resolved_at TEXT,
        sla_json TEXT NOT NULL, policy_citations_json TEXT NOT NULL,
        UNIQUE(claim_id, contractor_id)
    )""",
    """CREATE UNIQUE INDEX dispatch_active_claim ON dispatches(claim_id)
       WHERE status IN ('awaiting_acknowledgement','accepted')""",
)


class DispatchStore(WorkflowStore):
    @staticmethod
    def _failed(db, actor, claim_id):
        return frozenset(row[0] for row in db.execute(
            """SELECT contractor_id FROM dispatches
               WHERE tenant_id=? AND claim_id=? AND status IN ('rejected','timed_out')""",
            (actor.tenant_id, str(claim_id)),
        ))

    def failed(self, actor, claim_id):
        with self._connection() as db:
            self._snapshot(db, actor, claim_id)
            return self._failed(db, actor, claim_id)

    def send(self, actor, state, now):
        claim_id = UUID(state["claim_id"])
        with self._connection(write=True) as db:
            snapshot = self._snapshot(db, actor, claim_id)
            existing = db.execute("SELECT id FROM dispatches WHERE proposal_id=? AND workflow_id=?",
                                  (state["proposal_id"], state["workflow_id"])).fetchone()
            if existing:
                return existing["id"]
            row = db.execute("SELECT * FROM dispatch_proposals WHERE claim_id=? ORDER BY version DESC LIMIT 1",
                             (str(claim_id),)).fetchone()
            if not row or row["id"] != state["proposal_id"] or row["version"] != state["proposal_version"]:
                raise IntakeError(409, "stale_proposal", "The approved proposal is no longer current.")
            approved = db.execute(
                "SELECT 1 FROM workflow_decisions WHERE workflow_id=? AND proposal_id=? AND decision='approve'",
                (state["workflow_id"], row["id"]),
            ).fetchone()
            if not approved:
                raise IntakeError(409, "approval_required", "The exact proposal requires operator approval.")
            proposal = self._proposal(db, actor, row, snapshot, now=now)
            if (proposal.status != "awaiting_approval" or snapshot.fingerprint != state["fingerprint"]
                or proposal.requirements is None or proposal.recommended_contractor_id is None):
                raise IntakeError(409, "stale_proposal", "The approved proposal is no longer eligible.")
            failed = self._failed(db, actor, claim_id)
            if len(failed) >= MAX_ATTEMPTS or proposal.recommended_contractor_id in failed:
                raise IntakeError(409, "reassignment_unavailable", "The contractor or attempt limit prevents dispatch.")
            if db.execute("SELECT 1 FROM dispatches WHERE claim_id=? AND status IN ('awaiting_acknowledgement','accepted')",
                          (str(claim_id),)).fetchone():
                raise IntakeError(409, "active_dispatch_exists", "The claim already has an active or accepted dispatch.")
            dispatch_id = str(uuid4())
            deadline = now + timedelta(minutes=proposal.requirements.acknowledgement_limit_minutes)
            db.execute("INSERT INTO dispatches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                dispatch_id, actor.tenant_id, str(claim_id), state["workflow_id"], row["id"], row["version"],
                proposal.claim_version, proposal.recommended_contractor_id, "awaiting_acknowledgement",
                now.isoformat(), deadline.isoformat(), None, proposal.requirements.model_dump_json(),
                json.dumps(state["policy_citations"]),
            ))
            return dispatch_id

    def resolve_dispatch(self, actor, state, action, now):
        with self._connection(write=True) as db:
            row = db.execute("""SELECT * FROM dispatches WHERE id=? AND tenant_id=? AND claim_id=? AND workflow_id=?""",
                             (state["dispatch_id"], actor.tenant_id, state["claim_id"], state["workflow_id"])).fetchone()
            if row["status"] != "awaiting_acknowledgement":
                return row["status"]
            status = "timed_out" if now.isoformat() >= row["acknowledgement_deadline"] else action
            if status not in ("accepted", "rejected", "timed_out"):
                raise ValueError("SLA processing is only invoked once due.")
            db.execute("UPDATE dispatches SET status=?, resolved_at=? WHERE id=?", (status, now.isoformat(), row["id"]))
            return status

    def dispatch_history(self, actor, claim_id, workflow_id):
        with self._connection() as db:
            self._snapshot(db, actor, claim_id)
            rows = db.execute(
                """SELECT d.*, a.reviewer_id, a.decided_at, a.reason AS decision_reason, a.decision
                   FROM dispatches d JOIN workflow_decisions a ON a.proposal_id=d.proposal_id
                   WHERE d.tenant_id=? AND d.claim_id=? AND d.workflow_id=? ORDER BY d.rowid""",
                (actor.tenant_id, str(claim_id), str(workflow_id)),
            )
            history = []
            for row in rows:
                record = dict(row)
                record.pop("tenant_id")
                record["sla"] = json.loads(record.pop("sla_json"))
                record["policy_citations"] = json.loads(record.pop("policy_citations_json"))
                record["sla_outcome"] = {
                    "accepted": "met", "rejected": "declined", "timed_out": "breached",
                    "awaiting_acknowledgement": "pending",
                }[row["status"]]
                history.append(record)
            return history
