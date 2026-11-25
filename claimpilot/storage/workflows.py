from uuid import UUID, uuid4

from claimpilot.storage.contractors import ContractorStore
from claimpilot.storage.intake import IntakeError

WORKFLOW_SCHEMA = """
CREATE TABLE workflow_decisions (
    workflow_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES dispatch_proposals(id),
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
    reason TEXT NOT NULL,
    decided_at TEXT NOT NULL
)
"""


class WorkflowStore(ContractorStore):
    def start_workflow(self, actor, claim_id, key, version) -> str:
        with self._connection(write=True) as db:
            snapshot = self._snapshot(db, actor, claim_id)
            saved, request_id = self._request(
                db, self._operator(actor), f"workflow:start:{claim_id}", key, str(version),
            )
            if saved is not None:
                return saved["response_json"]
            if snapshot.claim.version != version:
                raise IntakeError(409, "stale_version", "Refresh the claim before starting.")
            workflow_id = str(uuid4())
            db.execute("UPDATE idempotency_requests SET response_json=? WHERE id=?", (workflow_id, request_id))
            return workflow_id

    def reserve_resume(self, actor, workflow_id, key, payload, matches_pause: bool) -> bool:
        with self._connection(write=True) as db:
            saved, _ = self._request(
                db, self._operator(actor), f"workflow:resume:{workflow_id}", key, payload.model_dump_json(),
            )
            if saved is None and not matches_pause:
                raise IntakeError(409, "stale_pause", "Refresh the workflow before resuming.")
            return saved is not None

    def decide(self, actor, state, choice) -> str:
        claim_id = UUID(state["claim_id"])
        with self._connection(write=True) as db:
            snapshot = self._snapshot(db, actor, claim_id)
            receipt = db.execute("SELECT decision FROM workflow_decisions WHERE workflow_id=? AND proposal_id=?",
                                 (state["workflow_id"], state["proposal_id"])).fetchone()
            if receipt:
                return receipt["decision"]
            row = db.execute(
                """SELECT * FROM dispatch_proposals WHERE claim_id=? ORDER BY version DESC LIMIT 1""",
                (str(claim_id),),
            ).fetchone()
            if row is None or row["id"] != state["proposal_id"] or row["version"] != state["proposal_version"]:
                return "stale_proposal"
            if choice["action"] == "approve":
                current = self._proposal(db, actor, row, snapshot)
                if current.status != "awaiting_approval" or snapshot.fingerprint != state["fingerprint"]:
                    return "stale_proposal"
            taken = db.execute("SELECT 1 FROM workflow_decisions WHERE proposal_id=?", (row["id"],)).fetchone()
            if taken:
                return "proposal_already_decided"
            db.execute("""INSERT INTO workflow_decisions
                       (workflow_id,proposal_id,reviewer_id,decision,reason,decided_at) VALUES (?,?,?,?,?,?)""", (
                state["workflow_id"], row["id"], choice["reviewer_id"], choice["action"],
                choice["reason"], self.now().isoformat(),
            ))
            return choice["action"]
