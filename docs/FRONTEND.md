# ClaimPilot interactive demo

From the repository root, start the isolated localhost demo in one PowerShell terminal and Vite in another.

```powershell
uv sync --locked --group agents
uv run --locked --group agents python -m examples.frontend_demo --interactive
```

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173` and select **Try Interactive Demo**. Use the sample report, add the optional sample image, and confirm the details. Continue to Operator Review, bind the suggested v1 policy, run the workflow, approve the proposed contractor, and simulate acceptance or rejection. The optional exclusion and unavailable-contractor scenarios end in human review. Walkthrough data is isolated per launch. Offline inference supports the included reports. Use `--provider groq` with a configured backend key to try arbitrary reports and run live photo analysis. If port 8000 is occupied, pass `--port 8010` and set `$env:CLAIMPILOT_DEV_API_TARGET = 'http://127.0.0.1:8010'` before starting Vite.

The illustration `synthetic-wall.png` is generated from the repository's fixture. The cropped `fema-wall.jpg` is a public-domain FEMA photograph by Patsy Lynch. It is illustrative and comes from a separate incident. [Source and license](https://commons.wikimedia.org/wiki/File:FEMA_-_17647_-_Photograph_by_Patsy_Lynch_taken_on_10-18-2005_in_Louisiana.jpg)

The landing page also starts separate chat, photo analysis, document search and workflow experiences. The document showcase starts with an empty library and restricts each answer to the selected file. Import or download the fictional visitor policy, then try its coverage, exclusion and insufficient-evidence questions. Offline question answering uses named-section fixtures. Arbitrary questions and custom reports can use the live provider.

## Manual access

Run these commands from the repository root in PowerShell. Choose different private local tokens for the claimant and operator. Keep the backend and frontend bound to loopback.

```powershell
uv sync --locked --group agents
$claimant = [Net.NetworkCredential]::new('', (Read-Host 'Choose claimant token' -AsSecureString)).Password
$operator = [Net.NetworkCredential]::new('', (Read-Host 'Choose operator token' -AsSecureString)).Password
$env:CLAIMPILOT_CLAIMANTS = ConvertTo-Json -Compress -InputObject @(@{tenant_id='frontend-demo'; claimant_id='customer'; token=$claimant})
$env:CLAIMPILOT_KB_OPERATORS = ConvertTo-Json -Compress -InputObject @(@{tenant_id='frontend-demo'; operator_id='ops'; token=$operator})
$env:CLAIMPILOT_INTAKE_DB = 'data/runtime/frontend-demo/pilot.sqlite3'
$env:CLAIMPILOT_KB_CHROMA = 'data/runtime/frontend-demo/chroma'
uv run --locked --group agents python -m examples.frontend_demo
```

In a second terminal, run:

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`. Use **Connect session** to enter the matching token on each role's page. Tokens are scoped to the current browser tab. The Vite development and preview proxies forward `/api` to `http://127.0.0.1:8000` by default. Override the upstream with `CLAIMPILOT_DEV_API_TARGET`. For a production build, serve the SPA and reverse-proxy `/api/v1` on the same origin, or configure `VITE_API_BASE_URL` for an authorized API origin. Keep provider keys on the server.

1. As an operator, open **Knowledge base** and select **Load synthetic documents**. In **RAG playground**, choose `SYN-123 · v1` and ask `What does A2 Sudden indoor water discharge say?`
2. Open **Customer Portal**, connect the claimant session, and start a conversation. Report the damage, property ZIP, incident time, policy ID and safety conditions. Review the facts, check the confirmation box, and confirm.
3. Open the claim from **Claims Dashboard**. Bind policy version `v1`, start the workflow, and review its evidence, trace and proposal. Enter a decision reason and approve the exact version. Select **Send simulated dispatch**, then **Simulate acceptance** or **Simulate rejection**. A rejection can produce a replacement proposal requiring a new approval. **Process SLA** checks the stored deadline using the server clock.

The local runner uses lexical fixture embeddings and a policy assessment fixture restricted to the bundled synthetic policy and dishwasher example. For live inference, start it with `--provider groq` and point `CLAIMPILOT_KB_CHROMA` at a separate directory before indexing. The Groq credential comes from the backend environment or root `.env`. The evaluations page displays a generated snapshot from the Phase 9 regression report and offline fixtures. Regenerate it with `uv run --locked --group agents python -m examples.export_frontend_benchmarks --junit PATH_TO_PYTEST_XML`.

Build checks use `npm.cmd --prefix frontend run typecheck` and `npm.cmd --prefix frontend run build`.
