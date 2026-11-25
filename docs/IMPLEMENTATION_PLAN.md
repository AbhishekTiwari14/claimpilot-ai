# ClaimPilot AI implementation plan

Implement phases in order and verify each phase before starting the next. Use synthetic claims, policies, contractors and events throughout. A phase ends with recorded commands, results, open issues and a review of its acceptance criteria.

## Phase 0 — Architecture and backend bootstrap

- Document boundaries, routing, persistence, contracts and failure behavior in [ARCHITECTURE.md](ARCHITECTURE.md).
- Establish Python 3.11, an isolated virtual environment, declared dependencies, local environment settings and Git exclusions.
- Implement GET /health and test the HTTP status and JSON body with FastAPI TestClient.
- Fresh environment resolves the declared dependencies. The locked bootstrap starts with no model credentials, a real localhost request returns HTTP 200 with {"status":"ok"}, and the initial Pytest suite passes.

### Local setup from the repository root

Use uv with CPython 3.11. [uv installation](https://docs.astral.sh/uv/getting-started/installation/) uv can provision the interpreter if needed. `uv sync` creates `.venv`. Activation is optional. Copy `.env.example` to `.env` to customize local settings. Environment variables take precedence over the dotenv file. The health endpoint works with built-in defaults and no credentials.

```powershell
uv sync --locked --group agents
uv run --locked pytest -q
uv run --locked uvicorn claimpilot.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, run `Invoke-RestMethod http://127.0.0.1:8000/health` or `curl http://127.0.0.1:8000/health`. Use `uv sync --locked` for API and test dependencies only. The agents group installs the selected graph, Groq and retrieval libraries. Agent execution and embedding downloads begin in later phases. [uv project workflow](https://docs.astral.sh/uv/guides/projects/) · [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)

## Phase 1 — Claim intake and durable execution

Create domain migrations, local authenticated actors, claim/message API, workflow event queue, audit projection and versioned LangGraph intake nodes. Build the offline extractor and configurable Groq adapter. Validate and merge evidence-backed facts, ask missing-field questions, and persist/resume with a stable thread ID.

- A synthetic multi-turn claim reaches the complete-intake milestone. Missing, conflicting and fabricated fields remain unresolved until corrected. Duplicate requests return the stored result, ownership checks block cross-tenant reads, and concurrent versioned submissions yield a conflict.
- Process restart during a clarification wait or across either checkpoint/transaction boundary preserves progress, and replay produces no duplicate fact revisions or audit events.
- Malformed model output and provider timeouts route to a bounded clarification or review. Offline tests run with networking disabled after dependencies are installed.

## Phase 2 — Policy evidence and urgency

Add synthetic versioned policy fixtures, a rebuildable Chroma index using local embeddings, read-only policy tools, citation verification, triage rules, reviewer resolution and alerts. Test model tool selection under an allowlist and a budget.

- Retrieval filters policy version, date and tenant, and each citation resolves to exact stored text. Empty, ambiguous, conflicting or unavailable evidence routes to staff review. Citation and instruction-injection fixtures cannot escape approved scope.
- Immediate hazards receive the highest review priority even when data is incomplete. Unknown or conflicting hazard state requires staff clearance. Deterministic urgency matches every hard-gate fixture.
- Rebuild the index from SQLite and pass the retrieval thresholds below against cached local weights.

## Phase 3 — Contractor proposals, approval and simulated dispatch

Add synthetic vendor catalog, deterministic ranker, review queue API and immutable expiring proposals. Implement human decisions and the idempotent simulator with persisted outcomes and dispatch reconciliation.

- All returned candidates match eligibility rules, and equal-scoring candidates keep stable order. The workflow opens a review item when no eligible contractor is available.
- Only a current, authorized, unexpired approval for the bound snapshot can reserve dispatch. Rejection, expiry, replay, scope change, fact change and mismatched actor or digest are covered.
- Restart before reservation, after reservation and after simulator acceptance. Repeated or concurrent request delivery creates one job, one dispatch result and one transition per operation. A timeout with unknown outcome is resolved by key or remains in reconciliation.

## Phase 4 — SLA tracking, reporting and release evaluation

Add simulator progress events, due-event scanner, SLA milestones, structured report API and operational/audit views. Build the end-to-end offline evaluation harness and optional Groq comparison.

- An injected clock drives SLA tests without real waits. Duplicate and out-of-order callbacks preserve monotonic state. Each breach is recorded once and completion retains breach history.
- Completed and closed claims yield reports that reconcile with domain records and their audit cutoff. Reports respect role-based redaction. Optional model summaries use a deterministic fallback.
- The offline suite completes the entire synthetic workflow with zero API keys. Scorecards include dataset, rules and prompt versions, provider/model, scenario counts, failure reasons and latency percentiles.

## Phase 5 — Frontend

Build the React/TypeScript client with Tailwind and shadcn/ui against generated OpenAPI types. Add claimant conversation, reviewer inbox, explicit approval UI, SLA timeline and structured reports.

- Browser tests cover staged intake, human approval, tracking and report views. The UI handles processing, empty, failure and stale-version states. Keyboard users can complete each workflow role. No browser action can impersonate a reviewer or bypass the API gate.

## Automated evaluation gates

Use a versioned held-out synthetic fixture set, starting with at least 30 scenarios across complete and incomplete claims, ambiguous policies, urgent hazards, simultaneous approvals, duplicate and stale events, model/tool errors and injection attempts. Score against labeled outputs with Python, without a model judge. Keep fixed fixture expectations separate from development examples. Add cases to the relevant phase and run the expanded suite at each phase boundary.

| Metric | Proposed acceptance threshold |
| --- | --- |
| Required intake field extraction | Micro F1 ≥ 0.90 on labeled field/value pairs, excluding unanswered fields |
| Clarification quality | ≥ 90% recall of missing/contradictory required fields before routing |
| Policy retrieval | Recall@5 ≥ 0.90 across labeled relevant clause IDs, scoped to the effective policy version |
| Grounding and isolation | 100% of returned citations resolve to the scoped policy and version, zero cross-claim disclosures |
| Urgency and workflow safety | 100% of immediate-hazard cases routed to timely review and zero unauthorized dispatches |
| Durable correctness | 100% of retry/restart scenarios preserve expected claims, approvals, simulator jobs and audit transitions |
| SLA/report reconciliation | 100% agreement with the simulated clock and stored facts, milestones and audit cutoff |

The offline provider is the automated release gate. Optional live Groq runs use the same labeled inputs, compare scores, and report errors and latency independently. Keep access credentials out of CI by default. Test the real local embedding index in retrieval integration tests once weights are cached. A synthetic pilot gate assesses workflow behavior on fixtures and is insufficient to validate live claims handling.
