import argparse
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from claimpilot.agents.photo_fixtures import sample_image
from claimpilot.config import ClaimantCredential, Settings
from claimpilot.main import create_app


def checked(response):
    if not response.is_success:
        body = response.json()
        raise SystemExit(f"Request failed: HTTP {response.status_code} {body.get('error_code') or body.get('code')}")
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload and analyze a single non-sensitive damage photo.")
    parser.add_argument("--provider", choices=("offline", "groq"), default="offline")
    parser.add_argument("--photo", type=Path, help="Optional local image. Required for Groq verification.")
    args = parser.parse_args()
    if args.provider == "groq" and args.photo is None:
        parser.error("--photo is required in Groq mode")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = "local-synthetic-photo-demo"
    with TemporaryDirectory() as directory:
        settings = Settings(
            claimpilot_llm_provider=args.provider,
            claimpilot_intake_db=Path(directory) / "intake.sqlite3",
            claimpilot_claimants=[
                ClaimantCredential(tenant_id="demo", claimant_id="claimant", token=token)
            ],
        )
        with TestClient(create_app(settings), headers={"Authorization": f"Bearer {token}"}) as client:
            turn = checked(client.post("/api/v1/conversations", headers={"Idempotency-Key": "start"}))
            claim = f"/api/v1/claims/{turn['claim']['claim_id']}"
            data = args.photo.read_bytes() if args.photo else sample_image()
            photo = checked(client.post(
                f"{claim}/photos", headers={"Idempotency-Key": "upload"},
                files={"photo": ("sample", data, "application/octet-stream")},
            ))
            analysis = checked(client.post(
                f"{claim}/photo-analyses", headers={"Idempotency-Key": "analysis"},
                json={"photo_ids": [photo["id"]], "context": "The customer reports staining on an interior wall."},
            ))
            saved = client.get(f"{claim}/photo-analyses/{analysis['id']}")
            assert checked(saved) == analysis
            assert analysis["status"] == "completed"
            assert checked(client.get(claim))["facts"] == {}
            print(saved.text)


if __name__ == "__main__":
    main()
