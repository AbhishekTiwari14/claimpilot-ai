"""Export measured offline metrics and a previously recorded pytest XML report."""
import argparse
from contextlib import redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from xml.etree import ElementTree

from examples.evaluate import offline_metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", required=True, type=Path)
    args = parser.parse_args()
    cases = list(ElementTree.parse(args.junit).iter("testcase"))
    captured = io.StringIO()
    with redirect_stdout(captured):
        valid = offline_metrics(Path("data/runtime") / f"frontend-benchmark-{uuid4().hex[:12]}")
    if not valid or not cases:
        raise SystemExit("Benchmark fixtures or test report were incomplete.")
    rag = json.loads(captured.getvalue().splitlines()[-1])
    contractor = subprocess.run([sys.executable, "-m", "examples.contractors", "--evaluate"],
                                capture_output=True, text=True, check=True)
    tools = json.loads(contractor.stdout.splitlines()[-1])
    results = []

    def add(metric, mode, numbers):
        results.append(dict(metric=metric, mode=mode, passed=numbers[0], total=numbers[1]))

    add("Backend regression tests", "Deterministic tests", [
        sum(not any(child.tag in ("failure", "error", "skipped") for child in case) for case in cases), len(cases),
    ])
    for name, key in (
        ("Structured extraction", "extraction_cases"), ("Retrieval Hit@5", "retrieval_hit_at_5"),
        ("Named-clause citation validity", "named_clause_citations"), ("Abstention and scope", "abstention_and_scope"),
    ):
        add(name, "Offline fixtures", rag[key])
    for name, key in (
        ("Valid tool executions", "tool_execution_validity"),
        ("Contractor eligibility", "contractor_eligibility_accuracy"), ("Correct escalation", "correct_escalation"),
    ):
        add(name, "Offline fixtures", tools[key])
    output = {"captured_on": date.today().isoformat(), "results": results}
    path = Path("frontend/src/benchmark.json")
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
