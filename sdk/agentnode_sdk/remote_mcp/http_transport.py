"""The thin, stateless Streamable-HTTP ASGI transport in front of the frozen adapter.

A hand-rolled ASGI app (uvicorn-served at deploy, driven in-process for tests) so every
security check is explicit and self-contained rather than relying on framework defaults. It
enforces, in order: exact Host allowlist, Origin / DNS-rebinding, HTTP method, content-type /
Accept, body-size, then bearer authentication through the ONE shared verifier, then protocol
version negotiation, rate / concurrency / runtime limits; and ONLY for ``tools/list`` /
``tools/call`` it delegates to the UNCHANGED :class:`UsabilityMCPAdapter.handle`, so the
``prepare -> approve -> submit`` contract stays byte-frozen. A transport token never
substitutes the operator-signed single-use human approval: an authenticated ``tools/call``
with no deposited approval flows the adapter's ``approval_required`` straight back. There is
no ``approve`` route and no local execution fallback.

Primary protocol MCP 2026-07-28 (stateless). A deliberately-negotiated 2025-11-25 dual-era
path is entered only via an explicit ``initialize``; the era is never inferred from a missing
header; legacy HTTP+SSE / 2024-11-05 is never served.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

from agentnode_sdk.capability.usability_adapter import UsabilityMCPAdapter
from agentnode_sdk.remote_mcp.limits import ConcurrencyGate, Limits, RateLimiter
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import Principal, TokenStore, TokenVerifyError

def _h(value: str) -> str:
    """Minimal HTML-attribute/text escaping for the consent page (no templating engine)."""
    import html
    return html.escape(str(value), quote=True)


SUPPORTED_VERSIONS = ("2026-07-28", "2025-11-25")
PRIMARY_VERSION = "2026-07-28"
REQUIRED_SCOPE = "mcp:use"
#: a request that merely authenticates still cannot run a tool without a human approval
PrincipalResolver = Callable[[dict, dict], Optional[str]]  # (scope, headers) -> principal_id


class RemoteMCPApp:
    def __init__(self, *, adapter: UsabilityMCPAdapter, token_store: TokenStore,
                 authz: AuthorizationServer, resource: ResourceConfig,
                 allowed_hosts: tuple[str, ...], allowed_origins: tuple[str, ...] = (),
                 principal_resolver: Optional[PrincipalResolver] = None,
                 login_provider: Optional[Any] = None,
                 console_url: str = "",
                 limits: Optional[Limits] = None, allow_x_access_token: bool = False,
                 clock: Callable[[], float] = None):
        import time
        # The stand pre-shared-secret resolver and the production browser-login provider are two
        # ways of establishing the authorizing account, and never both: a build that offered both
        # would leave the disabled test shim reachable in production, which is exactly what the
        # production path must make structurally impossible.
        if principal_resolver is not None and login_provider is not None:
            raise ValueError("principal_resolver and login_provider are mutually exclusive")
        self.adapter = adapter
        self.tokens = token_store
        self.authz = authz
        self.resource = resource
        self.allowed_hosts = tuple(h.lower() for h in allowed_hosts)
        self.allowed_origins = tuple(allowed_origins)
        self.principal_resolver = principal_resolver
        self.login_provider = login_provider
        self.console_url = console_url
        self.limits = limits or Limits()
        self.allow_x_access_token = allow_x_access_token
        clock = clock or time.time
        self._rate = RateLimiter(self.limits.calls_per_minute_per_principal, clock)
        self._gate = ConcurrencyGate(self.limits.max_concurrent_calls, self.limits.max_queued_calls)

    # ---- ASGI entrypoint ------------------------------------------------- #
    async def __call__(self, scope: dict, receive: Callable[[], Awaitable[dict]],
                       send: Callable[[dict], Awaitable[None]]) -> None:
        if scope["type"] != "http":
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        method = scope["method"]
        path = scope["path"]

        # Host allowlist applies to every route (defense in depth).
        host = (headers.get("host", "")).split(":")[0].lower()
        if host not in self.allowed_hosts:
            return await self._send(send, 421, {"error": "misdirected_request"})

        if path == self.resource.prm_path and method == "GET":
            return await self._raw(send, 200, self.resource.document_bytes())
        if path == "/.well-known/oauth-authorization-server" and method == "GET":
            return await self._triple(send, self.authz.metadata())
        if path == "/authorize" and method == "GET":
            return await self._authorize(scope, headers, send)
        if path == "/authorize" and method == "POST" and self.login_provider is not None:
            return await self._authorize_post(scope, headers, receive, send)
        if path == "/token" and method == "POST":
            return await self._form_endpoint(receive, send, self.authz.token)
        if path == "/register" and method == "POST":
            return await self._json_endpoint(receive, send, self.authz.register)
        if path == "/mcp":
            if method in ("GET", "DELETE"):
                return await self._send(send, 405, {"error": "method_not_allowed"},
                                        extra=[("allow", "POST")])
            if method != "POST":
                return await self._send(send, 405, {"error": "method_not_allowed"})
            return await self._mcp(scope, headers, receive, send)
        return await self._send(send, 404, {"error": "not_found"})

    # ---- /mcp ------------------------------------------------------------ #
    async def _mcp(self, scope, headers, receive, send) -> None:
        # Origin / DNS-rebinding: if an Origin is presented it must be allow-listed.
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            return await self._send(send, 403, {"error": "origin_forbidden"})
        # content-type + Accept
        ctype = headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return await self._send(send, 415, {"error": "unsupported_media_type"})
        accept = headers.get("accept", "")
        if accept and "application/json" not in accept and "*/*" not in accept:
            return await self._send(send, 406, {"error": "not_acceptable"})
        # body with size cap
        body, too_big = await self._read_body(receive, self.limits.max_body_bytes)
        if too_big:
            return await self._send(send, 413, {"error": "payload_too_large"})

        # authentication through the ONE verifier
        try:
            principal = self._authenticate(headers)
        except TokenVerifyError as exc:
            status = exc.http_status
            challenge = self.resource.challenge(
                error="invalid_token" if status == 401 else "insufficient_scope",
                error_description=exc.reason)
            return await self._send(send, status, {"error": exc.reason},
                                    extra=[("www-authenticate", challenge)])

        try:
            msg = json.loads(body)
        except Exception:
            return await self._rpc_error(send, None, -32700, "parse error")
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return await self._rpc_error(send, None, -32600, "invalid request")
        mid = msg.get("id")
        rpc_method = msg.get("method")

        # protocol version negotiation (never infer era from a missing header)
        version, verr = self._negotiate_version(headers, msg)
        if verr is not None:
            return await self._triple(send, verr)

        if rpc_method == "initialize":
            return await self._rpc_result(send, mid, {
                "protocolVersion": version,
                "serverInfo": {"name": "agentnode-remote-mcp", "version": "1"},
                "capabilities": {"tools": {}}})
        if rpc_method == "notifications/initialized":
            return await self._raw(send, 202, b"")

        # SEP-2243 header/body agreement for modern requests
        if version == PRIMARY_VERSION:
            hm = headers.get("mcp-method")
            if hm is not None and hm != rpc_method:
                return await self._rpc_error(send, mid, -32600, "Mcp-Method header/body mismatch")
            if rpc_method == "tools/call":
                hn = headers.get("mcp-name")
                bn = (msg.get("params") or {}).get("name")
                if hn is not None and hn != bn:
                    return await self._rpc_error(send, mid, -32600, "Mcp-Name header/body mismatch")

        if rpc_method == "tools/list":
            return await self._rpc_passthrough(send, msg)
        if rpc_method == "tools/call":
            return await self._tools_call(send, msg, principal)
        return await self._rpc_error(send, mid, -32601, "method not found: %s" % rpc_method)

    def _authenticate(self, headers: dict) -> Principal:
        auth = headers.get("authorization")
        xtok = headers.get("x-access-token")
        bearer = None
        if auth and auth.lower().startswith("bearer "):
            bearer = auth[7:].strip()
        if bearer is not None and xtok is not None:
            raise TokenVerifyError("multiple_credentials", 401)
        if bearer is None and xtok is not None:
            if not self.allow_x_access_token:
                raise TokenVerifyError("x_access_token_not_enabled", 401)
            bearer = xtok.strip()
        if not bearer:
            raise TokenVerifyError("no_credentials", 401)
        return self.tokens.verify(bearer, expected_audience=self.resource.resource,
                                  required_scopes=(REQUIRED_SCOPE,))

    def _negotiate_version(self, headers: dict, msg: dict):
        declared = headers.get("mcp-protocol-version")
        if declared is None:
            # modern per-request requires the header; initialize may carry it in params
            if msg.get("method") == "initialize":
                declared = (msg.get("params") or {}).get("protocolVersion")
            if declared is None:
                return None, (400, [("content-type", "application/json")],
                              json.dumps({"error": "protocol_version_required"}).encode())
        if declared not in SUPPORTED_VERSIONS:
            err = {"jsonrpc": "2.0", "id": msg.get("id"),
                   "error": {"code": -32022, "message": "unsupported protocol version",
                             "data": {"supported": list(SUPPORTED_VERSIONS)}}}
            return None, (200, [("content-type", "application/json")],
                          json.dumps(err).encode())
        return declared, None

    async def _tools_call(self, send, msg, principal: Principal) -> None:
        if not self._rate.allow(principal.principal_id):
            return await self._send(send, 429, {"error": "rate_limited"},
                                    extra=[("retry-after", "60")])
        if not self._gate.try_enter_queue():
            return await self._send(send, 503, {"error": "overloaded"})
        self._gate.promote_to_active()
        try:
            loop = asyncio.get_event_loop()
            fut = loop.run_in_executor(None, self.adapter.handle, msg)
            resp = await asyncio.wait_for(fut, timeout=self.limits.call_timeout_seconds)
        except asyncio.TimeoutError:
            return await self._rpc_error(send, msg.get("id"), -32001, "call timed out")
        finally:
            self._gate.release()
        return await self._raw(send, 200, json.dumps(resp).encode())

    async def _rpc_passthrough(self, send, msg) -> None:
        resp = self.adapter.handle(msg)
        return await self._raw(send, 200, json.dumps(resp).encode())

    # ---- AS endpoints ---------------------------------------------------- #
    async def _authorize(self, scope, headers, send) -> None:
        params = self._query(scope)
        if self.login_provider is not None:
            return await self._authorize_consent_page(scope, headers, params, send)
        # Stand path: a pre-shared-secret resolver, used ONLY by build_stand_app.
        principal_id = self.principal_resolver(scope, headers) if self.principal_resolver else None
        if not principal_id:
            return await self._send(send, 401, {"error": "login_required"})
        return await self._triple(send, self.authz.authorize(params, principal_id=principal_id))

    async def _authorize_consent_page(self, scope, headers, params, send) -> None:
        """GET /authorize in production: render a consent page for the signed-in account.

        The account comes from the gateway session cookie via the login provider; the page
        POSTs the same OAuth parameters back with a CSRF confirmation to actually issue a code.
        No code is ever issued on this GET.
        """
        subject = self.login_provider.subject(scope, headers)
        if subject is None:
            link = (" <a href=\"%s\">Sign in</a>" % _h(self.console_url)) if self.console_url else ""
            return await self._html(send, 401,
                                    "<h1>Sign in required</h1><p>Authenticate to AgentNode in "
                                    "the console, then retry.%s</p>" % link)
        if not subject.permitted:
            return await self._html(send, 403,
                                    "<h1>Not permitted</h1><p>This device may not run work "
                                    "(no RUN capability), so it cannot be granted mcp:use.</p>")
        return await self._html(send, 200, self._consent_html(params, subject))

    async def _authorize_post(self, scope, headers, receive, send) -> None:
        body, too_big = await self._read_body(receive, self.limits.max_body_bytes)
        if too_big:
            return await self._send(send, 413, {"error": "payload_too_large"})
        form = self._parse_form(body)
        subject = self.login_provider.confirmed_subject(scope, headers, form)
        if subject is None:
            return await self._html(send, 403, "<h1>Could not confirm</h1>"
                                    "<p>Your session or confirmation was not valid.</p>")
        if form.get("decision") != "allow" or not subject.permitted:
            # Deny WITHOUT redirecting: an un-validated redirect_uri must never be a redirect
            # target, and declining consent is not an error to hand back to the client.
            return await self._html(send, 200, "<h1>Request declined</h1>"
                                    "<p>No authorization was granted.</p>")
        return await self._triple(send, self.authz.authorize(
            form, principal_id=subject.principal_id, granted_scopes=subject.scopes))

    def _consent_html(self, params: dict, subject) -> str:
        fields = {k: params.get(k, "") for k in (
            "client_id", "redirect_uri", "response_type", "code_challenge",
            "code_challenge_method", "resource", "state", "scope")}
        hidden = "".join('<input type="hidden" name="%s" value="%s">' % (k, _h(v))
                         for k, v in fields.items())
        return (
            "<!doctype html><meta charset=utf-8><title>Authorize</title>"
            "<h1>Authorize access</h1>"
            "<p>Client <code>%s</code> is requesting <code>%s</code> on "
            "<code>%s</code> for account <code>%s</code>.</p>"
            "<form method=\"POST\" action=\"/authorize\">%s"
            "<input type=\"hidden\" name=\"confirm\" value=\"%s\">"
            "<button name=\"decision\" value=\"allow\">Allow</button> "
            "<button name=\"decision\" value=\"deny\">Deny</button></form>" % (
                _h(fields["client_id"]), _h(" ".join(subject.scopes) or "(none)"),
                _h(fields["resource"]), _h(subject.principal_id), hidden,
                _h(subject.consent_token)))

    async def _form_endpoint(self, receive, send, fn) -> None:
        body, too_big = await self._read_body(receive, self.limits.max_body_bytes)
        if too_big:
            return await self._send(send, 413, {"error": "payload_too_large"})
        params = self._parse_form(body)
        return await self._triple(send, fn(params))

    async def _json_endpoint(self, receive, send, fn) -> None:
        body, too_big = await self._read_body(receive, self.limits.max_body_bytes)
        if too_big:
            return await self._send(send, 413, {"error": "payload_too_large"})
        try:
            obj = json.loads(body or b"{}")
        except Exception:
            return await self._send(send, 400, {"error": "invalid_json"})
        return await self._triple(send, fn(obj))

    # ---- low-level ASGI helpers ----------------------------------------- #
    async def _read_body(self, receive, cap: int):
        chunks, total = [], 0
        while True:
            event = await receive()
            chunk = event.get("body", b"")
            total += len(chunk)
            if total > cap:
                return b"", True
            chunks.append(chunk)
            if not event.get("more_body"):
                break
        return b"".join(chunks), False

    def _query(self, scope) -> dict:
        from urllib.parse import parse_qsl
        return dict(parse_qsl(scope.get("query_string", b"").decode("latin1")))

    def _parse_form(self, body: bytes) -> dict:
        from urllib.parse import parse_qsl
        return dict(parse_qsl(body.decode("utf-8")))

    async def _send(self, send, status: int, obj: Any, extra: Optional[list] = None) -> None:
        return await self._raw(send, status, json.dumps(obj).encode(), extra)

    async def _html(self, send, status: int, body_html: str) -> None:
        return await self._raw(send, status, body_html.encode("utf-8"),
                               [("content-type", "text/html; charset=utf-8")])

    async def _triple(self, send, triple) -> None:
        status, headers, body = triple
        await self._raw(send, status, body, headers)

    async def _raw(self, send, status: int, body: bytes, extra: Optional[list] = None) -> None:
        hdrs = [(b"content-type", b"application/json")]
        if extra:
            hdrs = []
            have_ct = False
            for k, v in extra:
                if k.lower() == "content-type":
                    have_ct = True
                hdrs.append((k.encode("latin1"), v.encode("latin1")))
            if not have_ct:
                hdrs.append((b"content-type", b"application/json"))
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})

    async def _rpc_result(self, send, mid, result) -> None:
        await self._raw(send, 200, json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}).encode())

    async def _rpc_error(self, send, mid, code, message) -> None:
        await self._raw(send, 200, json.dumps(
            {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}).encode())
