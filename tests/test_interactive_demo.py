from fastapi.testclient import TestClient

from claimpilot.config import Settings
from claimpilot.main import create_app
from examples.frontend_demo import enable_interactive


def test_demo_is_opt_in_local_and_scoped(tmp_path):
    settings = Settings(_env_file=None, claimpilot_llm_provider="offline", claimpilot_intake_db=tmp_path / "demo.sqlite3")
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 40000)) as client:
        assert client.get("/api/v1/demo/status").status_code == 404
    app = create_app(settings)
    enable_interactive(app, settings)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 40000)) as client:
        path = "/api/v1/demo/session"
        key = {"Idempotency-Key": "demo-test"}
        assert client.post(path, headers=key | {"Origin": "https://example.com"}).status_code == 403
        assert client.post(path, headers=key | {"Host": "example.com"}).status_code == 403
        first = client.post(path, headers=key)
        assert first.status_code == 200 and first.headers["Cache-Control"] == "no-store"
        assert client.post(path, headers=key).json() == first.json()
        tokens = first.json()
        other = client.post(path, headers={"Idempotency-Key": "another-demo"}).json()
        assert tokens["operator"] != tokens["claimant"] != other["claimant"]
        customer = {"Authorization": f"Bearer {tokens['claimant']}"}
        operator = {"Authorization": f"Bearer {tokens['operator']}"}
        outsider = {"Authorization": f"Bearer {other['operator']}"}
        created = client.post("/api/v1/conversations", headers=customer | key)
        assert created.status_code == 201
        claim_id = created.json()["claim"]["claim_id"]
        assert client.get("/api/v1/operator/claims", headers=customer).status_code == 403
        assert client.get("/api/v1/customer/claims", headers=operator).status_code == 401
        assert client.get(f"/api/v1/operator/claims/{claim_id}", headers=operator).status_code == 200
        assert client.get(f"/api/v1/operator/claims/{claim_id}", headers=outsider).status_code == 404
