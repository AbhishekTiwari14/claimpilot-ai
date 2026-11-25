import json
import sqlite3
from time import sleep

from fastapi.testclient import TestClient
import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_groq import ChatGroq
import pytest

from claimpilot.agents.contractors import timed_read
from claimpilot.config import ClaimantCredential, OperatorCredential, Settings
from claimpilot.main import create_app
from claimpilot.services.contractor_tools import ContractorTools
from claimpilot.services.contractor_urgency import classify_urgency
from claimpilot.services.llm import LLMService, ToolTurn
from examples.contractors import CASES, CUSTOMER, OPERATOR, create_claim, fixture_embed, recommend, seed_handbook

TOOLS = (
    "search_contractors", "get_dispatch_requirements", "get_contractor_performance",
    "check_availability", "get_estimated_arrival", "propose_dispatch",
)
OTHER = {"Authorization": "Bearer other-contractor-operator"}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None, claimpilot_llm_provider="offline", claimpilot_intake_db=tmp_path / "test.sqlite3",
        claimpilot_kb_chroma=tmp_path / "chroma",
        claimpilot_claimants=[ClaimantCredential(tenant_id="demo", claimant_id="claimant",
                                                token=CUSTOMER["Authorization"][7:])],
        claimpilot_kb_operators=[
            OperatorCredential(tenant_id="demo", operator_id="ops", token=OPERATOR["Authorization"][7:]),
            OperatorCredential(tenant_id="other", operator_id="ops", token=OTHER["Authorization"][7:]),
        ],
    )


@pytest.fixture
def api(settings):
    with TestClient(create_app(settings, embed=fixture_embed, embedding_id="contractor-test-lexical")) as client:
        seed_handbook(client)
        yield client


def scenario(name):
    return next(case for case in CASES if case["case"] == name)


def adapter(script, before=None):
    index = 0

    def turn(messages, tools, **kwargs):
        nonlocal index
        index += 1
        if before is not None:
            before(index, messages)
        if index > len(script):
            return ToolTurn(AIMessage(content="Free text cannot affect the recommendation."), 0)
        name, args = script[index - 1]
        return ToolTurn(AIMessage(content="", tool_calls=[
            {"name": name, "args": args, "id": f"call-{index}"},
        ]), 0)

    return turn


def action(name, contractor="CT-001"):
    return name, {} if name in ("search_contractors", "get_dispatch_requirements") else {"contractor_id": contractor}


def change_record(settings, contractor_id, **changes):
    with sqlite3.connect(settings.claimpilot_intake_db) as db:
        data = json.loads(db.execute(
            "SELECT record_json FROM contractors WHERE tenant_id='demo' AND id=?", (contractor_id,),
        ).fetchone()[0])
        db.execute("UPDATE contractors SET record_json=? WHERE tenant_id='demo' AND id=?",
                   (json.dumps(data | changes), contractor_id))
    return data


@pytest.mark.parametrize("name", [case["case"] for case in CASES])
def test_labeled_offline_cases_and_tool_feedback(api, name):
    case = scenario(name)
    claim = create_claim(api, case)
    run, trace = recommend(api, claim, name)
    proposal = run["proposal"]
    assert proposal["status"] == case["expected"]
    assert proposal["urgency"]["level"] == case["expected_urgency"]
    assert set(case["required_tools"]) <= {tool["tool"] for tool in trace["tools"] if tool["success"]}
    for tool in trace["tools"]:
        assert any(tool["call_id"] in turn["feedback_call_ids"] for turn in trace["model_turns"])
    if case["eligible_ids"]:
        assert proposal["recommended_contractor_id"] in case["eligible_ids"]
        assert proposal["estimated_arrival_minutes"] > 0
        assert proposal["requirements"]["sources"][0]["chunk_id"]
    else:
        assert proposal["recommended_contractor_id"] is None
    assert proposal["human_intervention_required"]
    assert recommend(api, claim, name)[0] == run


@pytest.mark.parametrize("order", [
    TOOLS, ("get_dispatch_requirements", "search_contractors", "check_availability",
            "get_estimated_arrival", "get_contractor_performance", "propose_dispatch"),
])
def test_dynamic_read_order_is_not_prescribed(api, monkeypatch, order):
    monkeypatch.setattr(api.app.state.llm, "tool_turn", adapter([action(tool) for tool in order]))
    result, trace = recommend(api, create_claim(api, scenario("eligible")), "order")
    assert result["proposal"]["status"] == "awaiting_approval"
    assert [tool["tool"] for tool in trace["tools"]] == list(order)


def test_multiple_tools_in_one_turn_receive_individually_paired_results(api, monkeypatch):
    structure = [
        [action("search_contractors")],
        [action("get_estimated_arrival"), action("check_availability"), action("get_contractor_performance")],
        [action("get_dispatch_requirements")], [action("propose_dispatch")], [],
    ]
    calls = iter(enumerate(structure, 1))

    def choose(messages, tools, **kwargs):
        iteration, requested = next(calls)
        return ToolTurn(AIMessage(content="", tool_calls=[
            {"name": name, "args": args, "id": f"batch-{iteration}-{index}"}
            for index, (name, args) in enumerate(requested)
        ]), 0)

    monkeypatch.setattr(api.app.state.llm, "tool_turn", choose)
    result, trace = recommend(api, create_claim(api, scenario("eligible")), "native-batch")
    assert result["proposal"]["status"] == "awaiting_approval"
    assert len(trace["model_turns"][1]["requested_call_ids"]) == 3
    assert {"batch-2-0", "batch-2-1", "batch-2-2"} <= set(trace["model_turns"][2]["feedback_call_ids"])


@pytest.mark.parametrize("candidate", ["CT-003", "CT-015", "CT-023", "CT-999"])
def test_out_of_scope_or_ineligible_model_candidate_is_rejected(api, monkeypatch, candidate):
    script = [action("search_contractors"), action("check_availability", candidate)]
    monkeypatch.setattr(api.app.state.llm, "tool_turn", adapter(script))
    result, trace = recommend(api, create_claim(api, scenario("eligible")), "invalid-id")
    assert result["proposal"]["status"] != "awaiting_approval"
    assert result["proposal"]["recommended_contractor_id"] is None
    assert trace["tools"][-1]["result"] == {"error": "not_discovered"}
    assert len(trace["tools"][0]["result"]["candidate_ids"]) == 2


def test_availability_change_between_discovery_and_draft_fails_closed(api, settings, monkeypatch):
    def before(iteration, messages):
        if iteration == 6:
            change_record(settings, "CT-001", available=False)

    monkeypatch.setattr(api.app.state.llm, "tool_turn", adapter([action(tool) for tool in TOOLS], before))
    result, trace = recommend(api, create_claim(api, scenario("eligible")), "availability-change")
    assert result["proposal"]["recommended_contractor_id"] is None
    assert trace["tools"][-1]["result"] == {"error": "missing_or_stale_evidence"}


def test_invalid_calls_are_sanitized_and_stop_after_feedback(api, monkeypatch):
    claim = create_claim(api, scenario("eligible"))
    script = [("dispatch_now_private_marker", {"token": "sensitive-marker"}),
              ("search_contractors", {"workspace": "attacker", "limit": "two"}),
              ("search_contractors", {})]
    monkeypatch.setattr(api.app.state.llm, "tool_turn", adapter(script))
    result, trace = recommend(api, claim, "injection")
    assert result["proposal"]["recommended_contractor_id"] is None
    assert len(trace["tools"]) == 2
    assert [step["result"]["error"] for step in trace["tools"]] == ["unknown_tool", "invalid_arguments"]
    assert trace["model_turns"][-1]["feedback_call_ids"] == ["call-1", "call-2"]
    assert "sensitive-marker" not in json.dumps(trace) and "private_marker" not in json.dumps(trace)


def test_iteration_and_tool_limits_and_repeats(api, settings, monkeypatch):
    claim = create_claim(api, scenario("eligible"))
    repeated = [action("search_contractors")] * 4
    monkeypatch.setattr(api.app.state.llm, "tool_turn", adapter(repeated))
    result, trace = recommend(api, claim, "repeat")
    assert [step["success"] for step in trace["tools"]] == [True, False, False]
    assert trace["tools"][-1]["result"]["error"] == "repeated_tool_call"
    assert result["proposal"]["status"] == "unsuccessful"

    settings.claimpilot_agent_max_iterations = 1
    monkeypatch.setattr(api.app.state.llm, "tool_turn", adapter([action(t) for t in TOOLS]))
    result, trace = recommend(api, claim, "limit")
    assert len(trace["model_turns"]) == 1
    assert "iteration_limit" in result["proposal"]["uncertainties"]
    settings.claimpilot_agent_max_iterations = 6
    settings.claimpilot_agent_max_tool_calls = 1
    monkeypatch.setattr(api.app.state.llm, "tool_turn", adapter([action(t) for t in TOOLS]))
    result, trace = recommend(api, claim, "tool-limit")
    assert len(trace["tools"]) == 1
    assert "tool_budget_exhausted" in result["proposal"]["uncertainties"]


def test_transient_read_retry_and_timeout(api, monkeypatch):
    original = ContractorTools.get_dispatch_requirements
    calls = 0

    def fail_once(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("locked")
        return original(self)

    monkeypatch.setattr(ContractorTools, "get_dispatch_requirements", fail_once)
    result, trace = recommend(api, create_claim(api, scenario("eligible")), "retry")
    assert result["proposal"]["status"] == "awaiting_approval"
    step = next(step for step in trace["tools"] if step["tool"] == "get_dispatch_requirements")
    assert step["attempts"] == 2 and step["success"]

    class Slow:
        def invoke(self, args):
            sleep(0.1)

    assert timed_read(Slow(), {}, 0.01) == (None, "tool_timeout", 1)


@pytest.mark.parametrize("change", ["contractor", "claim", "requirements"])
def test_stale_proposal_is_invalidated(api, settings, change):
    claim = create_claim(api, scenario("eligible"))
    run, _ = recommend(api, claim, "stale")
    base = f"/api/v1/claims/{claim['claim_id']}"
    url = f"{base}/contractor-recommendations/{run['id']}"
    if change == "contractor":
        original = change_record(settings, "CT-001", active=False)
    elif change == "claim":
        with sqlite3.connect(settings.claimpilot_intake_db) as db:
            db.execute(
                "UPDATE claims SET state_json=json_set(state_json,'$.version',?) WHERE id=?",
                (claim["version"] + 1, claim["claim_id"]),
            )
    else:
        document = api.get("/api/v1/knowledge/documents", headers=OPERATOR).json()[0]
        api.delete(f"/api/v1/knowledge/documents/{document['id']}", headers={**OPERATOR, "Idempotency-Key": "delete"})
    stale = api.get(url, headers=OPERATOR).json()["proposal"]
    assert stale["stale"] and stale["status"] == "requires_intervention"
    if change == "contractor":
        change_record(settings, "CT-001", active=original["active"])
        assert api.get(url, headers=OPERATOR).json()["proposal"]["stale"]
    assert api.get(f"{base}/dispatch-proposals", headers=OPERATOR).json()[0]["stale"]


def test_operator_scope_and_version_gate(api, settings):
    claim = create_claim(api, scenario("eligible"))
    run, _ = recommend(api, claim, "auth")
    base = f"/api/v1/claims/{claim['claim_id']}/contractor-recommendations"
    assert api.get("/api/v1/contractors", headers=CUSTOMER).status_code == 403
    assert len(api.get("/api/v1/contractors", headers=OPERATOR).json()) == 24
    change_record(settings, "CT-001", available=False)
    assert api.get("/api/v1/contractors/CT-001", headers=OTHER).json()["available"] is True
    for path in (f"{base}/{run['id']}", f"{base}/{run['id']}/trace"):
        assert api.get(path, headers=OTHER).status_code == 404
    assert api.post(base, headers={**OTHER, "Idempotency-Key": "other"},
                    json={"expected_version": claim["version"]}).status_code == 404
    assert api.post(base, headers={**OPERATOR, "Idempotency-Key": "stale-version"},
                    json={"expected_version": claim["version"] - 1}).status_code == 409


def test_deterministic_urgency_preserves_safety_priority():
    facts = {"damage_type": "water damage", "ongoing_hazards": ["There is no active leak."]}
    assert classify_urgency(facts, []).level == "medium"
    facts["ongoing_hazards"] = ["There is no smoke but I smell gas."]
    result = classify_urgency(facts, [])
    assert result.level == "emergency" and result.mandatory_escalation


def test_native_wire_preserves_tool_call_id_and_feedback(settings):
    settings.claimpilot_llm_provider = "groq"
    sent = []

    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        first = len(sent) == 1
        message = {"role": "assistant", "content": ""} if not first else {
            "role": "assistant", "tool_calls": [{
                "id": "call-real-wire", "type": "function",
                "function": {"name": "search_contractors", "arguments": "{}"},
            }],
        }
        return httpx.Response(200, json={
            "id": "tool-native-mock", "model": settings.groq_model, "object": "chat.completion", "created": 0,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if first else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        })

    from langchain_core.tools import StructuredTool
    from claimpilot.schemas.contractors import NoArgs

    def search_contractors():
        return {}

    tools = [StructuredTool.from_function(search_contractors, description="Search.", args_schema=NoArgs)]
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        llm = LLMService(settings, chat_model=ChatGroq(
            model=settings.groq_model, api_key="mock-key", max_retries=0, http_client=transport,
        ))
        messages = [HumanMessage(content="Find eligible contractors.")]
        first = llm.tool_turn(messages, tools, offline_turn=lambda _: None).message
        assert first.tool_calls[0]["id"] == "call-real-wire"
        second = llm.tool_turn(messages + [
            first, ToolMessage(content='{"candidate_ids":[]}', tool_call_id="call-real-wire"),
        ], tools, offline_turn=lambda _: None).message
        assert not second.tool_calls
    assert sent[1]["messages"][-1]["tool_call_id"] == "call-real-wire"
    assert sent[1]["messages"][-1]["content"] == '{"candidate_ids":[]}'
    assert "response_format" not in sent[0]
