"""Serve the local UI demo with deterministic synthetic intake and indexed sources."""
import argparse
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse

from claimpilot.agents.intake_fixtures import OFFLINE_TURNS
from claimpilot.api.intake import RequestKey
from claimpilot.config import ClaimantCredential, OperatorCredential, Settings
from claimpilot.main import create_app
from claimpilot.services import knowledge_answers
from claimpilot.storage.intake import IntakeError
from examples.contractors import fixture_embed


def enable_interactive(app, settings):
    sessions = {}
    lock = Lock()
    loopback = {"localhost", "127.0.0.1", "::1"}

    def require_local(request: Request):
        origin = request.headers.get("origin")
        if (request.url.hostname not in loopback
            or request.client is None or request.client.host not in loopback
            or (origin and urlsplit(origin).hostname not in loopback)):
            raise IntakeError(403, "demo_local_only", "Interactive demo access is available only on localhost.")

    @app.get("/api/v1/demo/status")
    def status(request: Request):
        require_local(request)
        return {"enabled": True, "provider": settings.claimpilot_llm_provider}

    @app.post("/api/v1/demo/session")
    def session(request: Request, key: RequestKey):
        require_local(request)
        if request.headers.get("sec-fetch-site") not in (None, "same-origin"):
            raise IntakeError(403, "demo_same_origin", "Open the demo from the frontend origin.")
        with lock:
            if key not in sessions:
                if len(sessions) >= 25:
                    raise IntakeError(429, "demo_capacity", "The local demo has reached its session limit.")
                tenant = f"demo-{uuid4().hex}"
                claimant, operator = token_urlsafe(32), token_urlsafe(32)
                app.state.contractor_store.seed({tenant})
                settings.claimpilot_claimants.append(ClaimantCredential(tenant_id=tenant, claimant_id="visitor", token=claimant))
                settings.claimpilot_kb_operators.append(OperatorCredential(tenant_id=tenant, operator_id="reviewer", token=operator))
                sessions[key] = {"claimant": claimant, "operator": operator}
            return JSONResponse(sessions[key], headers={"Cache-Control": "no-store"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--provider", choices=["offline", "groq"], default="offline")
    parser.add_argument("--interactive", action="store_true", help="Enable isolated, localhost-only visitor sessions")
    args = parser.parse_args()
    settings = Settings(claimpilot_llm_provider=args.provider)
    if args.interactive:
        directory = Path("data/runtime") / f"interactive-{uuid4().hex}"
        settings.claimpilot_intake_db = directory / "pilot.sqlite3"
        settings.claimpilot_kb_chroma = directory / "chroma"
        settings.claimpilot_claimants = []
        settings.claimpilot_kb_operators = []
    elif not settings.claimpilot_claimants or not settings.claimpilot_kb_operators:
        parser.error("Configure CLAIMPILOT_CLAIMANTS and CLAIMPILOT_KB_OPERATORS first.")
    options = {}
    if args.provider == "offline":
        original = knowledge_answers._fixture
        knowledge = Path(__file__).resolve().parents[1] / "data/knowledge"
        forms = {
            "SYN-123": ("homeowners-a-v1.md", "A2 Sudden indoor water discharge", "potentially_covered", "coverage"),
            "SYN-456": ("homeowners-b-v1.md", "B2 Appliance water exclusion", "potentially_excluded", "exclusion"),
        }
        hashes = {policy: sha256((knowledge / spec[0]).read_bytes()).hexdigest() for policy, spec in forms.items()}

        def demo_assessment(query, evidence, assessment):
            if assessment and all(part in query for part in ("water damage", "dishwasher leaked", "kitchen", "no ongoing hazards")):
                for number, source in enumerate(evidence, 1):
                    spec = forms.get(source.policy_id)
                    if spec and (source.policy_version, source.section) == ("v1", spec[1]) and source.content_hash == hashes[source.policy_id]:
                        return {"outcome": spec[2], "sources": [{"source_number": number, "category": spec[3]}]}
            return original(query, evidence, assessment)

        knowledge_answers._fixture = demo_assessment
        if args.interactive:
            OFFLINE_TURNS["My policy ID is SYN-456."] = {"policy_id": "SYN-456"}
        options = {"embed": fixture_embed, "embedding_id": "frontend-demo-lexical"}
    app = create_app(settings, **options)
    if args.interactive:
        enable_interactive(app, settings)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
