# Frontend demo reference

Start offline or live interactive mode using the [README](../README.md#run-the-interactive-demo). The interactive runner binds to loopback and creates a fresh database and Chroma directory under `data/runtime` on every launch. Each visitor session has its own synthetic workspace.

If port 8000 is occupied, add `--port 8010` to the backend command and set `$env:CLAIMPILOT_DEV_API_TARGET = 'http://127.0.0.1:8010'` before starting Vite. Its development and preview proxy forwards `/api` to loopback port 8000 by default. See the [deployment audit](DEPLOYMENT.md) for production routing.

`synthetic-wall.png` is generated from a repository fixture. The cropped `fema-wall.jpg` is a public-domain photograph by Patsy Lynch from a separate incident. [FEMA photograph and license](https://commons.wikimedia.org/wiki/File:FEMA_-_17647_-_Photograph_by_Patsy_Lynch_taken_on_10-18-2005_in_Louisiana.jpg)

## Manual token sessions

Use this mode for a named local workspace that persists across restarts. Run from the repository root in PowerShell and choose distinct private tokens.

```powershell
$claimant = [Net.NetworkCredential]::new('', (Read-Host 'Choose claimant token' -AsSecureString)).Password
$operator = [Net.NetworkCredential]::new('', (Read-Host 'Choose operator token' -AsSecureString)).Password
$env:CLAIMPILOT_CLAIMANTS = ConvertTo-Json -Compress -InputObject @(@{tenant_id='frontend-demo'; claimant_id='customer'; token=$claimant})
$env:CLAIMPILOT_KB_OPERATORS = ConvertTo-Json -Compress -InputObject @(@{tenant_id='frontend-demo'; operator_id='ops'; token=$operator})
$env:CLAIMPILOT_INTAKE_DB = 'data/runtime/frontend-demo/pilot.sqlite3'
$env:CLAIMPILOT_KB_CHROMA = 'data/runtime/frontend-demo/chroma'
uv run --locked --group agents python -m examples.frontend_demo
```

Start Vite as in the README. Use **Connect session** to enter the matching token on each role's page. Tokens are scoped to the browser tab. As an operator, open **Knowledge base** and select **Load synthetic documents**. In **RAG playground**, choose `SYN-123` version `v1` and ask `What does A2 Sudden indoor water discharge say?` The confirmed claim appears in the operator dashboard for binding and workflow review.

For manual live mode, add `--provider groq` and use a separate Chroma directory from the offline lexical index. The Groq key stays in the backend environment or root `.env`.
