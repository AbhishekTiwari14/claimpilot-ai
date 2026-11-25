"""Serve the local UI demo with deterministic synthetic intake and indexed sources."""
import argparse
from hashlib import sha256
from pathlib import Path

import uvicorn

from claimpilot.config import Settings
from claimpilot.main import create_app
from claimpilot.services import knowledge_answers
from examples.contractors import fixture_embed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--provider", choices=["offline", "groq"], default="offline")
    args = parser.parse_args()
    settings = Settings(claimpilot_llm_provider=args.provider)
    if not settings.claimpilot_claimants or not settings.claimpilot_kb_operators:
        parser.error("Configure CLAIMPILOT_CLAIMANTS and CLAIMPILOT_KB_OPERATORS first.")
    options = {}
    if args.provider == "offline":
        original = knowledge_answers._fixture
        form = Path(__file__).resolve().parents[1] / "data/knowledge/homeowners-a-v1.md"
        form_hash = sha256(form.read_bytes()).hexdigest()

        def demo_assessment(query, evidence, assessment):
            if assessment and all(part in query for part in ("water damage", "dishwasher leaked", "kitchen", "no ongoing hazards")):
                for number, source in enumerate(evidence, 1):
                    if (source.policy_id, source.policy_version, source.section) == (
                        "SYN-123", "v1", "A2 Sudden indoor water discharge",
                    ) and source.content_hash == form_hash:
                        return {"outcome": "potentially_covered",
                                "sources": [{"source_number": number, "category": "coverage"}]}
            return original(query, evidence, assessment)

        knowledge_answers._fixture = demo_assessment
        options = {"embed": fixture_embed, "embedding_id": "frontend-demo-lexical"}
    uvicorn.run(create_app(settings, **options), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
