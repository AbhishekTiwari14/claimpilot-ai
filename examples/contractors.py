import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4

from fastapi.testclient import TestClient

from claimpilot.agents.intake_fixtures import ACTIVE_WATER, CONTRACTOR_LOCATIONS, DEMO_TURNS, HAZARD
from claimpilot.config import ClaimantCredential, OperatorCredential, Settings
from claimpilot.main import create_app

ROOT = Path(__file__).resolve().parents[1] / "data"
OPERATOR = {"Authorization": "Bearer phase5-synthetic-operator"}
CUSTOMER = {"Authorization": "Bearer phase5-synthetic-customer"}
CASES = json.loads((ROOT / "contractors" / "evaluation.json").read_text(encoding="utf-8"))


def fixture_embed(texts):
    vectors = []
    for text in texts:
        vector = [0.0] * 128
        for word in re.findall(r"\w+", text.casefold()):
            vector[int.from_bytes(sha256(word.encode()).digest()[:2], "big") % 128] += 1
        vectors.append(vector)
    return vectors


def checked(response):
    if not response.is_success:
        raise RuntimeError(f"HTTP {response.status_code}: {response.json().get('code')}")
    return response.json()


def seed_handbook(client):
    handbook = ROOT / "knowledge" / "restoration-operations.md"
    document = checked(client.post(
        "/api/v1/knowledge/documents", headers={**OPERATOR, "Idempotency-Key": "handbook"},
        files={"document": (handbook.name, handbook.read_bytes())},
        data={"metadata": json.dumps({"title": "Synthetic restoration handbook", "document_type": "operations"})},
    ))
    if document["status"] != "ready":
        raise RuntimeError(f"The operations handbook did not index: {document['error_code']}")


def create_claim(client, case):
    name = case["case"]
    state = checked(client.post("/api/v1/conversations", headers={
        **CUSTOMER, "Idempotency-Key": f"{name}-start"
    }))["claim"]
    messages = list(DEMO_TURNS)
    if case.get("zip"):
        messages[1] = CONTRACTOR_LOCATIONS[case["zip"]]
    if case.get("active_water"):
        messages[-1] = ACTIVE_WATER
    if case.get("emergency"):
        messages = [HAZARD]
    for index, text in enumerate(messages):
        state = checked(client.post(
            f"/api/v1/conversations/{state['conversation_id']}/messages",
            headers={**CUSTOMER, "Idempotency-Key": f"{name}-message-{index}"},
            json={"message": text, "expected_version": state["version"]},
        ))["claim"]
    if not case.get("emergency"):
        state = checked(client.post(f"/api/v1/claims/{state['claim_id']}/confirm", headers={
            **CUSTOMER, "Idempotency-Key": f"{name}-confirm",
        }, json={"expected_version": state["version"], "confirmed": True}))["claim"]
    return state


def recommend(client, claim, key):
    base = f"/api/v1/claims/{claim['claim_id']}/contractor-recommendations"
    result = checked(client.post(base, headers={**OPERATOR, "Idempotency-Key": key},
                                 json={"expected_version": claim["version"]}))
    trace = checked(client.get(f"{base}/{result['id']}/trace", headers=OPERATOR))
    return result, trace


def print_case(case, run, trace, full=False):
    proposal = run["proposal"]
    turns = trace["model_turns"]
    token_usage = {key: sum(turn["usage"].get(key, 0) for turn in turns)
                   for key in ("input_tokens", "output_tokens", "total_tokens")}
    print(json.dumps({
        "case": case["case"], "provider": run["provider"], "run": run["id"],
        "status": proposal["status"], "uncertainties": proposal["uncertainties"],
        "urgency": proposal["urgency"]["level"], "recommended": proposal["recommended_contractor_id"],
        "eta_minutes": proposal["estimated_arrival_minutes"], "iterations": len(turns),
        "token_usage": token_usage, "trace": trace if full else [
            {"tool": row["tool"], "id": row["call_id"], "result": row["result"], "ms": row["duration_ms"]}
            for row in trace["tools"]
        ],
        "feedback_confirmed": {
            row["call_id"]: any(row["call_id"] in turn["feedback_call_ids"] for turn in turns)
            for row in trace["tools"]
        },
    }), flush=True)


def score(results):
    steps = [step for _, _, trace in results for step in trace["tools"]]
    needs = [(case, trace) for case, _, trace in results if case["required_tools"]]
    eligible_cases = [(case, proposal) for case, proposal, _ in results if case["eligible_ids"]]
    recommended = [(case, proposal) for case, proposal, _ in results if proposal["recommended_contractor_id"]]
    escalations = [(case, proposal) for case, proposal, _ in results if not case["eligible_ids"]]
    report = {
        "tool_execution_validity": [sum(step["success"] for step in steps), len(steps)],
        "required_information_retrieval": [
            sum(set(case["required_tools"]) <= {s["tool"] for s in trace["tools"] if s["success"]}
                for case, trace in needs), len(needs)
        ],
        "contractor_eligibility_accuracy": [
            sum(proposal["recommended_contractor_id"] in case["eligible_ids"] for case, proposal in recommended),
            len(recommended),
        ],
        "successful_recommendation_rate": [
            sum(proposal["status"] == "awaiting_approval" for _, proposal in eligible_cases), len(eligible_cases)
        ],
        "correct_escalation": [
            sum(proposal["status"] == case["expected"] and proposal["urgency"]["level"] == case["expected_urgency"]
                for case, proposal in escalations), len(escalations)
        ],
        "unauthorized_actions": sum(
            proposal["status"] == "awaiting_approval"
            and proposal["recommended_contractor_id"] not in case["eligible_ids"]
            for case, proposal, _ in results
        ),
    }
    print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Run the synthetic contractor recommendation API.")
    parser.add_argument("--provider", choices=["offline", "groq"], default="offline")
    parser.add_argument("--scenario", choices=[case["case"] for case in CASES], default="eligible")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--full-trace", action="store_true")
    parser.add_argument("--runtime-dir", type=Path)
    args = parser.parse_args()
    scenarios = CASES if args.evaluate else [case for case in CASES if case["case"] == args.scenario]
    directory = args.runtime_dir or ROOT / "runtime" / f"contractor-demo-{uuid4().hex[:12]}"
    settings = Settings(
        claimpilot_llm_provider="offline", claimpilot_intake_db=directory / "pilot.sqlite3",
        claimpilot_kb_chroma=directory / "chroma",
        claimpilot_claimants=[ClaimantCredential(tenant_id="demo", claimant_id="claimant",
                                                token=CUSTOMER["Authorization"][7:])],
        claimpilot_kb_operators=[OperatorCredential(tenant_id="demo", operator_id="ops",
                                                   token=OPERATOR["Authorization"][7:])],
    )
    options = {"embed": fixture_embed, "embedding_id": "offline-lexical-fixture"} if args.provider == "offline" else {}
    with TestClient(create_app(settings, **options)) as client:
        seed_handbook(client)
        claims = [create_claim(client, case) for case in scenarios]
        settings.claimpilot_llm_provider = args.provider
        results = []
        for case, claim in zip(scenarios, claims, strict=True):
            run, trace = recommend(client, claim, f"{args.provider}-{case['case']}")
            print_case(case, run, trace, args.full_trace)
            results.append((case, run["proposal"], trace))
        if args.evaluate:
            score(results)
        if any(proposal["status"] != case["expected"] for case, proposal, _ in results):
            raise SystemExit("A scenario returned an unexpected status.")


if __name__ == "__main__":
    main()
