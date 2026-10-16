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
import re
import secrets
from typing import Any, Awaitable, Callable, Optional

from agentnode_sdk.capability.usability_adapter import UsabilityMCPAdapter
from agentnode_sdk.remote_mcp.limits import ConcurrencyGate, Limits, RateLimiter
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer, parse_unique_params
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import Principal, TokenStore, TokenVerifyError

def _h(value: str) -> str:
    """Minimal HTML-attribute/text escaping for the consent page (no templating engine)."""
    import html
    return html.escape(str(value), quote=True)


#: the modern, stateless era: server/discover, per-request `_meta` envelope, no sessions.
MODERN_VERSIONS = ("2026-07-28",)
#: the legacy dual-era path, deliberately negotiated via initialize. Only 2025-11-25 is served.
LEGACY_VERSIONS = ("2025-11-25",)
SUPPORTED_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS
PRIMARY_VERSION = "2026-07-28"
REQUIRED_SCOPE = "mcp:use"
#: MCP 2026-07-28 per-request `_meta` envelope keys (from mcp_types; reverse-DNS).
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
#: which param carries the name a modern Mcp-Name header must equal, per method.
NAME_BEARING_METHODS = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}


def _decode_mcp_name(value: str) -> str:
    """Reverse the SDK's header encoding: a non-ASCII name is wrapped `=?b?<base64>?=`."""
    import base64
    if value.startswith("=?b?") and value.endswith("?="):
        try:
            return base64.b64decode(value[4:-2]).decode("utf-8")
        except Exception:  # noqa: BLE001
            return value
    return value
#: cap on the /authorize query so an oversized request cannot be stashed or reflected.
MAX_AUTHORIZE_QUERY = 4096
#: how long a login continuation handle is valid before it must be re-initiated.
CONTINUATION_TTL = 300.0
#: the only shape a continuation handle may take (so it is safe to embed and to key on).
_HANDLE_RE = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


def _js_string(value: str) -> str:
    """A JSON string literal safe to place inside an HTML <script>: `json.dumps` alone is not
    (it leaves `</script>`, `<`, `>`, `&` and the U+2028/U+2029 line separators intact)."""
    s = json.dumps(value)
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
#: a request that merely authenticates still cannot run a tool without a human approval
PrincipalResolver = Callable[[dict, dict], Optional[str]]  # (scope, headers) -> principal_id


class RemoteMCPApp:
    def __init__(self, *, adapter: UsabilityMCPAdapter, token_store: TokenStore,
                 authz: AuthorizationServer, resource: ResourceConfig,
                 allowed_hosts: tuple[str, ...], allowed_origins: tuple[str, ...] = (),
                 principal_resolver: Optional[PrincipalResolver] = None,
                 login_provider: Optional[Any] = None,
                 live_authority: Optional[Any] = None,
                 console_url: str = "", max_continuations: int = 1024,
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
        self.live_authority = live_authority
        self.console_url = console_url
        self.limits = limits or Limits()
        self.allow_x_access_token = allow_x_access_token
        clock = clock or time.time
        self._now = clock
        self._rate = RateLimiter(self.limits.calls_per_minute_per_principal, clock)
        self._gate = ConcurrencyGate(self.limits.max_concurrent_calls, self.limits.max_queued_calls)
        # short-lived, single-use, crypto-random continuation handles for the login bridge:
        # handle -> (normalized validated request, expires_at). /login carries only the handle.
        # Hard-capped and swept on insert so unauthenticated /authorize cannot grow it unbounded.
        self._continuations: dict[str, tuple[dict, float]] = {}
        self.max_continuations = int(max_continuations)

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
        if path == "/login" and method == "GET" and self.login_provider is not None:
            return await self._login_page(scope, send)
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
        # MCP Streamable HTTP requires the client to Accept BOTH content types the server may
        # answer with; a request that does not is refused (not served a guessed type).
        accept = headers.get("accept", "")
        if "application/json" not in accept or "text/event-stream" not in accept:
            return await self._send(send, 406, {"error": "not_acceptable",
                                    "error_description": "Accept must list application/json and text/event-stream"})
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
        header_pv = headers.get("mcp-protocol-version")

        # ERA ROUTING by the protocol-version header (the era is NEVER inferred from a missing
        # one): a modern version, or a server/discover, takes the stateless modern path; a legacy
        # (handshake) version takes the initialize/session path. The two never mix.
        if header_pv in MODERN_VERSIONS or rpc_method == "server/discover":
            return await self._mcp_modern(send, headers, msg, mid, rpc_method, header_pv, principal)
        if header_pv is None:
            return await self._send(send, 400, {"error": "protocol_version_required"})
        if header_pv in LEGACY_VERSIONS:
            return await self._mcp_legacy(send, msg, mid, rpc_method, principal)
        # a stated but unsupported version
        return await self._rpc_error_data(send, mid, -32022, "unsupported protocol version",
                                          {"supported": list(SUPPORTED_VERSIONS), "requested": header_pv})

    # ---- modern era (2026-07-28): stateless, server/discover, _meta envelope ------------- #
    async def _mcp_modern(self, send, headers, msg, mid, method, header_pv, principal) -> None:
        params = msg.get("params") or {}
        meta = params.get("_meta") if isinstance(params, dict) else None
        # rung 1: the modern _meta envelope must carry protocolVersion + clientCapabilities
        _meta_ok = isinstance(meta, dict) and meta.get(PROTOCOL_VERSION_META_KEY) is not None and CLIENT_CAPABILITIES_META_KEY in meta
        if not _meta_ok:
            return await self._rpc_error(send, mid, -32602,
                                         "modern request requires _meta.protocolVersion and clientCapabilities")
        body_pv = meta.get(PROTOCOL_VERSION_META_KEY)
        # rung 2: header must be present and agree with _meta; Mcp-Method == method; Mcp-Name ==
        # the named param for a name-bearing method. Any disagreement is HEADER_MISMATCH.
        if header_pv is None or header_pv != body_pv:
            return await self._rpc_error(send, mid, -32600, "protocol version header/_meta disagree")
        hm = headers.get("mcp-method")
        if hm is None or hm != method:
            return await self._rpc_error(send, mid, -32600, "Mcp-Method header required and must equal the method")
        name_key = NAME_BEARING_METHODS.get(method)
        if name_key is not None and isinstance(params.get(name_key), str):
            hn = headers.get("mcp-name")
            if hn is None or _decode_mcp_name(hn) != params[name_key]:
                return await self._rpc_error(send, mid, -32600, "Mcp-Name header required and must equal the named param")
        # rung 3: the version must be a supported MODERN version
        if body_pv not in MODERN_VERSIONS:
            return await self._rpc_error_data(send, mid, -32022, "unsupported protocol version",
                                              {"supported": list(MODERN_VERSIONS), "requested": body_pv})
        # a modern `initialize` is NOT a modern handshake; the modern era uses server/discover.
        if method in ("initialize", "notifications/initialized"):
            return await self._rpc_error_data(send, mid, -32022,
                                              "initialize is not valid in the modern era; use server/discover",
                                              {"supported": list(MODERN_VERSIONS), "requested": body_pv})
        self._log_era("modern", body_pv, method)
        if method == "server/discover":
            return await self._rpc_result(send, mid, {
                "supportedVersions": list(MODERN_VERSIONS),
                "capabilities": {"tools": {"listChanged": False}},
                "resultType": "complete",
                "_meta": {SERVER_INFO_META_KEY: {"name": "agentnode-remote-mcp", "version": "1"}}})
        if method == "tools/list":
            return await self._rpc_passthrough(send, msg, modern=True)
        if method == "tools/call":
            return await self._tools_call(send, msg, principal, modern=True)
        if mid is None:
            return await self._raw(send, 202, b"")
        return await self._rpc_error(send, mid, -32601, "method not found: %s" % method)

    # ---- legacy era (2025-11-25): initialize handshake, stateless (no session id) -------- #
    async def _mcp_legacy(self, send, msg, mid, method, principal) -> None:
        params = msg.get("params") or {}
        meta = params.get("_meta") if isinstance(params, dict) else None
        # strict separation: a modern envelope must not ride a legacy request
        if isinstance(meta, dict) and PROTOCOL_VERSION_META_KEY in meta:
            return await self._rpc_error(send, mid, -32600, "modern _meta envelope on a legacy request")
        self._log_era("legacy", "2025-11-25", method)
        if method == "initialize":
            return await self._rpc_result(send, mid, {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "agentnode-remote-mcp", "version": "1"}})
        if method == "notifications/initialized":
            return await self._raw(send, 202, b"")
        if method == "tools/list":
            return await self._rpc_passthrough(send, msg)
        if method == "tools/call":
            return await self._tools_call(send, msg, principal)
        if mid is None:
            return await self._raw(send, 202, b"")
        return await self._rpc_error(send, mid, -32601, "method not found: %s" % method)

    def _log_era(self, era: str, version: str, method: str) -> None:
        import logging
        logging.getLogger("agentnode.remote_mcp").info(
            "mcp request era=%s negotiated_version=%s method=%s", era, version, method)

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
        principal = self.tokens.verify(bearer, expected_audience=self.resource.resource,
                                       required_scopes=(REQUIRED_SCOPE,))
        # Fail-closed live re-check against the real gateway on EVERY call: the token is
        # structurally valid, but account suspension, device withdrawal and RUN-withdrawal
        # live in the gateway, not the token store, so they are re-derived here each time.
        if self.live_authority is not None:
            reason = self.live_authority.authority_error(principal)
            if reason is not None:
                raise TokenVerifyError(reason, 403)
        return principal

    @staticmethod
    def _stamp_modern(resp: dict, modern: bool) -> dict:
        """Modern (2026-07-28) results MUST carry resultType; the frozen adapter does not know
        about it, so it is stamped here on the way out. Legacy results omit it (client bridges)."""
        if modern and isinstance(resp, dict) and isinstance(resp.get("result"), dict):
            resp["result"].setdefault("resultType", "complete")
        return resp

    async def _tools_call(self, send, msg, principal: Principal, modern: bool = False) -> None:
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
        return await self._raw(send, 200, json.dumps(self._stamp_modern(resp, modern)).encode())

    async def _rpc_passthrough(self, send, msg, modern: bool = False) -> None:
        resp = self.adapter.handle(msg)
        return await self._raw(send, 200, json.dumps(self._stamp_modern(resp, modern)).encode())

    # ---- continuation handles (login bridge) ----------------------------- #
    def _sweep_continuations(self) -> None:
        """Drop expired entries now (not only on later access), so the store tracks live pending
        logins rather than growing with every unauthenticated /authorize."""
        now = self._now()
        for h in [h for h, (_n, exp) in self._continuations.items() if now >= exp]:
            self._continuations.pop(h, None)

    def _mint_continuation(self, norm: dict) -> Optional[str]:
        """Return a new handle, or None when the store is full AFTER sweeping expired entries —
        the caller then refuses with a retry hint and never evicts a still-valid foreign login."""
        self._sweep_continuations()
        if len(self._continuations) >= self.max_continuations:
            return None
        handle = secrets.token_urlsafe(24)
        self._continuations[handle] = (norm, self._now() + CONTINUATION_TTL)
        return handle

    def _peek_continuation(self, handle: str) -> Optional[dict]:
        if not handle or not _HANDLE_RE.match(handle):
            return None
        rec = self._continuations.get(handle)
        if rec is None:
            return None
        norm, exp = rec
        if self._now() >= exp:
            self._continuations.pop(handle, None)
            return None
        return norm

    def _redeem_continuation(self, handle: str) -> Optional[dict]:
        """Single-use: the handle is removed on redemption, so it cannot be replayed."""
        norm = self._peek_continuation(handle)
        if norm is not None:
            self._continuations.pop(handle, None)  # single-use consume
        return norm

    # ---- AS endpoints ---------------------------------------------------- #
    async def _authorize(self, scope, headers, send) -> None:
        if len(scope.get("query_string", b"")) > MAX_AUTHORIZE_QUERY:
            return await self._send(send, 414, {"error": "request_too_long"})
        params, dup = parse_unique_params(scope.get("query_string", b"").decode("latin1"))
        if dup is not None:   # a repeated security parameter is rejected, never folded
            return await self._triple(send, dup)
        if self.login_provider is None:
            # Stand path: a pre-shared-secret resolver, used ONLY by build_stand_app.
            principal_id = self.principal_resolver(scope, headers) if self.principal_resolver else None
            if not principal_id:
                return await self._send(send, 401, {"error": "login_required"})
            return await self._triple(send, self.authz.authorize(params, principal_id=principal_id))
        return await self._authorize_production(scope, headers, params, send)

    async def _authorize_production(self, scope, headers, params, send) -> None:
        resume = params.get("resume", "")
        if resume:
            # Continuation of a request validated BEFORE the login bounce. The handle is the only
            # thing /login ever carried; the request itself was never a free URL.
            norm = self._peek_continuation(resume)
            if norm is None:
                return await self._secure_page(send, 400, "<h1>Link expired</h1><p>Please start "
                                               "the authorization again.</p>")
            subject = self.login_provider.subject(scope, headers)
            if subject is None:
                return await self._redirect(send, "/login?k=" + resume)   # go sign in
            self._redeem_continuation(resume)                              # single-use
            if not subject.permitted:
                return await self._secure_page(send, 403, "<h1>Not permitted</h1><p>This device "
                                               "may not run work (no RUN), so no mcp:use.</p>")
            return await self._consent(send, norm, subject)

        # Fresh request: FULLY validate before any login/pairing is ever offered.
        norm, err = self.authz.validate(params)
        if err is not None:
            return await self._triple(send, err)
        subject = self.login_provider.subject(scope, headers)
        if subject is not None:
            if not subject.permitted:
                return await self._secure_page(send, 403, "<h1>Not permitted</h1><p>This device "
                                               "may not run work (no RUN), so no mcp:use.</p>")
            return await self._consent(send, norm, subject)
        # Valid but not signed in: stash the normalized request under a single-use handle and
        # bounce once same-origin (which also makes a SameSite=Strict cookie be resent).
        handle = self._mint_continuation(norm)
        if handle is None:
            # store full after sweeping expired entries — refuse WITHOUT evicting a valid login,
            # with an actionable retry hint (never a silent displacement of someone else).
            return await self._secure_page(
                send, 503, "<h1>Too many sign-ins in progress</h1><p>Please retry in a few "
                "minutes.</p>", retry_after=120)
        return await self._redirect(send, "/authorize?resume=" + handle)

    async def _consent(self, send, norm: dict, subject) -> None:
        return await self._secure_page(send, 200, self._consent_html(norm, subject))

    async def _authorize_post(self, scope, headers, receive, send) -> None:
        body, too_big = await self._read_body(receive, self.limits.max_body_bytes)
        if too_big:
            return await self._send(send, 413, {"error": "payload_too_large"})
        form, dup = parse_unique_params(body.decode("utf-8", "replace"))
        if dup is not None:   # duplicate security parameters refused on the consent POST too
            return await self._triple(send, dup)
        subject = self.login_provider.confirmed_subject(scope, headers, form)
        if subject is None:
            return await self._secure_page(send, 403, "<h1>Could not confirm</h1>"
                                           "<p>Your session or confirmation was not valid.</p>")
        if form.get("decision") != "allow" or not subject.permitted:
            return await self._secure_page(send, 200, "<h1>Request declined</h1>"
                                           "<p>No authorization was granted.</p>")
        # authz.authorize re-validates everything (client, exact redirect_uri, PKCE, resource)
        # before issuing, so the consent form's embedded fields are never trusted on their own.
        return await self._triple(send, self.authz.authorize(
            form, principal_id=subject.principal_id, granted_scopes=subject.scopes,
            device_id=subject.device_id))

    def _consent_html(self, norm: dict, subject) -> str:
        # Every value goes into an HTML ATTRIBUTE via html.escape(quote=True); there is NO inline
        # script on this page (CSP script-src 'none'), so no value reaches a script context.
        # only fields actually present round-trip (an absent state is never fabricated into "")
        hidden = "".join('<input type="hidden" name="%s" value="%s">' % (k, _h(norm[k]))
                         for k in self.authz.REQUEST_FIELDS if k in norm)
        return (
            "<!doctype html><meta charset=\"utf-8\"><title>Authorize</title>"
            "<h1>Authorize access</h1>"
            "<p>Client <code>%s</code> is requesting <code>%s</code> on "
            "<code>%s</code> for account <code>%s</code>.</p>"
            "<form method=\"POST\" action=\"/authorize\">%s"
            "<input type=\"hidden\" name=\"confirm\" value=\"%s\">"
            "<button name=\"decision\" value=\"allow\">Allow</button> "
            "<button name=\"decision\" value=\"deny\">Deny</button></form>" % (
                _h(norm.get("client_id", "")), _h(" ".join(subject.scopes) or "(none)"),
                _h(norm.get("resource", "")), _h(subject.principal_id), hidden,
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

    async def _redirect(self, send, location: str) -> None:
        return await self._raw(send, 302, b"", [("location", location)])

    async def _secure_page(self, send, status: int, body_html: str,
                           nonce: Optional[str] = None, retry_after: Optional[int] = None) -> None:
        """Serve a login/consent page with restrictive headers (CSP incl. frame-ancestors/base-uri
        /form-action, no-referrer, nosniff, no-store, and X-Frame-Options for old agents)."""
        script_src = "'nonce-%s'" % nonce if nonce else "'none'"
        csp = ("default-src 'none'; style-src 'unsafe-inline'; connect-src 'self'; "
               "form-action 'self'; base-uri 'none'; frame-ancestors 'none'; script-src %s"
               % script_src)
        headers = [("content-type", "text/html; charset=utf-8"),
                   ("content-security-policy", csp), ("x-frame-options", "DENY"),
                   ("referrer-policy", "no-referrer"), ("x-content-type-options", "nosniff"),
                   ("cache-control", "no-store")]
        if retry_after is not None:
            headers.append(("retry-after", str(retry_after)))
        return await self._raw(send, status, body_html.encode("utf-8"), headers)

    async def _login_page(self, scope, send) -> None:
        """A same-origin sign-in landing that carries ONLY a single-use continuation handle (never
        a free URL). It drives the REAL gateway redeem (POST /v1/session, same origin) and then
        resumes the pre-validated request via /authorize?resume=<handle>. Mints no session and
        injects no cookie. The handle's restricted charset + script-safe encoding keep the inline
        script XSS-free, and it runs only under a per-response CSP nonce."""
        params = self._query(scope)
        handle = params.get("k", "")
        if self._peek_continuation(handle) is None:   # unknown / expired / malformed handle
            return await self._secure_page(send, 400, "<h1>Link expired</h1><p>Please start the "
                                           "authorization again.</p>")
        nonce = secrets.token_urlsafe(16)
        html = (
            "<!doctype html><meta charset=\"utf-8\"><title>Sign in</title>"
            "<h1>Sign in to AgentNode</h1>"
            "<p>Enter the pairing code from your invitation to authorize this request.</p>"
            "<form id=\"f\"><input name=\"code\" placeholder=\"ABCD-EFGH-JKLM\" autocomplete=\"off\"> "
            "<input name=\"client_name\" value=\"browser\"> "
            "<button type=\"submit\">Sign in</button></form><p id=\"msg\"></p>"
            "<script nonce=\"%s\">const k=%s;"
            "document.getElementById('f').addEventListener('submit',async e=>{e.preventDefault();"
            "const fd=new FormData(e.target);"
            "const r=await fetch('/v1/session',{method:'POST',headers:{'content-type':'application/json'},"
            "body:JSON.stringify({code:fd.get('code'),client_name:fd.get('client_name')})});"
            "if(r.ok){window.location='/authorize?resume='+encodeURIComponent(k);}"
            "else{document.getElementById('msg').textContent='sign-in failed ('+r.status+')';}});"
            "</script>" % (nonce, _js_string(handle)))
        return await self._secure_page(send, 200, html, nonce=nonce)

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

    @staticmethod
    def _http_for(code: int) -> int:
        # MCP's ERROR_CODE_HTTP_STATUS: classifier/parse/version errors -> 400, method-not-found
        # -> 404, everything else (internal, timeout) -> 200 with the JSON-RPC error body.
        if code == -32601:
            return 404
        if code in (-32700, -32600, -32602, -32022):
            return 400
        return 200

    async def _rpc_error(self, send, mid, code, message) -> None:
        await self._raw(send, self._http_for(code), json.dumps(
            {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}).encode())

    async def _rpc_error_data(self, send, mid, code, message, data) -> None:
        await self._raw(send, self._http_for(code), json.dumps(
            {"jsonrpc": "2.0", "id": mid,
             "error": {"code": code, "message": message, "data": data}}).encode())
