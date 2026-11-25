from fastapi.testclient import TestClient

from claimpilot.config import ClaimantCredential
from claimpilot.main import create_app
from examples.contractors import CUSTOMER, OPERATOR, checked, fixture_embed
from test_workflows import OTHER, api, policy_evidence, ready, settings  # noqa: F401


def test_portal_list_and_workflow_evidence_are_scoped(api, settings, policy_evidence):
    claim, _, state = ready(api)
    claim_id = claim["claim_id"]
    path = f"/api/v1/operator/claims/{claim_id}"
    sources_url = f"{path}/workflows/{state['workflow_id']}/policy-evidence"
    detail = checked(api.get(path, headers=OPERATOR))
    assert detail["workflow_ids"] == [state["workflow_id"]] and detail["policy_version"] == "v1"
    assert checked(api.get("/api/v1/operator/claims", headers=OTHER)) == []
    assert api.get(path, headers=OTHER).status_code == 404
    assert api.get(sources_url, headers=OTHER).status_code == 404
    assert api.get(sources_url, headers=CUSTOMER).status_code == 403
    excerpts = checked(api.get(sources_url, headers=OPERATOR))
    assert [item["chunk_id"] for item in excerpts] == state["policy_citations"]
    assert "sudden and accidental discharge" in excerpts[0]["excerpt"]
    assert [item["claim_id"] for item in checked(api.get("/api/v1/customer/claims", headers=CUSTOMER))] == [claim_id]
    token = "other-local-synthetic-claimant"
    settings.claimpilot_claimants.append(ClaimantCredential(tenant_id="demo", claimant_id="different", token=token))
    with TestClient(create_app(settings, embed=fixture_embed, embedding_id="contractor-test-lexical")) as restarted:
        assert checked(restarted.get("/api/v1/customer/claims", headers={"Authorization": f"Bearer {token}"})) == []
        docs = checked(restarted.get("/api/v1/knowledge/documents", headers=OPERATOR))
        doc_id = next(item["id"] for item in docs if item["policy_id"] == "SYN-123")
        assert restarted.delete(f"/api/v1/knowledge/documents/{doc_id}",
                                headers=OPERATOR | {"Idempotency-Key": "delete-form"}).status_code == 204
        assert checked(restarted.get(sources_url, headers=OPERATOR)) == []
