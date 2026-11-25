# Deployment readiness

The repository has no Dockerfile, Compose file or container entrypoint. The current interactive launcher is localhost-only. Its status and session endpoints check the request host, peer address and any supplied Origin. Session creation also checks `Sec-Fetch-Site` when supplied. The standard `claimpilot.main:app` entry point serves the API and health check. The separate demo launcher registers visitor session endpoints.

## Serve one origin

Build React from the repository root so Vite can resolve the bundled policy content under `data/knowledge`. Serve `frontend/dist` at the origin root with an `index.html` fallback for React Router deep links. Route `/api/v1/*` to internal Uvicorn unchanged and route `/health` explicitly. API errors should retain their original status and response body. Leave `VITE_API_BASE_URL` unset to use the relative `/api/v1` default. Vite's dev and preview proxies apply locally. Production needs explicit routing at the reverse proxy or equivalent ASGI routing.

## Persistence and configuration

Mount a writable volume at a dedicated runtime directory. Keep the following paths on that volume.

| Path or setting | Contents |
| --- | --- |
| Parent of `CLAIMPILOT_INTAKE_DB` | Domain SQLite database, normalized photo bytes, document sections and metadata, proposal and dispatch history, and sibling `.checkpoints.sqlite3` and `.workflow-lock.sqlite3` files. Keep SQLite journal and sidecar files on the same filesystem. |
| `CLAIMPILOT_KB_CHROMA` | Chroma projection. It can be rebuilt from the authoritative SQLite document records. Keep fixture and ONNX indexes separate. |
| `CLAIMPILOT_KB_MODEL_CACHE` | Downloaded and extracted CPU embedding model for live retrieval. Persist or prewarm it so restarts can reuse the cache. |

The core defaults are under `data/runtime`. The interactive runner overrides the domain database and Chroma locations with a fresh `data/runtime/interactive-*` directory on each launch. Its in-memory credentials are lost on restart, even if that directory is preserved. Claim photos live in SQLite and indexed document content is stored as sections and chunks. [Backend settings](../claimpilot/config.py) · [Interactive runner](../examples/frontend_demo.py)

- **Provider configuration.** `CLAIMPILOT_LLM_PROVIDER` selects `offline` or `groq` for the standard app. The demo runner uses `--provider`. `GROQ_API_KEY` is a server-only secret for live inference. `GROQ_MODEL` and `GROQ_VISION_MODEL` select models. `CLAIMPILOT_LLM_TIMEOUT_SECONDS` and `CLAIMPILOT_LLM_MAX_RETRIES` bound provider calls.
- **Role credentials.** `CLAIMPILOT_CLAIMANTS` and `CLAIMPILOT_KB_OPERATORS` are separate JSON arrays with private bearer tokens for named sessions. Inject secrets at runtime. Keep `.env`, secret values and runtime files out of the image, build context and browser bundle.
- **Resource limits.** Backend settings expose upload, photo, chunk, retrieval and tool budget bounds. Match ingress body limits and timeouts to documented endpoint limits. Per-visitor quotas are a separate deployment requirement.

## Capacity

The Windows production frontend build measured 915,818 bytes (about 0.9 MB). The local ONNX download archive measured 83,178,821 bytes (about 79 MiB) before extraction. Linux container footprint, peak RSS, throughput and concurrency remain unmeasured. A starting estimate for a low-traffic, single-worker portfolio demo is **2 vCPU, 2 GiB RAM and 5–10 GiB free disk** for images, dependencies, model cache and bounded runtime data. Groq runs text and vision inference remotely. The server handles document and image decoding, local embeddings and indexing. Peak memory and storage depend on concurrent uploads, index size, visitor retention and logs. Verify cold start, indexing and peak workload on the target host before sizing a public deployment.

## Public interactive sessions

The localhost launcher is unsuitable as an internet-facing session issuer. It uses process-local tokens, has a 25-session lifetime cap per process and lacks expiry and cleanup. A public implementation needs the following controls.

- Issue short-lived, revocable sessions in a dedicated demo environment. Bind both roles to one synthetic workspace and make session creation idempotent within that visitor's session. Authorize every claim, document, photo and operator action server-side. Use a shared or durable session record if sessions must survive a restart.
- Enforce a configured HTTPS host and origin, CSRF protection for cookie-authenticated mutations, secure session transport and trusted proxy boundaries. Keep the localhost check on the existing launcher.
- Add issuance and request rate limits, concurrent-job bounds, per-session upload and data budgets, and provider spending limits. Prefer bundled samples for anonymous traffic. Enable arbitrary uploads and live requests only with explicit cost, abuse and retention controls.
- Expire credentials and purge tenant data, photo bytes, graph checkpoints and Chroma entries on a schedule. Use a clear synthetic-data notice, minimal logs and tests for cross-session access, replay, expired sessions, cleanup and approval bypass.

## Minimum single-server Docker deployment

1. Add a locked frontend build stage and a Python 3.11 backend image with the agents dependency group and bundled synthetic data. Add a `.dockerignore` to exclude local secrets, caches, build outputs and runtime data. Run the backend as a non-root user.
2. Add a reverse proxy to serve the built SPA and terminate TLS. Forward API and health routes to one internal Uvicorn worker. Keep the backend port private. The standard app supports a protected manual-token deployment. Reproducing the offline fixture walkthrough needs the demo runner's fixture configuration in the eventual service entry point.
3. Mount the runtime volume, inject server secrets, add a health check and restart policy, and plan consistent SQLite backups and restore checks. Document how to rebuild Chroma and warm the ONNX cache. Verify deep links, API requests and state across a container restart.
4. Publish the walkthrough video or landing page while the public session issuer is pending. Enable public interactive actions after the session and abuse controls above are implemented and exercised.
