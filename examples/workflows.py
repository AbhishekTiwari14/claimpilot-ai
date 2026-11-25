import argparse
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from claimpilot.config import ClaimantCredential, OperatorCredential, Settings
from claimpilot.main import create_app
from examples.contractors import CUSTOMER, OPERATOR, ROOT, checked, create_claim, seed_handbook


def bind_policy(client, claim_id):
    form = ROOT / "knowledge" / "homeowners-a-v1.md"
    checked(client.post(
        "/api/v1/knowledge/documents", headers={**OPERATOR, "Idempotency-Key": "form-a"},
        files={"document": (form.name, form.read_bytes())},
        data={"metadata": json.dumps({"title": "Synthetic form A", "document_type": "policy",
                                     "policy_id": "SYN-123", "policy_version": "v1"})},
    ))
    checked(client.post(f"/api/v1/claims/{claim_id}/policy-binding",
                        headers={**OPERATOR, "Idempotency-Key": f"bind-{claim_id}"},
                        json={"policy_version": "v1"}))


def decision(state, action="approve"):
    return {"pause_id": state["pause_id"], "action": action, "expected_version": state["claim_version"],
            "proposal_id": state["proposal_id"], "proposal_version": state["proposal_version"],
            "reason": "Reviewed the synthetic contractor proposal and its evidence."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve", action="store_true", help="Explicitly approve the synthetic proposal without prompting")
    parser.add_argument("--dispatch", action="store_true", help="Send and accept the approved simulated dispatch")
    args = parser.parse_args()
    directory = ROOT / "runtime" / f"workflow-demo-{uuid4().hex[:12]}"
    settings = Settings(
        claimpilot_llm_provider="offline", claimpilot_intake_db=directory / "pilot.sqlite3",
        claimpilot_kb_chroma=directory / "chroma",
        claimpilot_claimants=[ClaimantCredential(tenant_id="demo", claimant_id="claimant",
                                                token=CUSTOMER["Authorization"][7:])],
        claimpilot_kb_operators=[OperatorCredential(tenant_id="demo", operator_id="ops",
                                                   token=OPERATOR["Authorization"][7:])],
    )
    with TestClient(create_app(settings)) as client:
        seed_handbook(client)
        claim = create_claim(client, {"case": "workflow", "zip": "98101"})
        claim_base = f"/api/v1/claims/{claim['claim_id']}"
        bind_policy(client, claim["claim_id"])
        settings.claimpilot_llm_provider = "groq"
        start = checked(client.post(f"{claim_base}/workflows", headers={**OPERATOR, "Idempotency-Key": "start"},
                                    json={"expected_version": claim["version"]}))
        print(json.dumps(start), flush=True)
        if start["status"] != "awaiting_approval":
            raise SystemExit("The workflow did not produce a proposal for approval.")
        trace_url = f"{claim_base}/contractor-recommendations/{start['contractor_run_id']}/trace"
        trace = checked(client.get(trace_url, headers=OPERATOR))
        print(json.dumps({"tools": [tool["tool"] for tool in trace["tools"] if tool["success"]]}), flush=True)
    if not args.approve and input("Type approve to confirm the synthetic proposal: ").strip() != "approve":
        return
    with TestClient(create_app(settings)) as restarted:
        url = f"{claim_base}/workflows/{start['workflow_id']}"
        assert checked(restarted.get(url, headers=OPERATOR))["pause_id"] == start["pause_id"]
        final = checked(restarted.post(f"{url}/resume", headers={**OPERATOR, "Idempotency-Key": "approve"},
                                      json=decision(start)))
        print(json.dumps(final))
        if final["status"] != "approved":
            raise SystemExit("The recorded decision did not approve the proposal.")
        if args.dispatch:
            sent = checked(restarted.post(f"{url}/dispatch", headers={**OPERATOR, "Idempotency-Key": "send"},
                                          json={"proposal_id": final["proposal_id"],
                                                "proposal_version": final["proposal_version"]}))
            dispatch_id = sent["dispatches"][-1]["id"]
            result = checked(restarted.post(f"{url}/dispatch/{dispatch_id}/response",
                                            headers={**OPERATOR, "Idempotency-Key": "accept"},
                                            json={"outcome": "accepted"}))
            print(json.dumps(result))
            if result["workflow"]["status"] != "accepted":
                raise SystemExit("The simulated contractor did not accept the dispatch.")


if __name__ == "__main__":
    main()
