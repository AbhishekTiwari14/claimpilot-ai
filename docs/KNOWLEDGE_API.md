# Knowledge base API and evaluation

Use the local SQLite database and Chroma directory configured in `.env.example`. Install the agent dependency group with `uv sync --locked --group agents`. The first real embedding call downloads the Chroma MiniLM ONNX archive into `CLAIMPILOT_KB_MODEL_CACHE` and checks its checksum. Later calls reuse the local CPU model. Keep the SQLite database as the source of truth. Restart and run rebuild if the Chroma projection is lost.

## Reproducible demo

```powershell
uv run --locked --group agents python -m examples.knowledge --runtime-dir data/runtime/kb-demo
uv run --locked --group agents python -m examples.knowledge --runtime-dir data/runtime/kb-demo --skip-seed
uv run --locked --group agents python -m examples.knowledge --runtime-dir data/runtime/kb-demo --skip-seed --provider groq --evaluate --pause 8
```

Running the seed command again reuses the same documents. The two form IDs and the two versions of SYN-123 deliberately have different appliance coverage. Form v2 has conflicting fire deductible clauses. The demo documents are training material with fictional terms. The offline answer fixture only selects a named section or abstains. It still runs real embeddings unless the app is constructed with an injected test embedder. The live example uses the local Groq key and sends synthetic excerpts.

## Start a local API session

Set a separate operator credential in the server terminal. Operator tokens are distinct from claimant tokens. Both authentication and scope are derived from configured credentials.

```powershell
$secret = Read-Host 'Choose a private development operator token' -AsSecureString
$token = [Net.NetworkCredential]::new('', $secret).Password
$env:CLAIMPILOT_KB_OPERATORS = ConvertTo-Json -InputObject @(
    @{ tenant_id = 'demo'; operator_id = 'admin'; token = $token }
) -Compress
uv run --locked --group agents uvicorn claimpilot.main:app --host 127.0.0.1 --port 8000
```

In a client terminal, upload, search, ask, reindex, and delete.

```powershell
$base = 'http://127.0.0.1:8000/api/v1'
$secret = Read-Host 'Enter the same operator token' -AsSecureString
$token = [Net.NetworkCredential]::new('', $secret).Password
$auth = @{ Authorization = "Bearer $token" }
$metadataPath = 'data/runtime/kb-upload-metadata.json'
New-Item -ItemType Directory -Path 'data/runtime' -Force | Out-Null
'{"title":"Synthetic Cedar v1","document_type":"policy","policy_id":"SYN-123","policy_version":"v1"}' |
    Set-Content -LiteralPath $metadataPath -Encoding Ascii
$key = [guid]::NewGuid().ToString()
$curlArgs = @(
    '--fail-with-body', '--silent', '--show-error',
    '-H', "Authorization: Bearer $token", '-H', "Idempotency-Key: $key",
    '-F', 'document=@data/knowledge/homeowners-a-v1.md', '-F', "metadata=<$metadataPath",
    "$base/knowledge/documents"
)
$document = curl.exe @curlArgs | ConvertFrom-Json
$url = "$base/knowledge/documents/$($document.id)"
Invoke-RestMethod -Uri "$base/knowledge/documents" -Headers $auth
Invoke-RestMethod -Uri $url -Headers $auth
$query = @{
    document_type = 'policy'; policy_id = 'SYN-123'; policy_version = 'v1'
    query = 'What does A2 Sudden indoor water discharge say?'; top_k = 5
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$base/knowledge/search" -Headers $auth -ContentType 'application/json' -Body $query
Invoke-RestMethod -Method Post -Uri "$base/knowledge/playground/query" -Headers $auth -ContentType 'application/json' -Body $query
Invoke-RestMethod -Method Post -Uri "$url/index" -Headers ($auth + @{ 'Idempotency-Key' = [guid]::NewGuid().ToString() })
Invoke-RestMethod -Method Post -Uri "$base/knowledge/rebuild" -Headers ($auth + @{ 'Idempotency-Key' = [guid]::NewGuid().ToString() })
Invoke-RestMethod -Method Delete -Uri $url -Headers ($auth + @{ 'Idempotency-Key' = [guid]::NewGuid().ToString() })
```

For operational search use `document_type=operations` with no policy fields. For claim assessment, complete and confirm a claim using the intake API. An operator in that workspace sends `POST /api/v1/claims/{id}/policy-binding` with `{"policy_version":"v1"}` and an idempotency key, then `POST /api/v1/claims/{id}/policy-assessment`. The claim's confirmed policy ID and owner determine scope. Operators can upload owner-restricted policy documents using `subject_claimant_id`. Standalone search excludes those documents. Claims use only shared forms and documents for their own claimant.

Upload requires one document, a JSON metadata field and an idempotency key. It reserves a pending record and attempts synchronous indexing. A failed indexing attempt is reflected in the returned document status and may be retried with a new key. An interrupted indexing attempt may be resumed with the original key. Rebuild reindexes the authorized workspace snapshot using the current configuration. Deleted and incomplete generations are excluded by SQLite before and after vector search. Backend failures during search or generation return 503. Verified answer citations are exact excerpts presented with trusted source metadata. An answer can still select text that is irrelevant to the question, so citation validity and relevance are evaluated separately.

## Recorded Phase 4 evaluation

The [labeled dataset](../data/knowledge/evaluation.json) includes four answerable questions, one conflicting-policy case, one unsupported question and two out-of-scope questions. The final run used local all-MiniLM-L6-v2 ONNX embeddings with 384 dimensions, ChromaDB 1.5.9 and live GPT-OSS 20B. It reopened the Windows Chroma directory in a new process with `--skip-seed`. The four documents were originally uploaded through the API.

| Measure | Final result |
| --- | --- |
| Retrieval Hit@5 on cases with relevant labels | 5/5 |
| All labeled sections present in the top five | 5/5 |
| Citations with valid IDs and exact stored excerpts | 6/6 |
| Expected answer outcomes, including scope isolation | 8/8 |
| Required evidence terms present in cited text | 5/5 |

Hit@5 counts at least one labeled section. The support check compares required terms against the cited text. Manual inspection confirmed the relevant appliance terms, both competing fire deductibles and the urgent acknowledgement deadline. These results describe this eight-case corpus. The regular test suite injects deterministic lexical embeddings while exercising actual local Chroma persistence and SQLite authorization.

The first live run reached 5/8 expected outcomes. The shared model contract allowed claim-assessment categories in standalone answers, and the validator rejected them. The final run used separate contracts for playground questions and policy assessment, with numbered model selections resolved to whole stored clauses.
