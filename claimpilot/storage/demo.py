from contextlib import closing
import re
import sqlite3

from claimpilot.storage.contractors import digest

WORKSPACE_PREFIX = "hosted-demo-"


def demo_workspaces(store):
    with store._connection() as db:
        return [
            row[0] for row in db.execute(
                "SELECT DISTINCT tenant_id FROM contractors WHERE tenant_id LIKE ?", (WORKSPACE_PREFIX + "%",),
            )
            if re.fullmatch(WORKSPACE_PREFIX + r"[0-9a-f]{32}", row[0])
        ]


def purge_workspace(store, index, tenant: str) -> None:
    # Purge projections first so the seeded contractor rows mark unfinished cleanup after a crash.
    index.purge_workspace(tenant)
    path = store.path.with_suffix(".checkpoints.sqlite3")
    if path.is_file():
        with closing(sqlite3.connect(path, timeout=5)) as db, db:
            db.execute("PRAGMA secure_delete = ON")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("writes", "checkpoints"):
                if table in tables:
                    db.execute(f"DELETE FROM {table} WHERE thread_id LIKE ?", (digest(tenant) + ":%",))

    claims = "(SELECT id FROM claims WHERE tenant_id=?)"
    with store._connection(write=True) as db:
        db.execute("PRAGMA secure_delete = ON")
        for table in ("dispatches", "idempotency_requests"):
            db.execute(f"DELETE FROM {table} WHERE tenant_id=?", (tenant,))
        db.execute("""DELETE FROM knowledge_chunks WHERE document_id IN
                      (SELECT id FROM knowledge_documents WHERE tenant_id=?)""", (tenant,))
        db.execute(f"DELETE FROM workflow_decisions WHERE proposal_id IN "
                   f"(SELECT id FROM dispatch_proposals WHERE claim_id IN {claims})", (tenant,))
        db.execute(f"DELETE FROM photo_analysis_images WHERE analysis_id IN "
                   f"(SELECT id FROM photo_analyses WHERE claim_id IN {claims})", (tenant,))
        for table in ("dispatch_proposals", "contractor_runs", "claim_policy_bindings",
                      "photo_analyses", "claim_photos", "claim_messages"):
            db.execute(f"DELETE FROM {table} WHERE claim_id IN {claims}", (tenant,))
        for table in ("knowledge_documents", "claims", "contractors"):
            db.execute(f"DELETE FROM {table} WHERE tenant_id=?", (tenant,))
