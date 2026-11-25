# Contractor recommendation API

## Run the synthetic scenarios

Install dependencies with `uv sync --locked --group agents`. The examples use the local configured Groq key for live runs. Each demo creates a SQLite file, seeds 24 fictional contractors and indexes the checked-in synthetic operations handbook. The demo writes its database and Chroma projection under the ignored `data/runtime` directory so that run records survive process exit.

```powershell
uv run --locked --group agents python -m examples.contractors --evaluate
uv run --locked --group agents python -m examples.contractors --provider groq --scenario eligible --full-trace
uv run --locked --group agents python -m examples.contractors --provider groq --scenario no-eligible --full-trace
uv run --locked --group agents pytest -q
```

Offline uses a scripted model selector and deterministic lexical fixture vectors. Live uses native GPT-OSS 20B tool calls and the actual cached MiniLM operations index. Tool execution uses the same SQLite and Chroma logic in both modes. Use a separate runtime directory for each provider if specifying `--runtime-dir`. Groq documents local tool calling for GPT-OSS 20B with sequential calls and separate strict-schema requests. [Groq tool calling](https://console.groq.com/docs/tool-use/overview) · [Groq structured outputs](https://console.groq.com/docs/structured-outputs)

## Exercise the HTTP API

The `get_dispatch_requirements` tool accepts no arguments. Send the empty JSON object `{}`. The server supplies claim and workspace scope.

Start the server with the workspace operator and claimant settings in the [knowledge guide](KNOWLEDGE_API.md) and [intake guide](INTAKE_API.md). Upload the bundled `data/knowledge/restoration-operations.md` file with `document_type=operations`. The agent treats the exact bundled document hash as the approved synthetic guideline source. Arbitrary uploaded instructions cannot change the approval requirement or SLA rule.

Complete intake with a confirmed service ZIP, such as `98101` for the seeded water restoration options. `99501` exercises the empty search. The offline intake fixture accepts the synthetic sentence “The damaged property is 12 Example Lane, ZIP 98101.”

```powershell
$base = 'http://127.0.0.1:8000/api/v1'
$secret = Read-Host 'Workspace operator token' -AsSecureString
$token = [Net.NetworkCredential]::new('', $secret).Password
$auth = @{ Authorization = "Bearer $token" }
$claimId = Read-Host 'Confirmed claim UUID'
$version = [int](Read-Host 'Confirmed claim version')
$claim = "$base/claims/$claimId"
Invoke-RestMethod "$base/contractors" -Headers $auth
Invoke-RestMethod "$base/contractors/CT-001" -Headers $auth
$key = [guid]::NewGuid().ToString()
$run = Invoke-RestMethod -Method Post -Uri "$claim/contractor-recommendations" -Headers (
    $auth + @{ 'Idempotency-Key' = $key }
) -ContentType 'application/json' -Body (@{ expected_version = $version } | ConvertTo-Json)
Invoke-RestMethod "$claim/contractor-recommendations/$($run.id)" -Headers $auth
Invoke-RestMethod "$claim/contractor-recommendations/$($run.id)/trace" -Headers $auth
Invoke-RestMethod "$claim/dispatch-proposals" -Headers $auth
```

The run POST is synchronous and reserves a run before compute. Replaying the same key can resume an interrupted run or return the prior completed run. A changed payload or an outdated claim version receives HTTP 409. Read routes check the operator's workspace. An observed stale proposal changes permanently to `requires_intervention`. Proposal validity includes the claim and policy snapshot, photo analysis set, active handbook citations, contractor records, operating window and a 15-minute expiry. A trace shows original execution, while the proposal endpoint shows current validity.

The response includes a versioned proposal with `awaiting_approval`, `requires_intervention`, or `unsuccessful` status. It carries only an advisory recommendation. Operating hours are represented in UTC for the synthetic records. Routine targets cite the handbook's four-hour acknowledgement rule. High urgency uses one-hour acknowledgement and four-hour arrival. Immediate hazards skip tool execution and require human safety review.

## Evaluation

[The five labeled cases](../data/contractors/evaluation.json) include eligible, active water, no eligible contractor, missing ZIP, and emergency scenarios. The scorer checks required successful tools as a set, so alternative sequences can pass. It records contractor eligibility, success among eligible cases, escalation among other cases and unauthorized proposals. Each trace includes tool-result call IDs appearing in subsequent model requests, output status, timings and provider token counts when supplied.

See the [evaluation summary](EVALUATION.md) for the offline scorecard.

## Recorded live verification

Both GPT-OSS 20B runs used the local operations index. Each recorded tool result, including the rejected call in the eligible run, was sent back to Groq in a subsequent successful request under its original call ID.

| Run | Observed result | Model turns | Input / output / total tokens |
| --- | --- | ---: | ---: |
| `172e9ba6-2c38-4b77-be40-dff015052fbe` | CT-001 with a 45-minute estimate, CT-002 as an alternative, awaiting approval | 8 | 5,046 / 376 / 5,422 |
| `1e032a43-99d5-47fb-88db-9a4b8f1fc987` | Empty search, requires intervention | 3 | 1,514 / 84 / 1,598 |

The eligible run selected search, availability, arrival, performance, requirements and proposal in that order. Its first requirements call contained invalid arguments and was rejected before execution. The next call supplied valid arguments and returned the handbook's O1 dispatch and O2 SLA citations. The empty-search run selected requirements ahead of search and escalated after receiving the empty result.
