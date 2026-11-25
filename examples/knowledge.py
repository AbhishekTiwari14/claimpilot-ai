import argparse
from contextlib import nullcontext
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

from fastapi.testclient import TestClient

from claimpilot.config import OperatorCredential, Settings
from claimpilot.main import create_app

ROOT = Path(__file__).resolve().parents[1] / "data" / "knowledge"
BASE = "/api/v1/knowledge"
AUTH = {"Authorization": "Bearer synthetic-knowledge-demo"}
OTHER = {"Authorization": "Bearer synthetic-knowledge-other"}


def checked(response):
    if not response.is_success:
        raise RuntimeError(f"HTTP {response.status_code}: {response.json().get('code')}")
    return response.json()


def seed(client):
    for row in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8")):
        name = row.pop("filename")
        data = (ROOT / name).read_bytes()
        key = "seed-" + sha256(data + json.dumps(row, sort_keys=True).encode()).hexdigest()
        response = client.post(
            f"{BASE}/documents", headers={**AUTH, "Idempotency-Key": key},
            files={"document": (name, data)}, data={"metadata": json.dumps(row)},
        )
        result = checked(response)
        if result["status"] != "ready":
            raise RuntimeError(f"Indexing {name} returned {result['status']}: {result['error_code']}")
        print(f"Ready {name} ({result['id']})", flush=True)


def evaluate(client, *, provider, pause):
    cases = json.loads((ROOT / "evaluation.json").read_text(encoding="utf-8"))
    hits = expected = all_sections = citations = valid = behavior = support_pass = support_n = 0
    for case in cases:
        request = {"query": case["query"], "top_k": 5, **case["scope"]}
        answer = checked(client.post(f"{BASE}/playground/query", json=request,
                                     headers=OTHER if case.get("other_workspace") else AUTH))
        found = {item["section"] for item in answer["retrieved"]}
        required = set(case["sections"])
        if required:
            expected += 1
            hits += bool(found & required)
            all_sections += required <= found
        scope_correct = all(
            all(source[key] == value for key, value in case["scope"].items())
            for source in answer["retrieved"]
        )
        actual = answer["outcome"] == case["outcome"] and scope_correct
        if case.get("expect_empty"):
            actual = actual and not answer["retrieved"]
        behavior += bool(actual)
        retrieved = {item["chunk_id"]: item for item in answer["retrieved"]}
        for citation in answer["citations"]:
            citations += 1
            source = citation["source"]
            valid += retrieved.get(source["chunk_id"]) == source and citation["excerpt"] in source["text"]
        supports = None
        if case["support"]:
            support_n += 1
            text = " ".join(c["excerpt"] for c in answer["citations"]).casefold()
            supports = all(term.casefold() in text for term in case["support"])
            support_pass += supports
        print(json.dumps({
            "case": case["name"], "outcome": answer["outcome"], "expected": case["outcome"],
            "retrieved_sections": [item["section"] for item in answer["retrieved"]],
            "labeled_support_present": supports, "citations": [
                [c["source"]["section"], c["excerpt"]] for c in answer["citations"]
            ],
        }), flush=True)
        if provider == "groq" and answer["retrieved"] and pause:
            sleep(pause)
    print(json.dumps({
        "provider": provider, "hit_at_5": [hits, expected], "all_labeled_sections_at_5": [all_sections, expected],
        "citation_validity": [valid, citations], "expected_behavior": [behavior, len(cases)],
        "labeled_support_proxy": [support_pass, support_n],
    }), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Upload synthetic knowledge, search, and evaluate grounded answers.")
    parser.add_argument("--provider", choices=["offline", "groq"], default="offline")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--pause", type=float, default=0)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--skip-seed", action="store_true")
    args = parser.parse_args()
    if args.skip_seed and not args.runtime_dir:
        parser.error("--skip-seed requires --runtime-dir")
    with (nullcontext(str(args.runtime_dir)) if args.runtime_dir else TemporaryDirectory()) as path:
        settings = Settings(
            claimpilot_intake_db=Path(path) / "knowledge.sqlite3",
            claimpilot_kb_chroma=Path(path) / "chroma",
            claimpilot_llm_provider=args.provider,
            claimpilot_kb_operators=[
                OperatorCredential(tenant_id="demo", operator_id="ops", token=AUTH["Authorization"][7:]),
                OperatorCredential(tenant_id="other", operator_id="ops", token=OTHER["Authorization"][7:]),
            ],
        )
        with TestClient(create_app(settings)) as client:
            if not args.skip_seed:
                seed(client)
            if args.evaluate:
                evaluate(client, provider=args.provider, pause=args.pause)
            else:
                query = (
                    "Does this policy cover resulting dwelling damage from a dishwasher that suddenly discharges water?"
                    if args.provider == "groq" else "What does A2 Sudden indoor water discharge say?"
                )
                response = checked(client.post(f"{BASE}/playground/query", headers=AUTH, json={
                    "document_type": "policy", "policy_id": "SYN-123", "policy_version": "v1", "query": query,
                }))
                print(json.dumps(response, indent=2))
                if response["outcome"] != "answered":
                    raise RuntimeError("The demo did not yield the expected grounded answer.")
            
if __name__ == "__main__":
    main()
