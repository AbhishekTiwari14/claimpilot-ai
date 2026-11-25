import asyncio
from collections import deque
from dataclasses import dataclass, field
import logging
import re
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from claimpilot.api.intake import RequestKey
from claimpilot.config import ClaimantCredential, OperatorCredential
from claimpilot.services.llm import LLMError, model_attempt_guard
from claimpilot.storage.demo import WORKSPACE_PREFIX, demo_workspaces, purge_workspace
from claimpilot.storage.intake import IntakeError

logger = logging.getLogger(__name__)
UPLOAD = re.compile(r"/api/v1/(?:knowledge/documents|claims/[^/]+/photos)/?")


def prune(events, cutoff):
    while events and events[0] <= cutoff:
        events.popleft()


@dataclass
class Workspace:
    tenant: str
    tokens: dict[str, str]
    expiry: float
    requests: deque = field(default_factory=deque)
    in_flight: int = 0
    mutations: int = 0
    uploads: int = 0
    model_calls: int = 0


class HostedDemo:
    def __init__(self, state, *, clock=monotonic):
        self.state = state
        self.settings = state.settings
        self.origin = urlsplit(self.settings.claimpilot_demo_origin)
        self.clock = clock
        self.lock = Lock()
        self.workspaces = {}
        self.tokens = {}
        self.launches = deque()
        self.model_calls = deque()
        self.active_writes = 0

    def require_origin(self, request):
        if (request.url.scheme != "https" or request.headers.get("host", "").lower() != self.origin.netloc
            or request.headers.get("origin") not in (None, self.settings.claimpilot_demo_origin)
            or request.headers.get("sec-fetch-site") not in (None, "same-origin")):
            raise IntakeError(403, "demo_same_origin", "Open the demo from its public HTTPS address.")

    def register(self, app):
        @app.get("/api/v1/demo/status")
        def status():
            return {"enabled": True, "hosted": True, "provider": "groq"}

        @app.post("/api/v1/demo/session")
        def session(request: Request, key: RequestKey):
            if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", key):
                raise IntakeError(422, "invalid_demo_key", "Use a random workspace launch key.")
            if request.client is None:
                raise IntakeError(403, "demo_peer_required", "The demo could not identify your connection.")
            workspace = self.launch(request.client.host, key)
            return JSONResponse(workspace.tokens, headers={"Cache-Control": "no-store"})

    def launch(self, peer, key):
        with self.lock:
            now = self.clock()
            existing = self.workspaces.get((peer, key))
            if existing:
                if existing.expiry <= now:
                    raise IntakeError(401, "demo_session_required", "This demo workspace expired. Start a new demo.")
                return existing
            while self.launches and self.launches[0][0] <= now - 3600:
                self.launches.popleft()
            if (len(self.launches) >= self.settings.claimpilot_demo_workspaces_per_hour
                or sum(ip == peer for _, ip in self.launches) >= self.settings.claimpilot_demo_workspaces_per_ip_hour
                or len(self.workspaces) >= self.settings.claimpilot_demo_active_workspaces):
                raise IntakeError(429, "demo_capacity", "The demo workspace limit has been reached. Try again later.")
            tenant = WORKSPACE_PREFIX + uuid4().hex
            tokens = {role: token_urlsafe(32) for role in ("claimant", "operator")}
            self.state.contractor_store.seed({tenant})
            workspace = Workspace(tenant, tokens, now + self.settings.claimpilot_demo_ttl_seconds)
            self.settings.claimpilot_claimants.append(
                ClaimantCredential(tenant_id=tenant, claimant_id="visitor", token=tokens["claimant"]),
            )
            self.settings.claimpilot_kb_operators.append(
                OperatorCredential(tenant_id=tenant, operator_id="reviewer", token=tokens["operator"]),
            )
            self.workspaces[peer, key] = workspace
            self.tokens.update((token, workspace) for token in tokens.values())
            self.launches.append((now, peer))
            return workspace

    def admit(self, request, write, upload):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        with self.lock:
            now = self.clock()
            workspace = self.tokens.get(token) if scheme.lower() == "bearer" else None
            if workspace is None or workspace.expiry <= now:
                raise IntakeError(401, "demo_session_required", "Start or restart the interactive demo to use this workspace.")
            prune(workspace.requests, now - 60)
            if len(workspace.requests) >= self.settings.claimpilot_demo_requests_per_minute:
                raise IntakeError(429, "demo_request_limit", "Too many demo requests. Try again in a minute.")
            if workspace.in_flight >= 8 or (write and self.active_writes >= 2):
                raise IntakeError(429, "demo_busy", "The demo is busy. Try again in a few seconds.")
            if write and workspace.mutations >= self.settings.claimpilot_demo_mutations_per_workspace:
                raise IntakeError(429, "demo_workspace_limit", "This workspace has reached its action limit.")
            if upload and workspace.uploads >= self.settings.claimpilot_demo_uploads_per_workspace:
                raise IntakeError(429, "demo_upload_limit", "This workspace has reached its upload limit.")
            workspace.requests.append(now)
            workspace.in_flight += 1
            if write:
                workspace.mutations += 1
                self.active_writes += 1
            if upload:
                workspace.uploads += 1
            return workspace

    def spend_model_call(self, workspace):
        with self.lock:
            now = self.clock()
            prune(self.model_calls, now - 3600)
            if (workspace.expiry <= now or workspace.model_calls >= self.settings.claimpilot_demo_model_calls_per_workspace
                or len(self.model_calls) >= self.settings.claimpilot_demo_model_calls_per_hour):
                raise LLMError("demo_model_limit")
            self.model_calls.append(now)
            workspace.model_calls += 1

    async def handle(self, app, scope, receive, send):
        request = Request(scope, receive)
        try:
            self.require_origin(request)
            if request.url.path.rstrip("/") in ("/api/v1/demo/status", "/api/v1/demo/session"):
                await app(scope, receive, send)
                return
            write = request.method not in ("GET", "HEAD", "OPTIONS")
            upload = request.method == "POST" and UPLOAD.fullmatch(request.url.path) is not None
            workspace = self.admit(request, write, upload)
            guard = model_attempt_guard.set(lambda: self.spend_model_call(workspace))
            try:
                if write and not upload:
                    receive = await bounded_body(request)
                await app(scope, receive, send)
            finally:
                model_attempt_guard.reset(guard)
                with self.lock:
                    workspace.in_flight -= 1
                    if write:
                        self.active_writes -= 1
        except IntakeError as error:
            await JSONResponse(
                {"code": error.code, "message": str(error), "request_id": str(uuid4())},
                status_code=error.status, headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
        except ClientDisconnect:
            return

    def purge_previous_sessions(self):
        for tenant in demo_workspaces(self.state.intake_store):
            purge_workspace(self.state.intake_store, self.state.knowledge, tenant)

    def sweep(self):
        with self.lock:
            expired = [
                (key, workspace) for key, workspace in self.workspaces.items()
                if workspace.expiry <= self.clock() and workspace.in_flight == 0
            ]
        for key, workspace in expired:
            try:
                purge_workspace(self.state.intake_store, self.state.knowledge, workspace.tenant)
            except Exception:
                logger.exception("hosted_demo_cleanup_failed")
                continue
            with self.lock:
                for token in workspace.tokens.values():
                    del self.tokens[token]
                del self.workspaces[key]
                self.settings.claimpilot_claimants = [
                    actor for actor in self.settings.claimpilot_claimants if actor.tenant_id != workspace.tenant
                ]
                self.settings.claimpilot_kb_operators = [
                    actor for actor in self.settings.claimpilot_kb_operators if actor.tenant_id != workspace.tenant
                ]

    async def cleanup_loop(self):
        while True:
            await asyncio.sleep(30)
            await run_in_threadpool(self.sweep)


async def bounded_body(request):
    body = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 32 * 1024:
                    raise IntakeError(413, "demo_body_limit", "The request exceeds the demo size limit.")
                body.extend(chunk)
    except TimeoutError:
        raise IntakeError(408, "demo_body_timeout", "The demo request took too long to upload.") from None
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return await request.receive()
        delivered = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    return receive


class HostedDemoMiddleware:
    def __init__(self, app, demo):
        self.app = app
        self.demo = demo

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/v1/"):
            await self.demo.handle(self.app, scope, receive, send)
        else:
            await self.app(scope, receive, send)
