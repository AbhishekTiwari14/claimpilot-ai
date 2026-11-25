from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi.testclient import TestClient
import pytest

from claimpilot.main import create_app
from claimpilot.storage.dispatch import DispatchStore
from examples.contractors import CUSTOMER, OPERATOR, checked, create_claim, fixture_embed, seed_handbook
from examples.workflows import bind_policy, decision
from test_workflows import OTHER, change_record, policy_evidence, ready, resume, settings, start  # noqa: F401


@pytest.fixture
def clock():
    now = [datetime.now(timezone.utc)]
    return now


@pytest.fixture
def api(settings, clock):
    with TestClient(create_app(settings, embed=fixture_embed, embedding_id="dispatch-test", clock=lambda: clock[0])) as client:
        seed_handbook(client)
        yield client


def post(api, path, key, data=None, headers=OPERATOR):
    return api.post(path, headers=headers | {"Idempotency-Key": key}, json=data)


def send(api, url, state, key="send", headers=OPERATOR):
    return post(api, f"{url}/dispatch", key, {
        "proposal_id": state["proposal_id"], "proposal_version": state["proposal_version"],
    }, headers)


def approve(api, url, state, key="approve"):
    return checked(resume(api, url, decision(state), key))


def test_acceptance_requires_approval_and_prevents_duplicate_dispatch(api, policy_evidence, settings):
    _, url, proposal = ready(api)
    assert send(api, url, proposal, headers=CUSTOMER).status_code == 403
    assert send(api, url, proposal, headers=OTHER).status_code == 404
    assert send(api, url, proposal).status_code == 409
    approved = approve(api, url, proposal)
    assert send(api, url, approved | {"proposal_version": 100}).status_code == 409
    sent = checked(send(api, url, approved))
    assert checked(send(api, url, approved)) == sent
    assert send(api, url, approved, key="duplicate").status_code == 409
    row, = sent["dispatches"]
    assert row["contractor_id"] == "CT-001" and row["decision"] == "approve"
    assert row["sla"]["provenance"] == "validated_operations"
    assert row["sla"]["sources"] and row["policy_citations"]
    path = f"{url}/dispatch/{row['id']}/response"
    final = checked(post(api, path, "accept", {"outcome": "accepted"}))
    assert final["workflow"]["status"] == "accepted"
    assert final["dispatches"][0]["sla_outcome"] == "met"
    assert checked(post(api, path, "accept", {"outcome": "accepted"})) == final
    assert post(api, path, "accept", {"outcome": "rejected"}).status_code == 409
    with sqlite3.connect(settings.claimpilot_intake_db) as db:
        assert db.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["rejected", "timed_out"])
def test_failure_reassigns_and_requires_fresh_approval(api, policy_evidence, clock, failure):
    _, url, proposal = ready(api)
    first = checked(send(api, url, approve(api, url, proposal)))["dispatches"][0]
    path = f"{url}/dispatch/{first['id']}"
    if failure == "timed_out":
        early = checked(post(api, f"{path}/sla", "sla"))
        assert early["workflow"]["status"] == "awaiting_acknowledgement"
        clock[0] += timedelta(hours=4)
        replacement = checked(post(api, f"{path}/sla", "sla"))
        assert checked(post(api, f"{path}/sla", "sla")) == replacement
    else:
        replacement = checked(post(api, f"{path}/response", "reject", {"outcome": "rejected"}))
    assert replacement["dispatches"][0]["status"] == failure
    state = replacement["workflow"]
    assert state["proposal_version"] > proposal["proposal_version"] and state["status"] == "awaiting_approval"
    assert send(api, url, state, "send-second").status_code == 409
    approved = approve(api, url, state, "approve-second")
    second = checked(send(api, url, approved, "send-second"))["dispatches"][-1]
    assert second["contractor_id"] == "CT-002" and second["proposal_id"] != first["proposal_id"]


def test_no_alternative_escalates(api, policy_evidence, settings):
    _, url, proposal = ready(api)
    approved = approve(api, url, proposal)
    first = checked(send(api, url, approved))["dispatches"][0]
    change_record(settings, "CT-002", available=False)
    final = checked(post(api, f"{url}/dispatch/{first['id']}/response", "reject", {"outcome": "rejected"}))
    assert final["workflow"]["status"] == "requires_intervention"
    assert final["workflow"]["reason"] == "no_eligible_contractors"


def test_stale_after_approval_and_synthetic_sla_defaults(policy_evidence, settings, clock):
    with TestClient(create_app(settings, embed=fixture_embed, embedding_id="dispatch-test", clock=lambda: clock[0])) as api:
        claim = create_claim(api, {"case": "default", "zip": "98101"})
        bind_policy(api, claim["claim_id"])
        url, state = start(api, claim)
        approved = approve(api, url, state)
        clock[0] += timedelta(minutes=16)
        blocked = checked(send(api, url, approved))
        assert blocked["workflow"]["reason"] == "stale_proposal" and not blocked["dispatches"]
        clock[0] -= timedelta(minutes=16)
        retry = checked(resume(api, url, {"pause_id": blocked["workflow"]["pause_id"], "action": "retry",
                                         "expected_version": state["claim_version"]}, "retry"))
        result = checked(send(api, url, approve(api, url, retry, "approve-fresh"), "send-fresh"))
        sla = result["dispatches"][0]["sla"]
        assert (sla["provenance"], sla["sources"], sla["acknowledgement_limit_minutes"]) == (
            "synthetic_default", [], 240,
        )


def test_dispatch_commit_survives_checkpoint_failure(api, policy_evidence, monkeypatch):
    _, url, proposal = ready(api)
    approved = approve(api, url, proposal)
    original = DispatchStore.send

    def crash_after_send(self, *args):
        original(self, *args)
        raise RuntimeError("simulated interruption")

    with monkeypatch.context() as patch:
        patch.setattr(DispatchStore, "send", crash_after_send)
        assert send(api, url, approved).status_code == 503
    recovered = checked(send(api, url, approved))
    assert recovered["workflow"]["status"] == "awaiting_acknowledgement" and len(recovered["dispatches"]) == 1
