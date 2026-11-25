import argparse
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from claimpilot.agents.intake_fixtures import DEMO_TURNS
from claimpilot.config import ClaimantCredential, Settings
from claimpilot.main import create_app


def checked(response):
    if not response.is_success:
        raise SystemExit(f"Intake request failed: HTTP {response.status_code} {response.json()['code']}")
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a complete synthetic intake through FastAPI.")
    parser.add_argument("--provider", choices=("offline", "groq"), default="offline")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = "local-synthetic-intake-demo"
    with TemporaryDirectory() as directory:
        settings = Settings(
            claimpilot_llm_provider=args.provider,
            claimpilot_intake_db=Path(directory) / "intake.sqlite3",
            claimpilot_claimants=[
                ClaimantCredential(tenant_id="demo", claimant_id="claimant", token=token)
            ],
        )
        with TestClient(
            create_app(settings), headers={"Authorization": f"Bearer {token}"}
        ) as client:
            turn = checked(client.post("/api/v1/conversations", headers={"Idempotency-Key": "start"}))
            conversation = f"/api/v1/conversations/{turn['claim']['conversation_id']}"
            claim = f"/api/v1/claims/{turn['claim']['claim_id']}"
            print(f"Agent: {turn['reply']['text']}", flush=True)
            for number, message in enumerate(DEMO_TURNS, start=1):
                print(f"Customer: {message}", flush=True)
                turn = checked(client.post(
                    f"{conversation}/messages",
                    headers={"Idempotency-Key": f"turn-{number}"},
                    json={"message": message, "expected_version": turn["claim"]["version"]},
                ))
                print(f"Agent: {turn['reply']['text']}", flush=True)
            assert turn["claim"]["status"] == "awaiting_customer_confirmation", turn["claim"]["status"]
            print(json.dumps(turn["claim"]["summary"], indent=2), flush=True)
            print("Customer: I confirm the claim summary.", flush=True)
            final = checked(client.post(
                f"{claim}/confirm",
                headers={"Idempotency-Key": "confirm"},
                json={"confirmed": True, "expected_version": turn["claim"]["version"]},
            ))
            print(f"Agent: {final['reply']['text']}")
            stored = checked(client.get(claim))
            history = checked(client.get(conversation))
            assert stored["status"] == final["claim"]["status"] == "intake_completed"
            assert all(field["status"] == "customer_confirmed" for field in stored["facts"].values())
            print(f"Result: {stored['status']}, {len(history['messages'])} persisted messages")


if __name__ == "__main__":
    main()
