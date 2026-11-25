import sqlite3

from fastapi.testclient import TestClient
import pytest

from claimpilot.main import create_app
from claimpilot.services import knowledge_answers
from claimpilot.storage.workflows import WorkflowStore
from examples.contractors import CUSTOMER, OPERATOR, checked, create_claim, fixture_embed
from examples.workflows import bind_policy, decision
from test_contractors import OTHER, api, change_record, settings  # noqa: F401


def start(api, claim, key="workflow"):
    base = f"/api/v1/claims/{claim['claim_id']}/workflows"
    result = checked(api.post(base, headers=OPERATOR | {"Idempotency-Key": key},
                              json={"expected_version": claim["version"]}))
    return f"{base}/{result['workflow_id']}", result


def resume(api, url, payload, key="resume", headers=OPERATOR):
    return api.post(f"{url}/resume", headers=headers | {"Idempotency-Key": key}, json=payload)


@pytest.fixture
def policy_evidence(monkeypatch):
    original = knowledge_answers._fixture

    def fixture(query, evidence, assessment):
        for number, source in enumerate(evidence, 1):
            if assessment and source.section == "A2 Sudden indoor water discharge":
                return {"outcome": "potentially_covered", "sources": [
                    {"source_number": number, "category": "coverage"},
                ]}
        return original(query, evidence, assessment)

    monkeypatch.setattr(knowledge_answers, "_fixture", fixture)


def ready(api):
    claim = create_claim(api, {"case": "ready", "zip": "98101"})
    bind_policy(api, claim["claim_id"])
    url, state = start(api, claim)
    assert state["status"] == "awaiting_approval", state
    assert state["policy_citations"] and state["dispatch_citations"]
    assert [step["node"] for step in state["transitions"]] == ["intake", "policy", "contractor"]
    return claim, url, state


def test_checkpoint_restart_approval_and_idempotency(api, settings, policy_evidence):
    claim, url, state = ready(api)
    assert start(api, claim)[1] == state
    with TestClient(create_app(settings, embed=fixture_embed, embedding_id="contractor-test-lexical")) as restarted:
        assert checked(restarted.get(url, headers=OPERATOR)) == state
        approved = checked(resume(restarted, url, decision(state)))
        assert approved["status"] == "approved" and approved["pause_id"]
        assert checked(resume(restarted, url, decision(state))) == approved
        assert resume(restarted, url, decision(state, "reject")).status_code == 409
        assert resume(restarted, url, decision(state), key="already-consumed").status_code == 409
    with sqlite3.connect(settings.claimpilot_intake_db) as db:
        assert db.execute("SELECT decision, reviewer_id FROM workflow_decisions").fetchall() == [("approve", "ops")]
        assert db.execute("SELECT COUNT(*) FROM contractor_runs").fetchone()[0] == 1


def test_rejection_and_workspace_isolation(api, settings, policy_evidence):
    _, url, state = ready(api)
    assert api.get(url, headers=OTHER).status_code == 404
    assert api.get(url, headers=CUSTOMER).status_code == 403
    assert resume(api, url, decision(state), headers=OTHER).status_code == 404
    assert resume(api, url, decision(state) | {"proposal_version": 999}).status_code == 409
    final = checked(resume(api, url, decision(state, "reject")))
    assert final["status"] == "rejected"
    with sqlite3.connect(settings.claimpilot_intake_db) as db:
        assert db.execute("SELECT decision FROM workflow_decisions").fetchall() == [("reject",)]


def test_stale_proposal_returns_to_review(api, settings, policy_evidence):
    _, url, state = ready(api)
    change_record(settings, "CT-001", available=False)
    final = checked(resume(api, url, decision(state)))
    assert final["status"] == "requires_intervention" and final["reason"] == "stale_proposal"
    assert final["pause_id"] and final["pause_id"] != state["pause_id"]
    with sqlite3.connect(settings.claimpilot_intake_db) as db:
        assert db.execute("SELECT COUNT(*) FROM workflow_decisions").fetchone()[0] == 0


def test_missing_intake_can_resume_after_existing_intake_api_updates_claim(api, policy_evidence):
    claim = checked(api.post("/api/v1/conversations", headers=CUSTOMER | {"Idempotency-Key": "ready-start"}))["claim"]
    url, state = start(api, claim)
    assert state["status"] == "awaiting_information" and "damage_type" in state["missing_fields"]
    completed = create_claim(api, {"case": "ready", "zip": "98101"})
    assert completed["claim_id"] == claim["claim_id"]
    bind_policy(api, completed["claim_id"])
    payload = {"pause_id": state["pause_id"], "action": "retry", "expected_version": completed["version"]}
    final = checked(resume(api, url, payload))
    assert final["status"] == "awaiting_approval"
    assert checked(resume(api, url, payload)) == final


@pytest.mark.parametrize(("scenario", "reason"), [
    ({"emergency": True}, "immediate_hazard"),
    ({"zip": "98101"}, "insufficient_evidence"),
    ({"zip": "99501"}, "no_eligible_contractors"),
])
def test_conditional_intervention(api, request, scenario, reason):
    if reason == "no_eligible_contractors":
        request.getfixturevalue("policy_evidence")
    claim = create_claim(api, {"case": "routing", **scenario})
    if not scenario.get("emergency"):
        bind_policy(api, claim["claim_id"])
    _, state = start(api, claim)
    assert (state["status"], state["reason"]) == ("requires_intervention", reason)
    assert state["pause_id"] and state["proposal_id"] is None


def test_resume_after_decision_commit_before_checkpoint(api, settings, policy_evidence, monkeypatch):
    _, url, state = ready(api)
    original = WorkflowStore.decide

    def crash_after_commit(self, *args):
        original(self, *args)
        raise RuntimeError("Simulated process interruption")

    with monkeypatch.context() as patch:
        patch.setattr(WorkflowStore, "decide", crash_after_commit)
        assert resume(api, url, decision(state)).status_code == 503
    with TestClient(create_app(settings, embed=fixture_embed, embedding_id="contractor-test-lexical")) as restarted:
        assert checked(resume(restarted, url, decision(state)))["status"] == "approved"
    with sqlite3.connect(settings.claimpilot_intake_db) as db:
        assert db.execute("SELECT COUNT(*) FROM workflow_decisions").fetchone()[0] == 1
