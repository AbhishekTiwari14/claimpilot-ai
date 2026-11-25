import argparse
import logging

from claimpilot.agents.intake import SAMPLE_REPORT, extract_claim
from claimpilot.config import Settings
from claimpilot.services.llm import LLMError, LLMService


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a synthetic property damage report.")
    parser.add_argument("--provider", choices=("offline", "groq"), default="offline")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    llm = LLMService(Settings(claimpilot_llm_provider=args.provider))
    try:
        result = extract_claim(SAMPLE_REPORT, llm)
    except LLMError as error:
        print(f"Extraction failed ({error.code}).")
        return 1
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
