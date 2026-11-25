from pathlib import Path
import sqlite3
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_groq import ChatGroq
from pydantic import ValidationError

from claimpilot.config import Settings
from claimpilot.main import create_app
from claimpilot.services.llm import LLMService
from examples.contractors import fixture_embed

ORIGIN = "https://claims.example.com"


def hosted_settings(tmp_path, **overrides):
    return Settings(_env_file=None, **({
        "claimpilot_hosted_demo": True,
        "claimpilot_demo_origin": ORIGIN,
        "claimpilot_llm_provider": "groq",
        "groq_api_key": "mock-key",
        "claimpilot_intake_db": tmp_path / "intake.sqlite3",
        "claimpilot_kb_chroma": tmp_path / "chroma",
    } | overrides))


def launch(api, key=None, **headers):
    return api.post("/api/v1/demo/session", headers={"Idempotency-Key": key or str(uuid4()), **headers})


def auth(tokens, role):
    return {"Authorization": f"Bearer {tokens[role]}", "Idempotency-Key": str(uuid4())}


def upload(api, headers, name="fictional.md", data=b"# Fictional\n\nThis policy covers a fictional kitchen leak."):
    return api.post("/api/v1/knowledge/documents", headers=headers, files={
        "document": (name, data, "text/markdown"),
        "metadata": (None, '{"title":"Fictional","document_type":"operations"}'),
    })


@pytest.mark.parametrize("overrides", [
    {"claimpilot_demo_origin": "http://claims.example.com"},
    {"claimpilot_demo_origin": ORIGIN + "/"},
    {"claimpilot_llm_provider": "offline"},
    {"groq_api_key": None},
])
def test_hosted_demo_requires_https_and_live_configuration(tmp_path, overrides):
    with pytest.raises(ValidationError):
        hosted_settings(tmp_path, **overrides)


def test_hosted_demo_origin_roles_and_isolation(tmp_path):
    app = create_app(hosted_settings(tmp_path), embed=fixture_embed, embedding_id="test-lexical")
    with TestClient(app, base_url=ORIGIN) as api:
        assert api.get("/api/v1/demo/status").json() == {"enabled": True, "hosted": True, "provider": "groq"}
        assert api.get("/api/v1/demo/status", headers={"Host": "untrusted.example"}).status_code == 403
        assert api.get("http://claims.example.com/api/v1/demo/status").status_code == 403
        assert launch(api, Origin="https://untrusted.example").status_code == 403
        assert launch(api, **{"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert launch(api, "guessable").status_code == 422
        assert api.get("/api/v1/operator/claims").status_code == 401
        key = str(uuid4())
        first = launch(api, key, Origin=ORIGIN)
        assert first.status_code == 200 and first.headers["Cache-Control"] == "no-store"
        assert launch(api, key).json() == first.json()
        claimant, operator = (auth(first.json(), role) for role in ("claimant", "operator"))
        second = launch(api).json()
        visitor = auth(second, "claimant")
        outsider = auth(second, "operator")
        created = api.post("/api/v1/conversations", headers=claimant)
        assert created.status_code == 201
        claim = created.json()["claim"]
        claim_url = f"/api/v1/claims/{claim['claim_id']}"
        assert api.get(claim_url, headers=visitor).status_code == 404
        assert api.get("/api/v1/operator/claims", headers=claimant).status_code == 403
        assert api.get("/api/v1/customer/claims", headers=operator).status_code == 401
        assert api.get(f"/api/v1/operator/claims/{claim['claim_id']}", headers=outsider).status_code == 404
        workflow = api.post(f"{claim_url}/workflows", headers=operator, json={"expected_version": claim["version"]})
        assert workflow.json()["reason"] == "confirmed_intake_required"
        document = upload(api, operator)
        assert document.status_code == 201 and document.json()["status"] == "ready"
        assert api.get(f"/api/v1/knowledge/documents/{document.json()['id']}", headers=outsider).status_code == 404
        assert api.get("/api/v1/knowledge/documents", headers=outsider).json() == []


def test_hosted_expiry_and_restart_purge_workspace_data(tmp_path):
    settings = hosted_settings(tmp_path, claimpilot_demo_ttl_seconds=10)
    app = create_app(settings, embed=fixture_embed, embedding_id="test-lexical")
    now = [0]
    demo = app.state.hosted_demo
    demo.clock = lambda: now[0]
    with TestClient(app, base_url=ORIGIN) as api:
        tokens = launch(api).json()
        claimant = auth(tokens, "claimant")
        created = api.post("/api/v1/conversations", headers=claimant).json()["claim"]
        photo = Path(__file__).resolve().parents[1] / "frontend/public/samples/synthetic-wall.png"
        uploaded = api.post(f"/api/v1/claims/{created['claim_id']}/photos", headers=claimant,
                            files={"photo": (photo.name, photo.read_bytes(), "image/png")})
        assert uploaded.status_code == 201
        operator = auth(tokens, "operator")
        workflow = api.post(f"/api/v1/claims/{created['claim_id']}/workflows", headers=operator,
                            json={"expected_version": created["version"]})
        assert workflow.json()["status"] == "awaiting_information"
        assert upload(api, operator).json()["status"] == "ready"
        collection = app.state.knowledge._vector_collection()
        assert collection.count() > 0
        workspace = demo.tokens[tokens["operator"]]
        now[0] = 10
        assert api.get("/api/v1/operator/claims", headers=operator).status_code == 401
        assert api.get("/api/v1/customer/claims", headers=auth(tokens, "claimant")).status_code == 401
        demo.sweep()
        assert workspace.tenant not in {actor.tenant_id for actor in settings.claimpilot_kb_operators}
        assert collection.count() == 0
        with sqlite3.connect(settings.claimpilot_intake_db.with_suffix(".checkpoints.sqlite3")) as db:
            assert db.execute("SELECT count(*) FROM checkpoints").fetchone() == (0,)
            assert db.execute("SELECT count(*) FROM writes").fetchone() == (0,)
        still_active = launch(api).json()
        assert upload(api, auth(still_active, "operator")).json()["status"] == "ready"
        assert collection.count() > 0

    restarted_settings = hosted_settings(tmp_path)
    restarted = create_app(restarted_settings, embed=fixture_embed, embedding_id="test-lexical")
    with TestClient(restarted, base_url=ORIGIN) as api:
        assert api.get("/api/v1/operator/claims", headers=auth(still_active, "operator")).status_code == 401
        assert restarted.state.knowledge._vector_collection().count() == 0
        with sqlite3.connect(settings.claimpilot_intake_db) as db:
            for table in ("claims", "claim_messages", "claim_photos", "idempotency_requests", "knowledge_documents",
                          "knowledge_chunks", "contractors"):
                assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_hosted_workspace_request_upload_and_body_limits(tmp_path):
    app = create_app(hosted_settings(
        tmp_path, claimpilot_demo_active_workspaces=2, claimpilot_demo_workspaces_per_ip_hour=1,
        claimpilot_demo_workspaces_per_hour=2, claimpilot_demo_requests_per_minute=4,
        claimpilot_demo_mutations_per_workspace=2, claimpilot_demo_uploads_per_workspace=1, claimpilot_kb_max_bytes=256,
    ))
    with TestClient(app, base_url=ORIGIN, client=("192.0.2.1", 50000)) as api:
        tokens = launch(api).json()
        assert launch(api).status_code == 429
        operator = auth(tokens, "operator")
        assert upload(api, operator, data=b"x" * 257).status_code == 413
        assert upload(api, operator).json()["code"] == "demo_upload_limit"
        assert api.post("/api/v1/conversations", headers=auth(tokens, "claimant"),
                        content=b"x" * (32 * 1024 + 1)).status_code == 413
        assert api.post("/api/v1/conversations", headers=auth(tokens, "claimant")).json()["code"] == "demo_workspace_limit"
        for _ in range(2):
            assert api.get("/api/v1/knowledge/documents", headers=operator).status_code == 200
        assert api.get("/api/v1/knowledge/documents", headers=operator).json()["code"] == "demo_request_limit"
        for peer, expected in (("192.0.2.2", 200), ("192.0.2.3", 429)):
            # A separate peer on the same running app.
            other = TestClient(app, base_url=ORIGIN, client=(peer, 50000))
            assert launch(other).status_code == expected


def test_hosted_model_budget_counts_provider_retries(tmp_path):
    settings = hosted_settings(tmp_path, claimpilot_demo_model_calls_per_workspace=2, claimpilot_demo_model_calls_per_hour=3)
    attempts = []

    def throttled(request):
        attempts.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"message": "Private provider detail"}})

    with httpx.Client(transport=httpx.MockTransport(throttled)) as transport:
        model = ChatGroq(model=settings.groq_model, api_key="mock-key", http_client=transport, max_retries=0)
        app = create_app(settings, llm=LLMService(settings, chat_model=model))
        with TestClient(app, base_url=ORIGIN) as api:
            for expected, total in (("rate_limited", 2), ("demo_model_limit", 3)):
                tokens = launch(api).json()
                claimant = auth(tokens, "claimant")
                claim = api.post("/api/v1/conversations", headers=claimant).json()["claim"]
                path = f"/api/v1/conversations/{claim['conversation_id']}/messages"
                body = {"message": "A dishwasher leaked in my kitchen.", "expected_version": claim["version"]}
                headers = auth(tokens, "claimant")
                first = api.post(path, headers=headers, json=body)
                assert first.status_code == 429 and first.json()["code"] == expected
                assert "Private provider" not in first.text
                assert len(attempts) == total
                retry = api.post(path, headers=headers, json=body)
                assert retry.status_code == 429 and retry.json()["code"] == "demo_model_limit"
                assert len(attempts) == total


def test_spa_fallback_keeps_api_health_and_assets_separate(tmp_path):
    spa = tmp_path / "spa"
    spa.mkdir()
    (spa / "index.html").write_text("<html>ClaimPilot SPA</html>")
    (spa / "logo.svg").write_text("<svg />")
    settings = Settings(_env_file=None, claimpilot_intake_db=tmp_path / "db.sqlite3", claimpilot_spa_dir=spa)
    with TestClient(create_app(settings)) as api:
        assert api.get("/customer/claims").text == "<html>ClaimPilot SPA</html>"
        assert api.head("/operator").status_code == 200
        assert api.get("/logo.svg").text == "<svg />"
        assert api.get("/assets/missing").status_code == 404
        assert api.get("/api/v1/demo/status").status_code == 404
        assert api.post("/api/v1/missing").status_code == 404
        assert api.get("/api/v1/customer/claims").status_code == 401
        assert api.get("/health").json() == {"status": "ok"}
