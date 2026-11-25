# Deploy ClaimPilot AI

The Docker image serves React and FastAPI on port 8000 with one non-root Uvicorn worker. React deep links such as `/customer/claims` load the SPA. The API is under `/api/v1` and the container uses `/health` for its health check. The image builds from `frontend/package-lock.json` and `uv.lock`. Vite uses the relative API URL.

## Public demo on one Linux host

Point your domain at the host. Use a host HTTPS reverse proxy that preserves the public `Host` header and sets trusted forwarded scheme and client IP headers. Keep port 8000 private. From the repository root, run the following commands on a standard Docker bridge network.

```sh
cp .env.docker.example .env.docker
chmod 600 .env.docker
${EDITOR:-vi} .env.docker

docker build -t claimpilot-ai .
docker volume create claimpilot-runtime
PROXY_IP=$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')
docker run -d --name claimpilot --restart unless-stopped \
  --env-file .env.docker --env "FORWARDED_ALLOW_IPS=$PROXY_IP" \
  -p 127.0.0.1:8000:8000 -v claimpilot-runtime:/runtime claimpilot-ai
```

In the environment file, set `CLAIMPILOT_DEMO_ORIGIN` to your public HTTPS origin without a trailing slash and enter `GROQ_API_KEY`. Use the proxy address as seen by the container if it differs from the bridge gateway above. Trust only that proxy address or a dedicated proxy subnet. The hosted API requires the configured HTTPS origin and a dedicated deployment with empty manual credential lists. The existing localhost runner stays available via `uv run --locked --group agents python -m examples.frontend_demo --interactive`.

For example, with Caddy already installed on the host, use this Caddyfile. Merge the global server options into the existing global block if there is one. Adjust the proxy body limit if you raise the upload limits. [Caddy server timeouts](https://caddyserver.com/docs/caddyfile/options#timeouts) · [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

```caddyfile
{
    servers :443 {
        timeouts {
            read_header 10s
            read_body 30s
        }
    }
}

claims.example.com {
    request_body {
        max_size 9MiB
    }
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo caddy reload --config /etc/caddy/Caddyfile
curl -fsS https://claims.example.com/health
curl -fsS https://claims.example.com/api/v1/demo/status
curl -fsS https://claims.example.com/customer/claims -o /dev/null
docker stats --no-stream claimpilot
```

Open the site and launch the interactive demo. Follow a sample claim through intake, photo analysis, explicit claim confirmation, policy review, contractor recommendation and human approval before simulated dispatch. Launch from a second tab or browser and verify the new workspace is empty. The first document index downloads the CPU embedding model. The cache is reused on later launches.

## Configuration and limits

| Runtime setting | Default or example |
| --- | --- |
| `CLAIMPILOT_HOSTED_DEMO` | Off by default. The Docker example enables it explicitly. |
| `CLAIMPILOT_DEMO_ORIGIN` | Public HTTPS origin, required in hosted mode. |
| `CLAIMPILOT_LLM_PROVIDER`, `GROQ_API_KEY` | Hosted mode requires `groq` and a server-side key. |
| `GROQ_MODEL`, `GROQ_VISION_MODEL` | `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` |
| `CLAIMPILOT_LLM_TIMEOUT_SECONDS`, `CLAIMPILOT_LLM_MAX_RETRIES` | 12 seconds and 1 retry. Retries count against the model-call budget. |
| `CLAIMPILOT_DEMO_TTL_SECONDS` | 3600 seconds from creation. |
| `CLAIMPILOT_DEMO_ACTIVE_WORKSPACES`, `CLAIMPILOT_DEMO_WORKSPACES_PER_HOUR`, `CLAIMPILOT_DEMO_WORKSPACES_PER_IP_HOUR` | 12 active, 12 new per rolling hour across the app, 3 new per rolling hour per client IP. |
| `CLAIMPILOT_DEMO_REQUESTS_PER_MINUTE`, `CLAIMPILOT_DEMO_MUTATIONS_PER_WORKSPACE` | 120 requests per rolling minute and 100 mutations over the workspace lifetime, shared by both roles. Up to two writes run concurrently across the app. |
| `CLAIMPILOT_DEMO_UPLOADS_PER_WORKSPACE`, `CLAIMPILOT_KB_MAX_BYTES`, `CLAIMPILOT_PHOTO_MAX_BYTES` | 16 upload attempts per workspace, including sample files and retries. Each document is limited to 5 MiB and each photo to 8 MiB. |
| `CLAIMPILOT_DEMO_MODEL_CALLS_PER_WORKSPACE`, `CLAIMPILOT_DEMO_MODEL_CALLS_PER_HOUR` | 45 provider attempts per workspace and 300 across the app per rolling hour. |
| `FORWARDED_ALLOW_IPS` | Trusted proxy IP or CIDR, used by Uvicorn. |

The browser receives short-lived claimant and operator bearer tokens for its workspace. The Groq key remains on the server. Use synthetic information only. Groq processes AI prompts and photos. Session records and rolling counters live in the worker's memory and reset when it restarts. Expired sessions stop authorizing immediately. The worker checks for expired data every 30 seconds and purges it after admitted requests finish. Startup purges hosted workspace data from previous runs.

## Persistent storage

Use a dedicated volume mounted at `/runtime` and keep one container on that volume. A bind mount must be writable by UID/GID 10001.

| Runtime path | Contents |
| --- | --- |
| `/runtime/intake.sqlite3` | Claims, normalized photo bytes, parsed documents and chunks, and dispatch history. Uploaded source files are processed during the request. |
| `/runtime/intake.checkpoints.sqlite3` and `/runtime/intake.workflow-lock.sqlite3` | LangGraph state and its workflow lock. SQLite sidecar files stay on the same volume. |
| `/runtime/chroma` | Vector index. SQLite holds the authoritative document records. |
| `/runtime/embeddings` | Downloaded and extracted CPU embedding model. |

The image sets `CLAIMPILOT_INTAKE_DB`, `CLAIMPILOT_KB_CHROMA` and `CLAIMPILOT_KB_MODEL_CACHE` to these volume paths. Restart with the same mount to reuse the cache. Demo cleanup deletes application records and indexed entries. Any external backups follow the host's own retention.

## Local image smoke test

Run the image with default settings to check routing and the health probe over loopback.

```sh
docker run --rm -d --name claimpilot-smoke -p 127.0.0.1:8000:8000 claimpilot-ai
curl -fsS --retry 15 --retry-connrefused http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/customer/claims -o /dev/null
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/customer/claims
docker exec claimpilot-smoke id
docker stats --no-stream claimpilot-smoke
docker stop claimpilot-smoke
```

The protected API call returns 401 and the container user is 10001. Measure memory again after indexing and completing an interactive session on your public host.
