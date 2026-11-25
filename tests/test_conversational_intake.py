from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from claimpilot.agents.intake_fixtures import (
    CLARIFICATION, CONTRADICTION, CORRECTION, DEMO_TURNS, HAZARD, INJECTION,
)
from claimpilot.config import ClaimantCredential, Settings
from claimpilot.main import create_app
from claimpilot.schemas.conversation import Claimant, MessageRequest
from claimpilot.schemas.intake import service_zip
from claimpilot.services.llm import LLMError, LLMService
from claimpilot.storage.intake import PendingTurn

API = "/api/v1"
HEADERS = {"Authorization": "Bearer owner-synthetic-key"}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, claimpilot_llm_provider="offline",
        claimpilot_intake_db=tmp_path / "intake.sqlite3",
        claimpilot_claimants=[
            ClaimantCredential(tenant_id="tenant-1", claimant_id="owner", token="owner-synthetic-key"),
            ClaimantCredential(tenant_id="tenant-1", claimant_id="other", token="other-synthetic-key"),
            ClaimantCredential(tenant_id="tenant-2", claimant_id="owner", token="tenant-2-synthetic-key"),
        ],
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), headers=HEADERS) as api:
        yield api


def start(client, key="start", headers=None):
    response = client.post(f"{API}/conversations", headers={"Idempotency-Key": key, **(headers or {})})
    assert response.status_code == 201, response.text
    return response.json()


def send(client, turn, message, key=None):
    claim = turn["claim"]
    return client.post(
        f"{API}/conversations/{claim['conversation_id']}/messages",
        headers={"Idempotency-Key": key or f"turn-{claim['version']}"},
        json={"message": message, "expected_version": claim["version"]},
    )


def advance(client, turn, message):
    response = send(client, turn, message)
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, turn, *, key="confirm", value=True, version=None):
    claim = turn["claim"]
    return client.post(
        f"{API}/claims/{claim['claim_id']}/confirm",
        headers={"Idempotency-Key": key},
        json={"confirmed": value, "expected_version": claim["version"] if version is None else version},
    )


def history(client, turn):
    return client.get(f"{API}/conversations/{turn['claim']['conversation_id']}").json()["messages"]


def ready(client):
    turn = start(client)
    for message in DEMO_TURNS:
        turn = advance(client, turn, message)
    return turn


def test_complete_multi_turn_claim_requires_explicit_confirmation(client):
    turn = ready(client)
    draft = turn["claim"]
    assert draft["status"] == "awaiting_customer_confirmation"
    assert draft["missing_fields"] == []
    assert draft["summary"]["policy_id"] == "SYN-123"
    assert draft["summary"]["affected_areas"] == ["kitchen"]
    assert draft["confirmed_at"] is None
    assert all(fact["status"] == "candidate" for fact in draft["facts"].values())

    response = confirm(client, turn)
    assert response.status_code == 200
    final = response.json()["claim"]
    assert final["status"] == "intake_completed"
    assert final["summary"] == draft["summary"]
    assert final["confirmed_at"]
    assert final["version"] == draft["version"] + 1
    assert all(fact["status"] == "customer_confirmed" for fact in final["facts"].values())
    assert len(history(client, turn)) == 13
    assert send(client, response.json(), CORRECTION).status_code == 409


def test_follow_ups_focus_on_missing_fields_and_use_context(client):
    turn = advance(client, start(client), DEMO_TURNS[0])
    assert turn["claim"]["status"] == "collecting_information"
    assert "damage_type" not in turn["claim"]["missing_fields"]
    assert "affected_areas" not in turn["claim"]["missing_fields"]
    assert "location" in turn["reply"]["text"]
    turn = advance(client, turn, DEMO_TURNS[1])
    assert "kitchen" in turn["reply"]["text"]
    assert "when" in turn["reply"]["text"].lower()
    assert "property_location" not in turn["claim"]["missing_fields"]
    assert turn["claim"]["summary"] is None


@pytest.mark.parametrize("location", ["kitchen", "12345 Example Lane", "Seattle", "9810", "98101 or 99501"])
def test_invalid_location_requires_zip_before_confirmation(client, monkeypatch, location):
    from claimpilot.api import intake

    original = intake.extract_claim

    def extract(message, *args, **kwargs):
        result = original(message, *args, **kwargs)
        return (result.model_copy(update={"property_location": location})
                if message == DEMO_TURNS[0] else result)

    monkeypatch.setattr(intake, "extract_claim", extract)
    turn = start(client)
    for message in (DEMO_TURNS[0], *DEMO_TURNS[2:]):
        turn = advance(client, turn, message)
    assert turn["claim"]["facts"]["affected_areas"]["value"] == ["kitchen"]
    assert "property_location" in turn["claim"]["missing_fields"]
    assert turn["claim"]["status"] == "collecting_information" and turn["claim"]["summary"] is None
    assert "five-digit ZIP code" in turn["reply"]["text"]
    assert confirm(client, turn).json()["code"] == "not_ready_to_confirm"
    turn = advance(client, turn, "98101")
    assert service_zip(turn["claim"]["summary"]["property_location"]) == "98101"
    assert confirm(client, turn).json()["claim"]["status"] == "intake_completed"


def test_correction_updates_snapshot_and_invalidates_older_confirmation(client):
    original = ready(client)
    revised = advance(client, original, CORRECTION)
    claim = revised["claim"]
    assert claim["status"] == "awaiting_customer_confirmation"
    assert claim["summary"]["property_location"] == "14 Example Lane, ZIP 98101"
    assert claim["facts"]["policy_id"] == original["claim"]["facts"]["policy_id"]
    assert confirm(client, original).json()["code"] == "stale_version"
    assert confirm(client, revised).status_code == 200


def test_conflict_requests_clarification_and_preserves_both_sources(client):
    turn = ready(client)
    conflict = advance(client, turn, CONTRADICTION)
    claim = conflict["claim"]
    assert claim["status"] == "awaiting_clarification"
    assert claim["summary"] is None
    assert [e["value"] for e in claim["conflicts"]["property_location"]] == [
        "12 Example Lane, ZIP 98101", "16 Example Lane, ZIP 98101"
    ]
    assert "12 Example Lane" in conflict["reply"]["text"]
    assert "16 Example Lane" in conflict["reply"]["text"]
    assert confirm(client, conflict).json()["code"] == "not_ready_to_confirm"
    resolved = advance(client, conflict, CLARIFICATION)
    assert resolved["claim"]["status"] == "awaiting_customer_confirmation"
    assert resolved["claim"]["summary"]["property_location"] == "12 Example Lane, ZIP 98101"
    assert resolved["claim"]["conflicts"] == {}


@pytest.mark.parametrize("message", [
    HAZARD,
    "The house is on fire.",
    "There is water in the basement and exposed electrical wiring.",
])
def test_immediate_safety_does_not_wait_for_extraction(client, message):
    turn = advance(client, start(client), message)
    assert turn["claim"]["status"] == "human_assistance_required"
    assert turn["claim"]["assistance_reason"] == "immediate_safety"
    assert "emergency services" in turn["reply"]["text"]
    assert turn["claim"]["facts"] == {}
    assert confirm(client, turn).status_code == 409


def test_three_inconclusive_turns_request_human_assistance(client):
    turn = start(client)
    for _ in range(3):
        turn = advance(client, turn, "I am not sure yet.")
    assert turn["claim"]["status"] == "human_assistance_required"
    assert turn["claim"]["assistance_reason"] == "clarification_exhausted"


def test_prompt_injection_cannot_confirm_or_mutate_facts(client):
    original = ready(client)
    turn = advance(client, original, INJECTION)
    assert turn["claim"]["status"] == "awaiting_customer_confirmation"
    assert turn["claim"]["facts"] == original["claim"]["facts"]
    assert turn["claim"]["confirmed_at"] is None
    assert history(client, turn)[-2]["text"] == INJECTION


def test_confirmation_rejects_missing_or_non_boolean_consent(client):
    turn = start(client)
    assert confirm(client, turn).json()["code"] == "not_ready_to_confirm"
    for message in DEMO_TURNS:
        turn = advance(client, turn, message)
    assert confirm(client, turn, value=False).json()["code"] == "confirmation_required"
    assert confirm(client, turn, value=1).json()["code"] == "invalid_request"
    assert len(history(client, turn)) == 11


@pytest.mark.parametrize("token", ["other-synthetic-key", "tenant-2-synthetic-key"])
def test_scoped_queries_and_writes_are_isolated(client, token):
    owner = start(client)
    claim = owner["claim"]
    other = {"Authorization": f"Bearer {token}"}
    conversation_url = f"{API}/conversations/{claim['conversation_id']}"
    claim_url = f"{API}/claims/{claim['claim_id']}"
    assert client.get(conversation_url, headers=other).status_code == 404
    assert client.get(claim_url, headers=other).status_code == 404
    assert client.post(
        f"{conversation_url}/messages",
        headers={"Idempotency-Key": "try", **other},
        json={"message": DEMO_TURNS[0], "expected_version": 0},
    ).status_code == 404
    assert client.post(
        f"{claim_url}/confirm", headers={"Idempotency-Key": "try", **other},
        json={"confirmed": True, "expected_version": 0},
    ).status_code == 404
    actor_claim = start(client, headers=other)["claim"]
    assert actor_claim["claim_id"] != claim["claim_id"]
    assert len(history(client, owner)) == 1


def test_credential_and_request_key_are_required(settings):
    with TestClient(create_app(settings)) as api:
        response = api.post(f"{API}/conversations", headers={"Idempotency-Key": "start"})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        response = api.post(f"{API}/conversations", headers=HEADERS)
        assert response.status_code == 422


def test_distinct_conversations_for_same_claimant_have_independent_state(client):
    one = start(client, "one")
    two = start(client, "two")
    advance(client, one, DEMO_TURNS[0])
    assert len(history(client, one)) == 3
    assert len(history(client, two)) == 1


def test_restart_preserves_claim_and_history(settings):
    with TestClient(create_app(settings), headers=HEADERS) as api:
        original = advance(api, start(api), DEMO_TURNS[0])
    with TestClient(create_app(settings), headers=HEADERS) as api:
        claim = api.get(f"{API}/claims/{original['claim']['claim_id']}").json()
        assert claim == original["claim"]
        assert len(history(api, original)) == 3
        resumed = advance(api, original, DEMO_TURNS[1])
        assert resumed["claim"]["facts"]["damage_type"] == claim["facts"]["damage_type"]
        assert resumed["claim"]["facts"]["property_location"]["value"] == "12 Example Lane, ZIP 98101"


def test_duplicate_requests_and_stale_versions_do_not_write_twice(client):
    first = start(client)
    assert start(client) == first
    message = send(client, first, DEMO_TURNS[0]).json()
    assert send(client, first, DEMO_TURNS[0]).json() == message
    assert send(client, first, DEMO_TURNS[1]).json()["code"] == "idempotency_conflict"
    assert send(client, first, DEMO_TURNS[1], key="different").json()["code"] == "stale_version"
    assert len(history(client, message)) == 3
    for text in DEMO_TURNS[1:]:
        message = advance(client, message, text)
    confirmed = confirm(client, message).json()
    assert confirm(client, message).json() == confirmed
    assert len(history(client, confirmed)) == 13


def test_failure_retains_message_and_same_key_recovers_after_restart(settings, monkeypatch, caplog):
    llm = LLMService(settings)

    def fail(*args, **kwargs):
        raise LLMError("timeout")

    monkeypatch.setattr(llm, "structured", fail)
    with TestClient(create_app(settings, llm=llm), headers=HEADERS) as api:
        turn = start(api)
        response = send(api, turn, DEMO_TURNS[0])
        assert response.status_code == 503
        stored = api.get(f"{API}/claims/{turn['claim']['claim_id']}").json()
        assert stored["processing_error"] == "timeout"
        assert stored["version"] == turn["claim"]["version"]
        messages = history(api, turn)
        assert [m["text"] for m in messages][1:] == [DEMO_TURNS[0]]
        assert stored["pending_message_id"] == messages[-1]["id"]
        assert send(api, turn, DEMO_TURNS[1], key="new").json()["code"] == "turn_pending"

    with TestClient(create_app(settings), headers=HEADERS) as api:
        recovered = advance(api, turn, DEMO_TURNS[0])
        assert recovered["claim"]["processing_error"] is None
        assert recovered["claim"]["pending_message_id"] is None
        assert len(history(api, turn)) == 3
        assert recovered["claim"]["facts"]["damage_type"]["message_id"] == messages[-1]["id"]
    assert DEMO_TURNS[0] not in caplog.text


def test_request_reserved_before_restart_can_be_resumed(settings):
    app = create_app(settings)
    with TestClient(app, headers=HEADERS) as api:
        turn = start(api)
        pending = app.state.intake_store.reserve(
            Claimant(tenant_id="tenant-1", claimant_id="owner"),
            UUID(turn["claim"]["conversation_id"]), "turn-0",
            MessageRequest(message=DEMO_TURNS[0], expected_version=0),
        )
        assert isinstance(pending, PendingTurn)
    with TestClient(create_app(settings), headers=HEADERS) as api:
        resumed = advance(api, turn, DEMO_TURNS[0])
        assert resumed["claim"]["facts"]["damage_type"]["message_id"] == str(pending.message_id)
        assert len(history(api, resumed)) == 3


def test_failed_offline_message_can_be_replaced_without_erasing_history(client):
    turn = start(client)
    unsupported = "An unsupported offline report."
    assert send(client, turn, unsupported).status_code == 503
    conversation = f"{API}/conversations/{turn['claim']['conversation_id']}"
    replacement = client.post(
        f"{conversation}/messages",
        headers={"Idempotency-Key": "replacement"},
        json={"message": DEMO_TURNS[0], "expected_version": 0, "replace_failed_message": True},
    )
    assert replacement.status_code == 200
    assert [entry["text"] for entry in history(client, turn)][1:3] == [unsupported, DEMO_TURNS[0]]
    assert send(client, turn, unsupported).json()["code"] == "stale_turn"


def test_safety_can_supersede_pending_routine_message(client):
    turn = start(client)
    unknown = "An unsupported offline report."
    assert send(client, turn, unknown).status_code == 503
    urgent = send(client, turn, HAZARD, key="safety")
    assert urgent.status_code == 200
    assert urgent.json()["claim"]["assistance_reason"] == "immediate_safety"
    assert [entry["text"] for entry in history(client, turn)][1:3] == [unknown, HAZARD]


def test_concurrent_replay_commits_one_message_and_reply(settings, monkeypatch):
    llm = LLMService(settings)
    offline = llm.structured
    concurrent = Barrier(2, timeout=5)

    def rendezvous(*args, **kwargs):
        concurrent.wait()
        return offline(*args, **kwargs)

    monkeypatch.setattr(llm, "structured", rendezvous)
    with TestClient(create_app(settings, llm=llm), headers=HEADERS) as api:
        turn = start(api)
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [pool.submit(send, api, turn, DEMO_TURNS[0]) for _ in range(2)]
            first, second = [call.result(timeout=10) for call in calls]
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert len(history(api, turn)) == 3
