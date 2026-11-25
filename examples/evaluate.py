"""Run deterministic regressions and offline measurements. Live verification is opt-in."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from claimpilot.agents.intake import SAMPLE_REPORT, extract_claim
from claimpilot.agents.intake_fixtures import DEMO_TURNS, INJECTION
from claimpilot.config import OperatorCredential, Settings
from claimpilot.main import create_app
from claimpilot.services.llm import LLMService
from examples.contractors import fixture_embed
from examples.knowledge import AUTH, BASE, OTHER, ROOT, checked, seed


def offline_metrics(directory: Path) -> bool:
    settings = Settings(
        _env_file=None, claimpilot_llm_provider="offline", claimpilot_intake_db=directory / "eval.sqlite3",
        claimpilot_kb_chroma=directory / "chroma",
        claimpilot_kb_operators=[
            OperatorCredential(tenant_id="demo", operator_id="ops", token=AUTH["Authorization"][7:]),
            OperatorCredential(tenant_id="other", operator_id="ops", token=OTHER["Authorization"][7:]),
        ],
    )
    llm = LLMService(settings)
    cases = [
        (SAMPLE_REPORT, {"damage_type": "water damage", "policy_id": "SYN-123", "affected_areas": ["kitchen"]}),
        (DEMO_TURNS[0], {"damage_type": "water damage", "policy_id": None}),
        (INJECTION, {"damage_type": None, "policy_id": None}),
    ]
    extracted = sum(all(getattr(result, field) == value for field, value in expected.items())
                    for result, expected in ((extract_claim(report, llm), expected) for report, expected in cases))
    hits = relevant = isolated = isolation_cases = valid = citations = named = 0
    with TestClient(create_app(settings, embed=fixture_embed, embedding_id="offline-lexical-fixture")) as client:
        seed(client)
        for case in json.loads((ROOT / "evaluation.json").read_text(encoding="utf-8")):
            payload = {"query": case["query"], "top_k": 5, **case["scope"]}
            headers = OTHER if case.get("other_workspace") else AUTH
            rag = checked(client.post(f"{BASE}/playground/query", headers=headers, json=payload))
            if case["sections"]:
                relevant += 1
                hits += bool(set(case["sections"]) & {chunk["section"] for chunk in rag["retrieved"]})
            if case["type"] in ("negative", "out_of_scope"):
                isolation_cases += 1
                isolated += rag["insufficient_evidence"] and (not case.get("expect_empty") or not rag["retrieved"])
            if case["type"] != "positive":
                continue
            named += 1
            answer = checked(client.post(f"{BASE}/playground/query", headers=headers,
                                         json=payload | {"query": f"What does {case['sections'][0]} say?"}))
            retrieved = {item["chunk_id"]: item for item in answer["retrieved"]}
            for citation in answer["citations"]:
                citations += 1
                source = citation["source"]
                valid += (retrieved.get(source["chunk_id"]) == source
                          and citation["excerpt"] in source["text"] and source["section"] in case["sections"])
    print(json.dumps({
        "mode": "offline_fixture", "extraction_cases": [extracted, len(cases)], "retrieval_hit_at_5": [hits, relevant],
        "abstention_and_scope": [isolated, isolation_cases], "named_clause_citations": [valid, citations],
        "named_clause_cases": named,
    }), flush=True)
    return extracted == len(cases) and isolated == isolation_cases and valid == citations == named


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Also run one Groq workflow with simulated approval and acceptance")
    args = parser.parse_args()
    directory = ROOT.parent / "runtime" / f"evaluation-{uuid4().hex[:12]}"
    directory.mkdir(parents=True)
    report = directory / "pytest.xml"
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q", f"--junitxml={report}"], check=False)
    if report.exists():
        counts = {}
        for case in ElementTree.parse(report).iter("testcase"):
            name = case.get("classname", "unknown")
            passed, total = counts.get(name, (0, 0))
            counts[name] = (passed + int(len(case) == 0 or all(c.tag == "properties" for c in case)), total + 1)
        print(json.dumps({"mode": "deterministic_tests", "passed_by_module": counts}), flush=True)
    offline_ok = offline_metrics(directory)
    contractors = subprocess.run([sys.executable, "-m", "examples.contractors", "--evaluate"], check=False)
    if tests.returncode or not offline_ok or contractors.returncode:
        return 1
    if args.live:
        return subprocess.run([sys.executable, "-m", "examples.workflows", "--approve", "--dispatch"], check=False).returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
