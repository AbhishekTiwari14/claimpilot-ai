# Conversational intake

## Run the example

From the repository root, use the existing uv lockfile. The CLI creates a temporary SQLite database and drives the FastAPI routes with a synthetic claimant.

```powershell
uv sync --locked --group agents
uv run --locked --group agents python -m examples.conversational_intake
uv run --locked --group agents python -m examples.conversational_intake --provider groq
uv run --locked --group agents pytest -q
```

The first example uses the offline fixtures. The second uses the Groq credential in the environment or local `.env`. Both print the conversation, claim summary and final status. The Groq example sends only the bundled synthetic inputs to the configured provider.

## Exercise the HTTP API

Open PowerShell at the repository root. Configure one synthetic actor and start the server. Keep the same database path and credential configuration when testing a restart.

```powershell
$secret = Read-Host 'Choose a private local synthetic token' -AsSecureString
$demoToken = [Net.NetworkCredential]::new('', $secret).Password
$env:CLAIMPILOT_CLAIMANTS = ConvertTo-Json -InputObject @(
    @{ tenant_id = 'demo'; claimant_id = 'customer'; token = $demoToken }
) -Compress
$env:CLAIMPILOT_LLM_PROVIDER = 'offline'
$env:CLAIMPILOT_INTAKE_DB = 'data/runtime/intake.sqlite3'
uv run --locked --group agents uvicorn claimpilot.main:app --host 127.0.0.1 --port 8000
```

In a second PowerShell terminal run the following against the local server. The transcript endpoint also supports `?after=<sequence>&limit=<page size>`. Use the returned `next_after` as the next cursor.

```powershell
$base = 'http://127.0.0.1:8000/api/v1'
$secret = Read-Host 'Enter the same local synthetic token' -AsSecureString
$demoToken = [Net.NetworkCredential]::new('', $secret).Password
$auth = @{ Authorization = "Bearer $demoToken" }

function Send-IntakePost($url, $key, $payload) {
    $headers = $auth + @{ 'Idempotency-Key' = $key }
    if ($null -eq $payload) {
        return Invoke-RestMethod -Method Post -Uri $url -Headers $headers
    }
    Invoke-RestMethod -Method Post -Uri $url -Headers $headers -ContentType 'application/json' `
        -Body (ConvertTo-Json -InputObject $payload -Compress)
}

$turn = Send-IntakePost "$base/conversations" ([guid]::NewGuid()) $null
$conversation = "$base/conversations/$($turn.claim.conversation_id)"
$claim = "$base/claims/$($turn.claim.claim_id)"
$messages = @(
    'There is water damage in the kitchen.',
    'The damaged property is 12 Example Lane, ZIP 98101.',
    'I noticed it yesterday morning.',
    'My policy ID is SYN-123.',
    'There are no ongoing hazards. I think the dishwasher leaked.'
)
foreach ($message in $messages) {
    $turn = Send-IntakePost "$conversation/messages" ([guid]::NewGuid()) @{
        message = $message
        expected_version = $turn.claim.version
    }
    $turn.reply.text
}
$turn.claim.summary | ConvertTo-Json
$turn = Send-IntakePost "$claim/confirm" ([guid]::NewGuid()) @{
    confirmed = $true
    expected_version = $turn.claim.version
}
Invoke-RestMethod -Uri $claim -Headers $auth
Invoke-RestMethod -Uri $conversation -Headers $auth
```

For a retry test, reuse the original idempotency key and JSON content. On a model failure the API returns HTTP 503, and claim GET exposes `processing_error` and `pending_message_id`. Retry the original input with its original key and version after the provider recovers. To replace a failed input, send a new message with a new key, the current claim version and `replace_failed_message: true`. Both customer messages remain in the history. An identical repeat of a committed request returns its original response. A stale version, changed request for the same key, or unconfirmed summary produces a conflict. Owner and workspace checks apply to every route. Phase 2 uses local development credentials for synthetic data. [FastAPI bearer credentials](https://fastapi.tiangolo.com/tutorial/security/first-steps/)
