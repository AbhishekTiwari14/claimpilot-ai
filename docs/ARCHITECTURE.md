# ClaimPilot AI architecture

## Scope and reference research

ClaimPilot handles synthetic property claims and simulated restoration operations. It collects customer reports, grounds policy guidance in versioned clauses, triages urgency, proposes contractors, waits for human approval, tracks service milestones, and produces an audited report. Coverage adjudication, payment, emergency-service contact, and live dispatch are outside this design.

- Insurance Agentic AI documents specialized intake and policy agents, typed boundaries, policy retrieval, and a graph that can interrupt for review. ClaimPilot applies that separation to property restoration with claim-specific evidence and a dispatch gate. [Insurance Agentic AI](https://github.com/mohanhundre1/insurance-agentic-ai#architecture)
- ClaimLens documents bounded tool use, rules that govern routing, human authorization, an append-only event log, and a credential-free evaluation gate. ClaimPilot adopts those patterns using relational claim records and an append-only activity history. Its restoration workflow uses independently specified rules and contracts. [ClaimLens](https://github.com/kb2326/claimlens)
- Research used the public repository documentation and publication listings. ClaimPilot source, workflow, schema and contracts were authored independently.

## V1 interfaces and access

| Interface | V1 scope |
| --- | --- |
| Customer portal | Conversational claim intake, damage photograph uploads, clarification requests, and claim and restoration status for the claimant's own cases. |
| Operations dashboard | Agent execution status, elapsed time and outcomes, evidence review, human dispatch approval, contractor job status, SLA milestones, and ordered audit history. |
| Knowledge base | Preloaded synthetic documents, PDF/DOCX/TXT/MD uploads, document version and indexing management, and a RAG playground with verifiable source citations. |

A workspace is the tenant boundary represented by `tenant_id` in storage. Resolve workspace membership and role from authenticated server context. Claimants see their own claims. Reviewers see operational data within their workspace. Knowledge managers manage workspace documents and can run playground queries against authorized scopes. Playground queries perform read-only retrieval and generation without modifying claim state.

## Runtime boundaries

| Component | Responsibility |
| --- | --- |
| `claimpilot/main.py`, future `api/` | FastAPI liveness, authenticated REST, request limits, DTO validation, role and claim access checks |
| `schemas/` | Pydantic API, agent output, tool I/O, report and event contracts |
| `agents/` | Intake extracts candidate facts and quotes. Policy summarizes cited clauses. Triage extracts hazard signals. Report narrative can summarize verified facts. |
| `tools/` | Typed, scoped capabilities with validated inputs and bounded outputs |
| `services/` | Claim merging, retrieval, urgency rules, ranking, review authorization, simulated dispatch and SLA rules |
| `orchestration/` | LangGraph StateGraph nodes, conditional routes, interrupt/resume and worker |
| `storage/` | Versioned SQLite migrations, transactions and repository queries. Chroma policy index adapter. |
| `tests/`, future `evals/` | Unit, graph, API and crash-path tests. Versioned synthetic scenarios and score runner. |

The backend serves health and conversational intake. Phase 2 processes turns synchronously and persists each one before contacting the model. The planned workflow worker will serialize downstream graph execution per claim and scan due SLAs and approval expiry. The future React/TypeScript client uses versioned OpenAPI types, Tailwind and shadcn/ui. [FastAPI settings](https://fastapi.tiangolo.com/advanced/settings/) · [shadcn/ui Vite setup](https://ui.shadcn.com/docs/installation/vite)

### Phase 1 model boundary

`LLMService` owns the ChatGroq integration, strict JSON Schema requests, timeouts, retries, and metadata logging. `GROQ_MODEL` defaults to `openai/gpt-oss-20b`, which Groq lists for strict schema output and local tool calling, with free-plan limits of 30 requests/minute, 1,000 requests/day, 8,000 tokens/minute and 200,000 tokens/day. Exact limits may vary by account. Strict schema extraction and future tool calling use separate requests. `GROQ_VISION_MODEL` is independently configurable and defaults to the documented multimodal `qwen/qwen3.8-27b`. Phase 1 makes text requests. [ChatGroq](https://docs.langchain.com/oss/python/integrations/chat/groq) · [Groq structured outputs](https://console.groq.com/docs/structured-outputs) · [Groq tool support](https://console.groq.com/docs/tool-use/overview) · [Groq free-plan limits](https://console.groq.com/docs/rate-limits) · [Groq vision](https://console.groq.com/docs/vision)

`agents/intake.py` validates a single report and calls the service using the versioned prompt in `prompts/claim_extraction.py`. The report occupies a separate role message as JSON data. Every populated field must match a case-sensitive excerpt in the source. Missing values are null, conflicts retain both excerpts, and the list of missing fields is computed locally. Excerpt validation verifies presence and preserves original wording, while semantic assignment remains a model task. Operational use requires confirmation before promoting candidates to facts.

Offline mode replays the bundled single-report and multi-turn synthetic fixtures through the same validator. Unrecognized offline reports return a typed unsupported-input error. Groq failures return a typed error without substituting a fixture. The service defaults to a 12-second HTTP timeout per attempt and one retry. Retry only for transient failures and respect numeric `Retry-After` when within the five-second retry delay budget. Logs contain the model, operation/prompt version, total latency, retries, outcome and token counts when supplied. Raw prompts, provider errors, keys, and reasoning stay out of application logs. LangSmith tracing is disabled for extraction.

### Phase 2 conversational intake

A conversation and its claim get separate UUIDs. FastAPI authenticates local synthetic claimant tokens from configuration and resolves the workspace and claimant on the server. SQLite scopes reads and writes by both identities. The history returns message UUIDs, roles, timestamps and a pagination sequence. A file-backed SQLite database uses foreign keys, rollback journaling, a schema version and short write transactions. [SQLite transactions](https://docs.python.org/3.11/library/sqlite3.html)

The intake agent sends the latest report and the field it last asked about to the existing extraction service. Earlier evidence stays in SQLite. The rules merge verbatim candidates with message provenance, preserve equal and unrelated answers, surface conflicting values and accept explicit corrections or a direct clarification. The next question is selected from unresolved fields. It can cite the competing answers or previously reported damage areas. The suspected cause is optional and keeps the customer's words of uncertainty.

The required fields are damage type, affected areas, property location, incident time, policy ID and current hazards, including an explicit denial when applicable. Intake collects reported details. Catalog verification, date normalization and downstream urgency classification occur later. The five intake states are `collecting_information`, `awaiting_clarification`, `awaiting_customer_confirmation`, `intake_completed` and `human_assistance_required`. A conservative screen checks explicit immediate danger before any model call and returns a safety-first reply with the human-assistance state. Three consecutive unresolved turns without progress also route to human assistance. An explicit confirmation POST for the current version promotes the snapshot from candidate evidence to customer-confirmed evidence. It confirms what the customer reported, including the uncertainty of a suspected cause.

A write transaction saves the input message and idempotency record before extraction. A second write transaction saves the assistant reply, next state and replay response together. Versions reject stale writes. A repeat with the same key and payload replays the stored response or resumes pending work after failure or restart. Concurrent identical requests may both perform extraction while pending. They can commit only one reply and state change. A failed turn remains in history and can be retried or explicitly replaced. A report that triggers the immediate safety screen can supersede pending routine work. Terminal intake states stop routine writes. Human-assistance routing records the status and reason without contacting an outside service.

Phase 2 uses `claims` with a versioned intake snapshot and pending message metadata, `claim_messages` with ordered history, and `idempotency_requests` scoped by claimant, workspace and route. The following workflow and persistence sections define the downstream design.

### Documents, indexing and source isolation

Use a workspace-scoped document registry as the authoritative record for both seeded and uploaded content. A policy document must bind to exactly one authorized policy version in the same workspace. General knowledge documents use an explicit workspace knowledge scope. Resolve scope server-side and verify the selected policy belongs to the workspace before accepting or indexing its upload. The playground chooses an authorized scope explicitly. Claim policy retrieval always uses the resolved policy version.

Validate file type and size and store uploads under opaque private keys. Parse PDF/DOCX/TXT/MD without executing embedded instructions or macros. Queue indexing automatically after a valid upload and expose pending, indexing, ready, failed and archived states. Use content digests and deterministic chunk IDs for retries. Publish a new revision only after its index is complete. Filter by workspace, scope, and policy version before ranking, then revalidate every hit against the active, authorized SQLite revision. Archiving hides a document from retrieval immediately and schedules index cleanup. Chroma PersistentClient is rebuildable from stored revisions and uses local all-MiniLM-L6-v2 embeddings. [Chroma embeddings](https://docs.trychroma.com/docs/embeddings/embedding-functions)

Citations include document and revision IDs, title, chunk ID, page or section locator, and quoted text. Revalidate them against the retrieved sources before returning answers. Insufficient evidence returns an explicit no-answer result. Uploaded document text is untrusted input to the model. Its content cannot select tools, change permissions, or approve a claim.

### Photograph observations and confirmed facts

Store original uploads privately with authenticated workspace and claim ownership checks, file type and size validation, and restricted access. Create a separate observation record for each model result, with photo ID, model/prompt version, timestamp, visible evidence and uncertainty. A photo may suggest visible damage or an affected area. Cause, incident time, urgency and policy details require independently verified context. The customer portal and operations dashboard label photo observations as unconfirmed. Only an authorized, audited confirmation with appropriate evidence updates the claim fact revision. Conflicting photo and customer evidence stays visible for review.

## Workflow and routing

Persist one LangGraph thread per claim. Validated server-side events resume the relevant interrupt with their stored event ID. Pause nodes contain only the interrupt and return the resume input for validation in the next node. LangGraph may execute a paused node again on resume. [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) · [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

| Node | Decision and outgoing paths |
| --- | --- |
| `extract_intake` | Extract candidates and evidence offsets from the current message. Reject invented evidence, preserve confirmed facts, record contradictions, and validate field types. Go to `evaluate_intake`. |
| `evaluate_intake` | A credible immediate hazard creates a staff alert and shows a predefined safety message even when intake is incomplete. Missing or conflicting required facts go to `await_information`. After three unanswered or inconclusive clarification turns go to `await_review` with reason `incomplete_intake`. Complete intake goes to `retrieve_policy`. |
| `await_information` | Pause with typed field questions. A new claimant message returns to `extract_intake`. A due intake deadline sends the item to staff review. |
| `retrieve_policy` | Resolve the claimant-linked policy version effective on the incident date. Retrieve scoped clauses and validate citations. Missing or ambiguous match, unavailable retrieval, or an exclusion/condition needing interpretation flags staff review. Continue to `assess_urgency`. |
| `assess_urgency` | Derive mitigation needs and urgency from validated signals and a versioned rule table. Immediate hazard or material uncertainty goes to `await_review`. Missing or ambiguous policy evidence also goes to review. Otherwise go to `find_contractors`. |
| `find_contractors` | Filter eligible vendors, rank deterministically and persist the proposed scope and candidate. No suitable vendor goes to `await_review`. A valid proposal goes to `await_approval`. |
| `await_review` | Pause with reasons and supporting evidence. Authorized staff can request clarification, resolve documented facts or policy concerns and rerun evaluation, rerun candidate matching, or close without dispatch with a recorded reason. Overrides carry a reason and evidence. Every dispatch path still requires approval. |
| `await_approval` | Pause on one immutable, expiring proposal. Approval for the exact current snapshot goes to `dispatch`. Rejection goes to `await_review`. Expiry, changed facts, revised scope, or vendor change invalidate the proposal and return to evaluation. |
| `dispatch` | Recheck approval/snapshot/expiry, policy review clearance, vendor eligibility and claim version at execution. Stale or ineligible proposals route back to evaluation. Successful simulated dispatch creates SLA milestones and goes to `await_progress`. Confirmed failure goes to review. An uncertain outcome is reconciled by idempotency key before any new proposal or send. |
| `await_progress` | Pause for stored dispatch status updates and due SLA events. Accept only monotonic status transitions. A breach adds an alert and leaves tracking active. Failed/cancelled jobs go to staff review. Completion goes to `generate_report`. |
| `generate_report` | Compile a versioned JSON report from authoritative facts, clauses, routing decisions, human decisions, dispatch and SLA events. An optional narrative is validated or replaced by a deterministic summary. Persist the report and atomically mark the claim terminal, then end. Staff closure also uses this path. |

Required intake fields are policy reference, incident local date, verified property or service area, reported damage category and description, and active hazard status (`yes/no/unknown`). Unknown hazard status and conflicting hazard evidence require staff review before dispatch. Incident dates cannot be future-dated in the property's local timezone. Exact synthetic policy/location validation happens against the fixture catalog. Hazards with evidence such as active fire, gas smell, live electrical exposure or structural collapse take `emergency_review` precedence. Active water intrusion with no immediate hazard is `urgent`. Contained damage with established evidence is `routine`. Unclear or conflicting evidence is `unknown`. Urgency affects queue priority and SLA thresholds. A reviewer acknowledges emergency conditions and resolves safety clearance before vendor matching. Policy output is advisory and cannot authorize or deny coverage.

Vendor eligibility requires the required trade, service area, active credential and insurance flags and availability in the proposal's time window. Rank eligible vendors by earliest arrival, then distance and stable vendor ID. Initial simulated thresholds measured from dispatch acceptance are acknowledgement and arrival at 1h/4h for urgent work and 4h/24h for routine work. An emergency cleared for mitigation uses urgent thresholds. One completion deadline comes from the approved proposal. Breached milestones remain in the report after fulfillment. Fixtures use an injectable UTC clock.

### Graph state

Use a TypedDict with JSON primitives and nested Pydantic DTO dumps, validated when entering/leaving nodes. Fields use replacement semantics, and durable collections stay in SQLite. Node guards compare versions before consuming state.

| Field | Shape and ownership |
| --- | --- |
| `schema_version`, `workflow_version`, `claim_id`, `claim_version`, `run_id` | Graph/worker context. Claim version advances on domain transitions. |
| `input_event_id`, `last_applied_event_id` | Durable input and replay cursor. Worker resumes with stored event only. |
| `fact_revision`, `missing_fields`, `conflicting_fields`, `clarification_round` | Validated intake projection. Fact values and evidence remain in domain records. |
| `policy_version_id`, `clause_ids`, `policy_evidence_status` | Retrieval projection. Status is `grounded`, `uncertain` or `unavailable`. |
| `urgency`, `reason_codes`, `rules_version`, `review_item_id` | Deterministic routing and review references. |
| `proposal_id`, `proposal_digest`, `approval_id`, `dispatch_id` | Durable action references, nullable until present. |
| `waiting`, `terminal_reason`, `last_error_code` | Wait carries kind, item ID and optional expiry. Terminal reason is `completed` or `closed_without_dispatch`. |

### Agent output contracts

| Agent | Validated output |
| --- | --- |
| Intake | Phase 1 `IntakeExtraction` contains nullable verbatim fields, quoted contradictions and computed missing information. Durable intake will associate verified excerpts with the message ID and source offsets before confirming or merging facts. |
| Policy | `PolicyEvidenceAssessment` with cited clause IDs and bounded rationale, issue codes for exclusions/conditions, and unresolved questions. Validation removes unsupported evidence and fails the assessment to review when material. |
| Triage | `DamageSignals` with damage category, hazard enum, active intrusion flag, proposed trades and span references. The rule service derives urgency and approved trade scope. |
| Reporting | `ReportNarrative` with a brief summary and referenced facts, decisions or milestones. The report service owns the actual record and rendering authorization. |

## Tool contracts

All tools take strict typed inputs and return a typed result or a stable error code (`invalid_input`, `forbidden`, `not_found`, `stale`, `unavailable`, `timeout`, `outcome_unknown`). Tenant, claim, authorization, operation key and clock are injected by the service. Policy agents may select only the read tools for searching or fetching evidence, capped at three calls per turn. Remaining operations are invoked by graph/service code.

| Tool | Inputs | Successful result and guarantees |
| --- | --- | --- |
| `resolve_policy` | verified policy reference, incident date | Unique active version ID, effective window, content digest. Zero or overlapping versions flagged. |
| `search_policy_clauses` | scoped policy version, bounded query, limit 1–8 | Ranked clause IDs, distance and cited excerpts. Filter by tenant and exact version before ranking. |
| `get_policy_clause` | scoped policy version, clause ID | Exact text, section, document version and digest. Cross-policy IDs rejected. |
| `find_contractors` | service area, trade, arrival window | Bounded list of eligible IDs, ETA, distance, credential state and snapshot time. Deterministic order. |
| `create_dispatch` | approved proposal ID and digest, approval ID, operation key | One simulated job ID, accepted time and status. Same key and payload returns the original result. A mismatched payload returns a conflict. |
| `get_dispatch` | operation key | Authoritative simulator outcome and status version for reconciliation after a timeout. |
| `check_sla` | dispatch ID and injected current time | Due unmet milestone IDs and breach timestamps. Writes are handled in an idempotent service transaction. |

Agent outputs use strict Pydantic fields, bounded enums, explicit unknowns and evidence references. Treat all customer text, policy text and vendor content as data in prompts. Verify quoted spans and clause IDs against stored sources. Tools are local, scoped, allowlisted and accept neither arbitrary URLs nor SQL. The workflow will apply a node-level time budget around the Phase 1 provider call. Invalid structured output becomes a typed failure that the workflow can route to clarification or review. Retrieval without verified evidence and failed extraction can produce clarifications or staff review. No fabricated facts or citations. Dispatch retries use the same operation key after lookup. A permanently unknown external outcome requires reconciliation. [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/)

## Persistence design

Use Python sqlite3 with versioned migrations, foreign keys enabled on each connection, bounded lock timeout and short write transactions. Start with rollback journaling for domain state. Any switch to WAL must verify the runtime SQLite includes the WAL-reset fix before concurrent use. The separate LangGraph SQLite checkpointer manages its own tables and journaling. Verify the same fix before enabling concurrent access to that file. Chroma is a rebuildable projection. [SQLite journaling and concurrency](https://sqlite.org/wal.html) · [LangGraph SQLite checkpointer](https://pypi.org/project/langgraph-checkpoint-sqlite/)

IDs are server-generated UUID text unless otherwise stated. UTC times are RFC 3339 at fixed precision. The `incident_date` is ISO local date. Tenant and actor IDs come from authenticated context. API reads and writes check claim ownership or reviewer role. Foreign keys on tenant-scoped entities include tenant ID or are verified by the service in the write transaction. JSON columns store versioned typed objects. Required fields are non-null unless marked optional below.

| Table | Columns, keys and constraints |
| --- | --- |
| `claims` | `id PK, tenant_id, claimant_id, status, version, fact_revision, thread_id UNIQUE, created_at, updated_at`. Index tenant/claimant/status and tenant/updated time. |
| `claim_messages` | `id PK, claim_id FK, actor_id, role, text, created_at`. Index claim/time/id. |
| `claim_facts` | `claim_id FK, revision, facts_json, provenance_json, created_at`. PK claim/revision. Provenance pairs values with message IDs and validated spans or reviewer decision evidence. |
| `policy_versions` | `id PK, tenant_id, policy_ref, property_ref, effective_from, effective_until, content_digest`. Unique tenant/policy/effective_from. Effective intervals include the start and exclude the end. The service rejects overlaps. |
| `policy_clauses` | `id PK, policy_version_id FK, document_chunk_id FK, section, text, digest`. Index version/section. |
| `knowledge_documents` | `id PK, tenant_id, scope, policy_version_id?, title, active_revision_id?, archived_at?, created_at`. Require policy version for policy scope and enforce the same workspace in the write transaction. |
| `document_revisions` | `id PK, document_id FK, revision, media_type, storage_key, digest, indexing_state, error_code?, created_at`. Unique document/revision. |
| `document_chunks` | `id PK, revision_id FK, ordinal, locator_json, text, digest`. Unique revision/ordinal. Workspace and policy scope derive from the document in SQLite and are copied to index metadata. |
| `claim_photos` | `id PK, claim_id FK, tenant_id, storage_key, media_type, size_bytes, digest, uploaded_by, created_at`. The claim and photo share the workspace. |
| `photo_observations` | `id PK, photo_id FK, model, prompt_version, observations_json, created_at`. An observation remains separate from fact revisions. Audited fact confirmations may reference it as supporting evidence. |
| `contractors` | `id PK, tenant_id, name, trades_json, areas_json, active, credential_valid_until, insurance_valid_until, availability_json, version`. Index tenant/active. |
| `review_items` | `id PK, claim_id FK, reason_codes_json, evidence_json, status, created_at, resolved_at?, resolution_json?`. One open item per claim. |
| `dispatch_proposals` | `id PK, claim_id FK, contractor_id FK, basis_claim_version, fact_revision, snapshot_json, digest, expires_at, status, created_at`. One pending proposal per claim. Immutable snapshot includes scope, policy clearance, vendor version, ETA and completion deadline. |
| `approval_decisions` | `id PK, proposal_id FK UNIQUE, reviewer_id, outcome, reason, proposal_digest, decided_at`. Outcomes approve/reject. Immutable. |
| `dispatches` | `id PK, claim_id FK, proposal_id FK UNIQUE, approval_id FK UNIQUE, operation_key UNIQUE, external_ref? UNIQUE, status, status_version, accepted_at?`. One active dispatch per claim. |
| `simulated_dispatch_jobs` | `operation_key PK, payload_digest, job_ref UNIQUE, status, status_version, accepted_at`. Persistent simulator ledger. |
| `sla_milestones` | `id PK, dispatch_id FK, kind, due_at, fulfilled_at?, breached_at?`. Unique dispatch/kind. Index unmet due times. |
| `reports` | `id PK, claim_id FK, version, schema_version, cutoff_audit_seq, body_json, created_at`. Unique claim/version. |
| `audit_events` | `id PK, claim_id FK, seq, actor_type, actor_id?, kind, run_id?, request_id?, payload_json, created_at`. Unique claim/seq. Append via service. Database triggers reject updates and deletes. |
| `workflow_events` | `id PK, claim_id FK, dedupe_key, kind, payload_json, status, attempts, next_attempt_at, lease_until?, lease_owner?, last_error_code?, created_at`. Unique claim/dedupe_key. Index runnable status/time. The claim sequence follows insertion order using the associated audit sequence in payload. Lease owner includes a unique fencing token. |
| `workflow_effects` | `workflow_event_id FK, step_key, claim_id FK, result_json, applied_at`. PK workflow_event_id/step_key. Committed with the corresponding domain and audit changes. |
| `idempotency_requests` | `tenant_id, actor_id, route_scope, key, request_digest, response_code, response_json, created_at`. Composite PK tenant/actor/route/key. |

Use database checks for enum states and nonnegative versions/counts and deterministic uniqueness for side effects and audit events from replayed steps. Domain mutations append audit and work items in the same transaction. Apply workflow event and node effects under unique event/node keys and use absolute, versioned updates so replay can return the stored result. The checkpoint and domain writes use separate transactions. On restart the worker resolves in-flight input from committed operation outcomes and the graph checkpoint before moving to the next input. Mark an input complete only after its checkpoint reaches the next pause or terminal step. Use a single worker with a renewable lease, conditional state updates, and a fencing token on domain writes. Restart tests must cover both sides of the checkpoint/transaction boundary. Use primitive checkpoint payloads and the checkpointer's restricted deserialization mode.

Approval creation and dispatch reservation verify the proposed scope, subject, digest, expected version, validity window and reviewer. Reuse one reservation with its original operation key after a crash. Vendor/scope or material fact changes before reservation invalidate approval. Serialize reservation with the claim update and treat the accepted reservation as the start of dispatch. Subsequent changes go through staff and job status handling. The simulator commits the same idempotent outcome before returning it to the graph.

## API contract

The current API serves liveness and the Phase 2 intake endpoints. Future workflow, knowledge and photo routes are planned under `/api/v1`. Use JSON and Pydantic-generated OpenAPI. Local synthetic users receive development bearer credentials configured outside Git. Roles are claimant, reviewer, knowledge manager and simulator. The server derives identity, tenant and permissions. A claim's owner can view its customer projection. Reviewers in the tenant can view operational evidence and audit data. Simulator credentials are scoped to simulator events only.

| Endpoint | Input | Success |
| --- | --- | --- |
| `GET /health` | None | 200 `{ "status": "ok" }`, process liveness |
| `POST /conversations` | No body | 201 `IntakeTurn` with claim and initial reply, plus conversation Location header |
| `POST /conversations/{id}/messages` | `{message, expected_version, replace_failed_message?}` | 200 `IntakeTurn` with updated claim and reply |
| `GET /conversations/{id}` | `after?, limit?` | 200 paged conversation history |
| `POST /claims/{id}/confirm` | `{confirmed: true, expected_version}` | 200 `IntakeTurn` for the confirmed claim |
| `GET /claims` | `status?, cursor?, limit?` | 200 cursor page of actor-scoped claim summaries |
| `GET /claims/{id}` | None | 200 `ClaimView` with intake status, candidate or confirmed evidence, unresolved fields and summary when ready |
| `GET /review-queue` | `kind?, cursor?, limit?` | 200 cursor page of open review items and proposals, reviewer only |
| `POST /claims/{id}/reviews/{review_id}/decision` | `{action, reason, expected_version, resolution?}`. Actions `request_information`, `resolve_and_retry`, `retry_match`, `close`. Resolution may supply validated evidence-backed facts or acknowledgements. | 202 `ClaimView`, reviewer only |
| `POST /claims/{id}/proposals/{proposal_id}/decision` | `{outcome, reason, proposal_digest, expected_version}` | 202 `ClaimView`, reviewer only |
| `POST /simulator/dispatches/{dispatch_id}/events` | `{external_event_id, status, status_version, occurred_at}` | 202 `{event_id}`, simulator only. Server validates status against simulator ledger. |
| `GET /claims/{id}/audit` | `after_seq?, limit?` | 200 cursor page of ordered audit events, reviewer only |
| `GET /claims/{id}/report` | `version?` | 200 typed report or 404 while unavailable. The customer view excludes internal-only evidence and actor IDs |
| `POST /claims/{id}/photos` | Multipart image | 202 photo metadata and queued work |
| `GET /claims/{id}/photos` | `cursor?, limit?` | 200 scoped photo page with observation and confirmation status |
| `GET /claims/{id}/photos/{photo_id}/content` | None | 200 authorized image bytes |
| `GET /claims/{id}/runs` | `cursor?, limit?` | 200 reviewer-only execution metadata |
| `POST /knowledge/documents` | Multipart file, title, scope, policy version when required | 202 document and pending revision, knowledge manager only |
| `GET /knowledge/documents` | `scope?, policy_version_id?, indexing_state?, cursor?, limit?` | 200 authorized document metadata page |
| `GET /knowledge/documents/{id}` | None | 200 authorized document metadata and revisions |
| `POST /knowledge/documents/{id}/revisions` | Multipart replacement or stored revision ID for reindexing | 202 queued revision work, knowledge manager only |
| `POST /knowledge/documents/{id}/archive` | Expected active revision ID | 202 archived state, knowledge manager only |
| `POST /knowledge/playground/query` | `{query, scope, policy_version_id?, limit?}` | 200 answer or no-answer status with verified citations |

`ClaimView` includes ID, version, status, timestamps, allowed facts, urgency and an optional wait object with kind, item ID, questions or expiry. Reviewer views may include reason codes, clause citations, proposal and SLA milestones. Status values are `queued`, `processing`, `awaiting_information`, `awaiting_review`, `awaiting_approval`, `tracking`, `completed`, `closed_without_dispatch`. Simulator transitions go from `accepted` through `acknowledged`, `arrived`, `completed` or a permitted failure/cancellation. Monotonic versions and unique external event IDs prevent replay or regression. Report fields are schema version, claim ID, fact revision, facts/provenance, policy status/citations, urgency/rule decisions, human approvals/reviews, dispatch timeline, SLA outcomes and closure reason with an audit cutoff.

Use required `Idempotency-Key` (1–128 characters) on every POST. Same actor/route/key plus canonical request digest returns the original acknowledgement. Different body or stale `expected_version` returns 409. Use 401 for missing credentials, 403 for insufficient role on permitted resources and 404 for out-of-scope resources. Errors use `{code, message, request_id, field_errors?}`. Malformed or invalid data returns 422. Limit JSON bodies to 64 KiB, messages to 4,000 characters and list pages to 100 items. Multipart endpoints enforce separate streamed byte limits and include a file digest in request idempotency. Ordering uses stable cursors and IDs. A 202 means input committed and work queued.

## Audit and observability

Record accepted inputs, structured extraction and source references, policy evidence IDs, model/tool metadata and failures, deterministic rule transitions, human decisions, dispatch attempts/outcomes, SLA changes and report generation. Use per-claim monotonic sequence numbers and unique operation-derived event IDs. Logs carry request, claim, run, node/tool, elapsed time, retry count and outcome. Store decision reasons and evidence references with access restrictions. Exclude credentials, raw model reasoning, raw prompts and unnecessary customer text from logs. Emit local counts for queue depth, time-to-review, model errors, rejected outputs, dispatch outcomes and SLA breaches. Append-only database controls protect application history against accidental edits. Scope remains a local synthetic pilot.
