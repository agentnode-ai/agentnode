"""OAuth 2.1 Authorization Server for the remote MCP service (R3, R4).

Framework-agnostic: every endpoint is a method returning ``(status, headers, body_bytes)`` so
the ASGI layer and the counter-checks drive it identically. It issues through the SHARED
:class:`~agentnode_sdk.remote_mcp.tokens.TokenStore` (``issuance_kind="oauth"``), so an OAuth
token and a human-minted personal token are the same record and pass the same verifier.

Implements, per MCP 2026-07-28 authorization:
  * RFC 8414 AS metadata at ``/.well-known/oauth-authorization-server`` advertising
    ``client_id_metadata_document_supported: true`` (CIMD primary), S256 PKCE, auth-code only.
  * CIMD client resolution: an HTTPS-URL ``client_id`` whose document is fetched SSRF-safely,
    whose ``client_id`` MUST equal its own URL, with exact ``redirect_uris`` matching.
  * Authorization-code grant with mandatory PKCE S256 and the RFC 8707 ``resource`` indicator
    bound into the issued token's audience.
  * DCR (RFC 7591) ONLY when ``enable_dcr=True`` (a bounded, measured per-client fallback);
    off by default and NOT advertised when off.

Secrets discipline: authorization codes, PKCE verifiers and tokens are never logged; errors
carry OAuth error codes only.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import secrets
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from agentnode_sdk.remote_mcp.tokens import TokenStore

JSON = [("content-type", "application/json")]

#: Authorization parameters that must appear AT MOST ONCE; a repeat is rejected rather than
#: silently folded (``resource`` is single by this product's rule, not just by RFC 8707).
SECURITY_PARAMS = ("client_id", "redirect_uri", "response_type", "code_challenge",
                   "code_challenge_method", "state", "scope", "resource")


def parse_unique_params(raw_query: str):
    """Parse an authorization query/form preserving every occurrence, and REJECT a duplicate of
    any security-relevant parameter instead of collapsing it (``dict(parse_qsl(...))`` keeps the
    last silently). Returns ``(params_dict, None)`` or ``(None, error_triple)``. Blank values are
    kept, so a present-but-empty ``state`` is distinguishable from an absent one."""
    seen: dict[str, str] = {}
    for k, v in parse_qsl(raw_query, keep_blank_values=True):
        if k in seen and k in SECURITY_PARAMS:
            return None, _json(400, {"error": "invalid_request",
                                     "error_description": "duplicate parameter %s" % k})
        seen[k] = v
    return seen, None


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_s256_challenge(verifier: str) -> str:
    return _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())


def _json(status: int, obj: Any, extra_headers: Optional[list] = None) -> tuple[int, list, bytes]:
    return status, JSON + (extra_headers or []), json.dumps(obj, sort_keys=True).encode("utf-8")


# ---- CIMD client metadata (SSRF-safe fetch) ----------------------------------------------- #

class CIMDError(Exception):
    pass


def _reject_non_public(ip_text: str) -> None:
    ip = ipaddress.ip_address(ip_text)
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        raise CIMDError("client_id resolves to a non-public address: %s" % ip)


def resolve_public_ips(host: str, port: int = 443, *,
                       getaddrinfo: Callable = socket.getaddrinfo) -> list[str]:
    """Resolve ``host`` EXACTLY ONCE and return its addresses, rejecting if ANY of them is a
    private / loopback / link-local / reserved / multicast / unspecified address.

    The fetcher then connects pinned to the returned address and never re-resolves, so a name
    that answers public here cannot rebind to an internal address at connect time. Rejecting
    the whole set when any member is non-public also defeats a mixed public+internal answer.
    """
    try:
        infos = getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise CIMDError("client_id host does not resolve") from exc
    ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        _reject_non_public(addr)
        ips.append(addr)
    if not ips:
        raise CIMDError("client_id host did not resolve to any address")
    return ips


def _pinned_https_get(ip: str, port: int, hostname: str, path: str, timeout: float,
                      max_bytes: int) -> tuple[int, str, bytes]:
    """HTTPS GET to the already-validated ``ip`` with SNI + certificate validation against
    ``hostname``. Pinning the socket to the pre-resolved IP is what closes the DNS-rebinding
    window; redirects are never followed (any non-200 is rejected by the caller)."""
    import http.client
    import ssl

    ctx = ssl.create_default_context()
    raw = socket.create_connection((ip, port), timeout=timeout)
    try:
        conn = http.client.HTTPSConnection(hostname, port, timeout=timeout, context=ctx)
        conn.sock = ctx.wrap_socket(raw, server_hostname=hostname)
        conn.request("GET", path, headers={"Accept": "application/json", "Host": hostname,
                                            "Connection": "close"})
        resp = conn.getresponse()
        data = resp.read(max_bytes + 1)
        status, ctype = resp.status, resp.getheader("content-type", "")
        conn.close()
        return status, ctype, data
    finally:
        try:
            raw.close()
        except OSError:
            pass


#: injectable so SSRF / rebinding counter-checks drive the fetch without a real network
Connector = Callable[[str, int, str, str, float, int], tuple[int, str, bytes]]


def default_cimd_fetcher(client_id_url: str, *, max_bytes: int = 64 * 1024,
                         timeout: float = 5.0,
                         getaddrinfo: Callable = socket.getaddrinfo,
                         connector: Optional[Connector] = None) -> dict:
    """Fetch and parse a Client-ID Metadata Document from an HTTPS URL, SSRF-safely.

    HTTPS only; no embedded credentials; the host is resolved exactly once and rejected if any
    resolved address is non-public; the connection is pinned to that address (no rebind); no
    redirects are followed (non-200 rejected); response size, time and content-type are bounded.
    The AS still validates the document contents (``client_id`` must equal its own URL); this
    only gets the bytes safely.
    """
    parts = urlsplit(client_id_url)
    if parts.scheme != "https" or not parts.hostname:
        raise CIMDError("client_id must be an https URL")
    if parts.username or parts.password:
        raise CIMDError("client_id must not embed credentials")
    host = parts.hostname
    port = parts.port or 443
    ip = resolve_public_ips(host, port, getaddrinfo=getaddrinfo)[0]   # single resolution, pinned
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    get = connector or _pinned_https_get
    status, ctype, data = get(ip, port, host, path, timeout, max_bytes)
    if status != 200:
        raise CIMDError("client_id document HTTP %s (redirects not followed)" % status)
    if ctype.split(";")[0].strip().lower() != "application/json":
        raise CIMDError("client_id document content-type must be application/json")
    if len(data) > max_bytes:
        raise CIMDError("client_id document too large")
    try:
        return json.loads(data)
    except Exception as exc:  # noqa: BLE001
        raise CIMDError("client_id document is not JSON") from exc


@dataclass
class ResolvedClient:
    client_id: str
    redirect_uris: tuple[str, ...]
    source: str   # "cimd" | "preregistered" | "dcr"


@dataclass
class _AuthCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    resource: str
    principal_id: str
    scopes: tuple[str, ...]
    expires_at: float
    device_id: str = ""
    used: bool = False


class AuthorizationServer:
    def __init__(self, *, issuer: str, resource: str, token_store: TokenStore,
                 scopes_supported: tuple[str, ...] = ("mcp:use",),
                 preregistered: Optional[dict[str, tuple[str, ...]]] = None,
                 cimd_fetcher: Callable[[str], dict] = default_cimd_fetcher,
                 enable_dcr: bool = False,
                 code_ttl_seconds: int = 60,
                 token_ttl_seconds: int = 600,
                 clock: Callable[[], float] = time.time):
        self.issuer = issuer.rstrip("/")
        self.resource = resource
        self.tokens = token_store
        self.scopes_supported = scopes_supported
        self.preregistered = dict(preregistered or {})       # client_id -> redirect_uris
        self.cimd_fetcher = cimd_fetcher
        self.enable_dcr = enable_dcr
        self.code_ttl = code_ttl_seconds
        self.token_ttl = token_ttl_seconds
        self._clock = clock
        self._codes: dict[str, _AuthCode] = {}
        self._dcr_clients: dict[str, tuple[str, ...]] = {}

    # discovery ------------------------------------------------------------ #
    def metadata(self) -> tuple[int, list, bytes]:
        doc = {
            "issuer": self.issuer,
            "authorization_endpoint": "%s/authorize" % self.issuer,
            "token_endpoint": "%s/token" % self.issuer,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": list(self.scopes_supported),
            "client_id_metadata_document_supported": True,
        }
        if self.enable_dcr:
            doc["registration_endpoint"] = "%s/register" % self.issuer
        return _json(200, doc)

    # client resolution ---------------------------------------------------- #
    def _resolve_client(self, client_id: str) -> ResolvedClient:
        if client_id in self.preregistered:
            return ResolvedClient(client_id, tuple(self.preregistered[client_id]), "preregistered")
        if self.enable_dcr and client_id in self._dcr_clients:
            return ResolvedClient(client_id, tuple(self._dcr_clients[client_id]), "dcr")
        # CIMD: client_id is an https URL naming its own document (primary dynamic path)
        if client_id.startswith("https://"):
            doc = self.cimd_fetcher(client_id)
            if doc.get("client_id") != client_id:
                raise CIMDError("document client_id does not equal its URL")
            redirects = doc.get("redirect_uris")
            if not isinstance(redirects, list) or not redirects:
                raise CIMDError("document missing redirect_uris")
            if "client_name" not in doc:
                raise CIMDError("document missing client_name")
            return ResolvedClient(client_id, tuple(redirects), "cimd")
        raise CIMDError("unknown client_id and not a CIMD https URL")

    # validation (done BEFORE any login/pairing is ever offered) ----------- #
    #: fields a normalized, validated authorization request carries forward.
    REQUEST_FIELDS = ("client_id", "redirect_uri", "response_type", "code_challenge",
                      "code_challenge_method", "resource", "state", "scope")
    #: per-field length cap, so an oversized value cannot be stashed or reflected.
    MAX_FIELD_LEN = 2048

    def validate(self, params: dict[str, str]):
        """Fully validate an authorization request: registered/CIMD client, EXACT redirect_uri,
        response_type=code, PKCE S256, resource, scope and size limits. Returns
        ``(normalized_request, None)`` or ``(None, error_triple)``. No side effects, mints
        nothing — so an INVALID request can be rejected before any login or device pairing."""
        for k, v in params.items():
            if isinstance(v, str) and len(v) > self.MAX_FIELD_LEN:
                return None, _json(400, {"error": "invalid_request",
                                         "error_description": "%s too long" % k})
        for req in ("client_id", "redirect_uri", "response_type", "code_challenge",
                    "code_challenge_method", "resource"):
            if not params.get(req):
                return None, _json(400, {"error": "invalid_request",
                                         "error_description": "missing %s" % req})
        if params["response_type"] != "code":
            return None, _json(400, {"error": "unsupported_response_type"})
        if params["code_challenge_method"] != "S256":
            return None, _json(400, {"error": "invalid_request",
                                     "error_description": "PKCE S256 required"})
        if params["resource"] != self.resource:
            return None, _json(400, {"error": "invalid_target",
                                     "error_description": "resource mismatch"})
        if urlsplit(params["redirect_uri"]).fragment or "#" in params["redirect_uri"]:
            # a redirect_uri MUST NOT carry a fragment (rejected at validation and registration)
            return None, _json(400, {"error": "invalid_request",
                                     "error_description": "redirect_uri must not contain a fragment"})
        try:
            client = self._resolve_client(params["client_id"])
        except CIMDError as exc:
            return None, _json(400, {"error": "invalid_client", "error_description": str(exc)})
        if params["redirect_uri"] not in client.redirect_uris:
            # per OAuth, an unregistered redirect_uri must NOT be redirected to
            return None, _json(400, {"error": "invalid_request",
                                     "error_description": "redirect_uri mismatch"})
        # required fields always; state/scope ONLY when actually sent (so a present-but-empty
        # state round-trips and an absent one is not fabricated into "").
        norm = {k: str(params[k]) for k in ("client_id", "redirect_uri", "response_type",
                                            "code_challenge", "code_challenge_method", "resource")}
        norm["client_id"] = client.client_id
        for opt in ("state", "scope"):
            if opt in params:
                norm[opt] = str(params[opt])
        return norm, None

    # authorize ------------------------------------------------------------ #
    def authorize(self, params: dict[str, str], *, principal_id: str,
                  granted_scopes: Optional[tuple[str, ...]] = None,
                  device_id: str = "") -> tuple[int, list, bytes]:
        """The user has authenticated to AgentNode out of band; ``principal_id`` is that
        identity and ``granted_scopes`` what they consented to. Returns a 302 redirect with the
        code, or a JSON error. Re-validates everything (never trusts a prevalidation)."""
        norm, err = self.validate(params)
        if err is not None:
            return err
        scopes = granted_scopes if granted_scopes is not None else self.scopes_supported
        scopes = tuple(s for s in scopes if s in self.scopes_supported)
        code = secrets.token_urlsafe(24)
        self._codes[code] = _AuthCode(
            client_id=norm["client_id"], redirect_uri=norm["redirect_uri"],
            code_challenge=norm["code_challenge"], resource=norm["resource"],
            principal_id=principal_id, scopes=scopes, device_id=device_id,
            expires_at=self._clock() + self.code_ttl)
        # Keep the registered redirect_uri's query BYTE-FOR-BYTE (urlsplit does not decode it) and
        # append ONLY the form-encoded `code` and the UNCHANGED `state` (echoed only if the client
        # sent one, including an empty one). We do NOT re-encode the existing query through
        # parse_qsl()+urlencode(), which would canonicalize %20/+/%2B/%2F, drop empties, reorder
        # and fold duplicate ordinary params. A state with &,=,+,%,#,space or Unicode is itself
        # form-encoded here, so it round-trips exactly; exactly one server-generated code is added.
        parts = urlsplit(norm["redirect_uri"])
        added = [("code", code)]
        if "state" in norm:
            added.append(("state", norm["state"]))
        addition = urlencode(added)
        new_query = (parts.query + "&" + addition) if parts.query else addition
        location = urlunsplit((parts.scheme, parts.netloc, parts.path,
                               new_query, ""))   # fragment stays empty (rejected earlier)
        return 302, [("location", location)], b""

    # token ---------------------------------------------------------------- #
    def token(self, params: dict[str, str]) -> tuple[int, list, bytes]:
        if params.get("grant_type") != "authorization_code":
            return _json(400, {"error": "unsupported_grant_type"})
        for req in ("code", "redirect_uri", "code_verifier", "client_id", "resource"):
            if not params.get(req):
                return _json(400, {"error": "invalid_request", "error_description": "missing %s" % req})
        rec = self._codes.get(params["code"])
        if rec is None or rec.used or self._clock() >= rec.expires_at:
            return _json(400, {"error": "invalid_grant", "error_description": "code invalid or expired"})
        if rec.client_id != params["client_id"] or rec.redirect_uri != params["redirect_uri"]:
            return _json(400, {"error": "invalid_grant", "error_description": "code binding mismatch"})
        if rec.resource != params["resource"] or params["resource"] != self.resource:
            return _json(400, {"error": "invalid_target"})
        if pkce_s256_challenge(params["code_verifier"]) != rec.code_challenge:
            return _json(400, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
        rec.used = True  # single-use code
        issued = self.tokens.mint(principal_id=rec.principal_id, audience=rec.resource,
                                  scopes=rec.scopes, issuance_kind="oauth",
                                  ttl_seconds=self.token_ttl, label="oauth",
                                  device_or_connection_id=rec.device_id or None)
        return _json(200, {
            "access_token": issued.plaintext,
            "token_type": "Bearer",
            "expires_in": self.token_ttl,
            "scope": " ".join(issued.record.scopes),
        })

    # DCR (bounded, off by default) ---------------------------------------- #
    def register(self, body: dict) -> tuple[int, list, bytes]:
        if not self.enable_dcr:
            return _json(404, {"error": "not_found"})
        redirects = body.get("redirect_uris")
        if not isinstance(redirects, list) or not redirects:
            return _json(400, {"error": "invalid_client_metadata",
                               "error_description": "redirect_uris required"})
        if any(not str(u).startswith("https://") and not str(u).startswith("http://127.0.0.1")
               for u in redirects):
            return _json(400, {"error": "invalid_redirect_uri"})
        if any("#" in str(u) or urlsplit(str(u)).fragment for u in redirects):
            # a redirect_uri with a fragment is refused at REGISTRATION, not just at /authorize
            return _json(400, {"error": "invalid_redirect_uri",
                               "error_description": "redirect_uri must not contain a fragment"})
        client_id = "dcr-" + secrets.token_hex(8)
        self._dcr_clients[client_id] = tuple(redirects)
        return _json(201, {"client_id": client_id, "redirect_uris": list(redirects),
                           "token_endpoint_auth_method": "none",
                           "grant_types": ["authorization_code"], "response_types": ["code"]})
