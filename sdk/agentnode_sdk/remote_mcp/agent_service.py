"""`rmcp-agent`: the ONE unprivileged service behind every `/agent/*` route (local-worker slice 2, Codex Q-LW2-0003
Q1, D6 as amended).

    /agent/v1/slot          -> the 443 edge (`reverse_edge.EdgeServer`, its own unix socket) -> the reverse hub
    /agent/v1/pair/start    -> a pairing begins (agent, JSON)
    /agent/v1/pair/poll     -> the agent asks, signed with its worker key
    /agent/v1/pair/ack      -> the agent confirms it collected the result
    /agent/pair             -> the person approves in a browser (GET page, POST form)
    /agent/v1/device/retire -> an uninstalling agent asks for its own retirement (signed)

It runs as its own account, holds neither the operator key nor the CA, and reads only what root projects for it
(`agent_authority`: devices, accounts, session times, the kill switch, a mirror of the signed register and the public
trust lists). It writes only its pairing store. Certificates, register generations and pair keys are made by the root
pairing helper, whose one operation it calls with an opaque authorization id. Root `rmcp-control` reaches dial-back
targets only through this service's relay socket, and judges every admission and renewal itself.

Off unless configured: nothing here starts without an `agent.json`, and nginx carries no `/agent/*` location unless an
operator installs one.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

from agentnode_sdk.remote_mcp import agent_authority as AA
from agentnode_sdk.remote_mcp import pairing_helper as PH
from agentnode_sdk.worker import pairing as WP

PAIR_SECONDS = 600
POLL_INTERVAL = 5
MAX_PENDING = 256
START_PER_MINUTE_PER_IP = 5
WRONG_CODES_BEFORE_LOCK = 5
LOCK_SECONDS = 900
PROOF_SKEW = 60
MAX_BODY = 16384
#: unambiguous upper-case letters (no vowels, no I/O/U/Y): 20^8 > 2.5e10 codes
USER_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
_USER_CODE = re.compile(r"[%s]{4}-?[%s]{4}\Z" % (USER_ALPHABET, USER_ALPHABET))
SESSION_COOKIE = "__Host-agentnode"
CSRF_SECONDS = 600


class Refused(Exception):
    def __init__(self, code: str, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


def _now() -> float:
    return time.time()


def _write_private(path: Path, doc: dict, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (json.dumps(doc, sort_keys=True) + "\n").encode("utf-8")
    if exclusive:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return
    tmp = path.with_name("." + path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))


def normal_user_code(text: str) -> str:
    t = str(text or "").strip().upper().replace(" ", "")
    if not _USER_CODE.match(t):
        return ""
    return t.replace("-", "")


def user_code_hash(code: str) -> str:
    return WP.sha256_hex(("agentnode-user-code\n" + normal_user_code(code)).encode("ascii"))


# ---------------------------------------------------------------------- the pairing store
class PairingStore:
    """Pending pairings, the authorizations the browser created, and retirement requests -- this service's only
    writable state. Codes are stored as hashes; the device code itself never touches the disk."""

    def __init__(self, root, *, helper: Callable[[str], dict] | None = None, clock: Callable[[], float] = _now,
                 max_pending: int = MAX_PENDING) -> None:
        self.root = Path(root)
        self.helper = helper
        self.clock = clock
        self.max_pending = int(max_pending)
        self._lock = threading.RLock()
        self._start_rate: dict[str, list] = {}
        self._wrong: dict[str, list] = {}
        for d in ("pending", "users", "authorizations", "retire"):
            (self.root / d).mkdir(parents=True, exist_ok=True, mode=0o700)

    # ------------------------------------------------------------ agent side
    def _pending(self, dch: str) -> dict | None:
        p = self.root / "pending" / (dch + ".json")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def _save(self, rec: dict) -> None:
        _write_private(self.root / "pending" / (rec["device_code_hash"] + ".json"), rec)

    def start(self, body: dict, ip: str) -> dict:
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        now = self.clock()
        with self._lock:
            hits = [t for t in self._start_rate.get(ip, []) if now - t < 60]
            if len(hits) >= START_PER_MINUTE_PER_IP:
                raise Refused("pair_rate_limited", 429)
            hits.append(now)
            self._start_rate[ip] = hits
            if not isinstance(body, dict) or set(body) != {"protocol", "csr", "transport_key", "name", "concurrency",
                                                           "queue", "platform"} or body["protocol"] != WP.PROTOCOL:
                raise Refused("pair_request_invalid")
            try:
                csr = x509.load_pem_x509_csr(str(body["csr"]).encode("ascii"))
                key = csr.public_key()
                if not csr.is_signature_valid or not isinstance(key, ec.EllipticCurvePublicKey) \
                        or key.curve.name != "secp256r1":
                    raise ValueError
                raw = WP.unb64(body["transport_key"])
                if len(raw) != 65:
                    raise ValueError
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
                spki = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            except (ValueError, TypeError):
                raise Refused("pair_request_invalid") from None
            name = re.sub(r"[^A-Za-z0-9 ._-]", "", str(body["name"]))[:64] or "worker"
            for k, top in (("concurrency", PH.MAX_CONCURRENCY), ("queue", PH.MAX_QUEUE)):
                if not isinstance(body[k], int) or isinstance(body[k], bool) or not 1 <= body[k] <= top:
                    raise Refused("pair_request_invalid")
            platform = body["platform"] if isinstance(body["platform"], dict) else {}
            platform = {str(k)[:32]: str(v)[:64] for k, v in list(platform.items())[:12]}
            live = 0
            for p in (self.root / "pending").glob("*.json"):
                try:
                    if json.loads(p.read_text(encoding="utf-8")).get("expires_at", 0) > now:
                        live += 1
                except (OSError, ValueError):
                    continue
            if live >= self.max_pending:
                raise Refused("pair_rate_limited", 429, "too many pairings are pending")
            device_code = secrets.token_urlsafe(32)                    # 256 bits
            user = "".join(secrets.choice(USER_ALPHABET) for _ in range(8))
            dch = WP.code_hash(device_code)
            rec = {"device_code_hash": dch, "user_code_hash": user_code_hash(user), "created_at": now,
                   "expires_at": now + PAIR_SECONDS, "interval": POLL_INTERVAL, "last_poll": 0.0,
                   "status": "pending", "csr": str(body["csr"]), "spki_sha256": WP.sha256_hex(spki),
                   "transport_key": body["transport_key"], "name": name, "concurrency": body["concurrency"],
                   "queue": body["queue"], "platform": platform, "authorization_id": "", "account": "",
                   "result": None}
            self._save(rec)
            _write_private(self.root / "users" / (rec["user_code_hash"] + ".json"), {"device_code_hash": dch})
        return {"device_code": device_code, "user_code": user[:4] + "-" + user[4:], "interval": POLL_INTERVAL,
                "expires_in": PAIR_SECONDS}

    def _proved(self, body: dict, what: str) -> dict:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        if not isinstance(body, dict) or set(body) != {"device_code", "ts", "proof"}:
            raise Refused("pair_request_invalid")
        rec = self._pending(WP.code_hash(str(body["device_code"])))
        if rec is None:
            raise Refused("pair_code_unknown")
        try:
            seconds = str(body["ts"])
            if not seconds.isdigit() or abs(self.clock() - int(seconds)) > PROOF_SKEW:
                raise Refused("pair_proof_invalid")
            key = x509.load_pem_x509_csr(rec["csr"].encode("ascii")).public_key()
            key.verify(WP.unb64(body["proof"]), WP.poll_message(rec["device_code_hash"], seconds, what),
                       ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError):
            raise Refused("pair_proof_invalid") from None
        return rec

    def poll(self, body: dict) -> dict:
        now = self.clock()
        with self._lock:
            rec = self._proved(body, "poll")
            if rec["status"] == "collected":
                raise Refused("pair_code_consumed")
            if rec["status"] == "denied":
                raise Refused("access_denied")
            if rec["status"] in ("pending", "approved") and now >= rec["expires_at"] and not rec.get("result"):
                raise Refused("pair_code_expired")
            if now - float(rec["last_poll"]) < rec["interval"] - 0.5:
                rec["interval"] = int(rec["interval"]) + 5
                rec["last_poll"] = now
                self._save(rec)
                raise Refused("slow_down")
            rec["last_poll"] = now
            self._save(rec)
            if rec["status"] == "pending":
                raise Refused("authorization_pending")
            if rec["status"] == "approved" and not rec.get("result"):
                self._materialize(rec)
            if not rec.get("result"):
                raise Refused("authorization_pending")
            return {"result": rec["result"]}

    def ack(self, body: dict) -> dict:
        with self._lock:
            rec = self._proved(body, "ack")
            if rec["status"] == "collected":
                raise Refused("pair_code_consumed")
            if not rec.get("result"):
                raise Refused("authorization_pending")
            rec["status"] = "collected"
            rec["result"] = {"collected": True}                      # nothing kept that a later poll could take
            self._save(rec)
            return {"status": "collected"}

    def _materialize(self, rec: dict) -> None:
        if self.helper is None:
            return
        try:
            rec["result"] = self.helper(rec["authorization_id"])
            rec["status"] = "materialized"
            self._save(rec)
        except PH.HelperRefused as refused:
            if refused.code in ("pair_not_permitted", "pair_account_inactive", "pair_code_expired",
                                "pair_authorization_invalid"):
                rec["status"] = "denied"
                self._save(rec)
                raise Refused("access_denied", 400, refused.code) from None
            # the helper is down or failed transiently: the authorization stands, the next poll asks again

    # ------------------------------------------------------------ browser side
    def lookup(self, code: str, session_fp: str) -> dict:
        """The pending pairing a typed user code names, or a refusal; five wrong codes lock the session 15 min."""
        now = self.clock()
        with self._lock:
            wrong = [t for t in self._wrong.get(session_fp, []) if now - t < LOCK_SECONDS]
            if len(wrong) >= WRONG_CODES_BEFORE_LOCK:
                raise Refused("pair_locked", 429)
            found = None
            if normal_user_code(code):
                p = self.root / "users" / (user_code_hash(code) + ".json")
                if p.exists():
                    found = self._pending(json.loads(p.read_text(encoding="utf-8"))["device_code_hash"])
            if found is None or found["status"] != "pending":
                wrong.append(now)
                self._wrong[session_fp] = wrong
                raise Refused("pair_user_code_invalid")
            if now >= found["expires_at"]:
                raise Refused("pair_code_expired")
            return found

    def decide(self, code: str, session_fp: str, *, approve: bool, account: str, approver: str,
               session_opened_at: float) -> dict:
        now = self.clock()
        with self._lock:
            rec = self.lookup(code, session_fp)
            if not approve:
                rec["status"] = "denied"
                self._save(rec)
                return rec
            if not 0 <= now - float(session_opened_at) <= PH.RECENT_SESSION_SECONDS:
                raise Refused("pair_not_permitted", 403, "sign in again: the session is older than 300 s")
            aid = secrets.token_hex(32)
            auth = {"schema": PH.AUTH_SCHEMA, "authorization_id": aid, "device_code_hash": rec["device_code_hash"],
                    "account": account, "approver_device": approver, "approver_session": session_fp,
                    "approved_at": now, "expires_at": min(rec["expires_at"], now + PH.AUTHORIZATION_SECONDS),
                    "csr": rec["csr"], "spki_sha256": rec["spki_sha256"], "transport_key": rec["transport_key"],
                    "name": rec["name"], "concurrency": rec["concurrency"], "queue": rec["queue"]}
            _write_private(self.root / "authorizations" / (aid + ".json"), auth, exclusive=True)
            rec.update(status="approved", authorization_id=aid, account=account)
            self._save(rec)
            try:
                self._materialize(rec)
            except Refused:
                pass
            return rec

    # ------------------------------------------------------------ retirement requests
    def retire_request(self, body: dict, tls_trust, deployment: str) -> dict:
        """An uninstalling agent's signed request to retire itself. Stored immutably for the root reconciler, which
        verifies it again before it withdraws anything."""
        req = check_retire_request(body, tls_trust, deployment, now=self.clock())
        path = self.root / "retire" / (req["request_id"] + ".json")
        try:
            _write_private(path, req, exclusive=True)
        except FileExistsError:
            pass                                                       # a replay converges on the same request
        return {"status": "accepted", "request_id": req["request_id"]}


RETIRE_KEYS = ("deployment", "instance", "target_id", "device_id", "request_id", "ts", "certificate", "proof")
RETIRE_DOMAIN = b"agentnode-device-retire/1\n"


def retire_message(req: dict) -> bytes:
    return RETIRE_DOMAIN + "\n".join(str(req[k]) for k in RETIRE_KEYS[:6]).encode("ascii")


def check_retire_request(body: dict, trust, deployment: str, *, now: float) -> dict:
    """Shape, freshness (+-60 s), the worker certificate of this deployment for that instance, and its signature."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    from agentnode_sdk.pki import identity as _identity

    if not isinstance(body, dict) or set(body) != set(RETIRE_KEYS) or body["deployment"] != deployment \
            or not re.match(r"[0-9a-f]{32}\Z", str(body["request_id"])) or not str(body["ts"]).isdigit():
        raise Refused("retire_request_invalid")
    if abs(now - int(body["ts"])) > PROOF_SKEW:
        raise Refused("retire_request_stale")
    try:
        der = WP.unb64(body["certificate"])
        cert = x509.load_der_x509_certificate(der)
        cert.verify_directly_issued_by(trust.anchor())
        who = _identity.check_peer(der, deployment=deployment, expected_role=_identity.WORKER,
                                   accept_instances=frozenset({str(body["instance"])}), trust=trust)
        if str(who.instance) != body["instance"]:
            raise ValueError
        cert.public_key().verify(WP.unb64(body["proof"]), retire_message(body), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError, _identity.PeerRefused):
        raise Refused("retire_proof_invalid") from None
    return dict(body)


# ---------------------------------------------------------------------- the ASGI app
class AgentApp:
    def __init__(self, *, store: PairingStore, authority: AA.Authority, host: str, edge_trust=None,
                 deployment: str = "", trusted_proxy: bool = True, secret: bytes | None = None,
                 clock: Callable[[], float] = _now) -> None:
        self.store = store
        self.authority = authority
        self.host = host
        self.origin = "https://" + host
        self.edge_trust = edge_trust
        self.deployment = deployment
        self.trusted_proxy = trusted_proxy
        self.secret = secret or secrets.token_bytes(32)
        self.clock = clock
        self.lifespan_hooks: list = []

    # ------------------------------------------------------------ plumbing
    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    for start, _stop in self.lifespan_hooks:
                        await start()
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    for _start, stop in reversed(self.lifespan_hooks):
                        await stop()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        if headers.get("host", "") != self.host:
            return await self._json(send, 421, {"error": "misdirected_request"})
        path, method = scope["path"], scope["method"]
        try:
            if path in (WP.START_PATH, WP.POLL_PATH, WP.ACK_PATH, "/agent/v1/device/retire"):
                if method != "POST":
                    raise Refused("method_not_allowed", 405)
                if "origin" in headers or "cookie" in headers:
                    raise Refused("pair_request_invalid", 400, "an agent request carries no Origin or Cookie")
                if (headers.get("content-type") or "").split(";")[0].strip() != "application/json":
                    raise Refused("pair_request_invalid", 415)
                body = await self._body(receive)
                try:
                    doc = json.loads(body.decode("utf-8"))
                except ValueError:
                    raise Refused("pair_request_invalid") from None
                out = await asyncio.to_thread(self._agent_call, path, doc, self._ip(scope, headers))
                return await self._json(send, 200, out)
            if path == WP.PAGE_PATH and method == "GET":
                return await self._page(send, *await asyncio.to_thread(self._page_get, headers))
            if path == WP.PAGE_PATH and method == "POST":
                if headers.get("origin") != self.origin:
                    raise Refused("origin_forbidden", 403)
                form = self._form(await self._body(receive))
                return await self._page(send, *await asyncio.to_thread(self._page_post, headers, form))
            return await self._json(send, 404, {"error": "not_found"})
        except Refused as refused:
            if path == WP.PAGE_PATH:
                return await self._page(send, refused.status if refused.status >= 400 else 400,
                                        "<h1>Not paired</h1><p>%s</p>" % html.escape(_say(refused)))
            return await self._json(send, refused.status, {"error": refused.code})
        except AA.AuthorityUnavailable:
            return await self._json(send, 503, {"error": "authority_unavailable"})

    def _agent_call(self, path: str, doc, ip: str) -> dict:
        if path == WP.START_PATH:
            out = self.store.start(doc, ip)
            out["verification_uri"] = self.origin + WP.PAGE_PATH
            return out
        if path == WP.POLL_PATH:
            return self.store.poll(doc)
        if path == WP.ACK_PATH:
            return self.store.ack(doc)
        if self.edge_trust is None:
            raise Refused("not_found", 404)
        return self.store.retire_request(doc, self.edge_trust(), self.deployment)

    def _ip(self, scope, headers) -> str:
        if self.trusted_proxy and headers.get("x-forwarded-for"):
            return headers["x-forwarded-for"].split(",")[-1].strip()[:64]
        client = scope.get("client") or ("?", 0)
        return str(client[0])

    async def _body(self, receive) -> bytes:
        data = b""
        while True:
            msg = await receive()
            data += msg.get("body", b"")
            if len(data) > MAX_BODY:
                raise Refused("request_too_large", 413)
            if not msg.get("more_body"):
                return data

    @staticmethod
    def _form(body: bytes) -> dict:
        from urllib.parse import parse_qsl
        out = {}
        for k, v in parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True):
            if k in out:
                raise Refused("pair_request_invalid")
            out[k] = v
        return out

    async def _json(self, send, status: int, obj) -> None:
        body = json.dumps(obj, sort_keys=True).encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    async def _page(self, send, status: int, body_html: str, nonce: str = "") -> None:
        csp = ("default-src 'none'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; "
               "base-uri 'none'; frame-ancestors 'none'; script-src %s" % ("'nonce-%s'" % nonce if nonce else "'none'"))
        page = ("<!doctype html><meta charset=\"utf-8\"><title>Connect a machine - AgentNode</title>"
                "<style>body{font:16px system-ui;max-width:40em;margin:3em auto;padding:0 1em}"
                "code,.fp{font-family:ui-monospace,monospace}</style>" + body_html)
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"text/html; charset=utf-8"), (b"content-security-policy", csp.encode("ascii")),
            (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"),
            (b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": page.encode("utf-8")})

    # ------------------------------------------------------------ the person
    def _who(self, headers) -> dict | None:
        """The signed-in person, from the gateway's session cookie and root's projection; never a worker device."""
        sid = ""
        for part in (headers.get("cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == SESSION_COOKIE:
                sid = v
        found = self.authority.session(sid)
        if not found:
            return None
        device = self.authority.device(found["client_id"])
        if device is None or device.get("kind") == AA.WORKER_DEVICE:
            return None
        return {"session": found["session"], "client_id": found["client_id"], "account": device["account_id"],
                "opened_at": float(found.get("opened_at") or 0), "capabilities": device.get("capabilities") or [],
                "active": self.authority.account_active(device["account_id"])}

    def _csrf(self, session_fp: str, issued: int | None = None) -> str:
        issued = int(issued if issued is not None else self.clock())
        mac = hmac.new(self.secret, ("%s|%d" % (session_fp, issued)).encode("ascii"), hashlib.sha256).hexdigest()
        return "%d.%s" % (issued, mac)

    def _csrf_ok(self, session_fp: str, value: str) -> bool:
        try:
            issued, _mac = str(value).split(".", 1)
            fresh = 0 <= self.clock() - int(issued) <= CSRF_SECONDS
        except ValueError:
            return False
        return fresh and hmac.compare_digest(self._csrf(session_fp, int(issued)), str(value))

    def _sign_in(self, why: str) -> tuple:
        nonce = secrets.token_urlsafe(16)
        return (200, "<h1>Connect a machine to your AgentNode account</h1><p>%s</p>"
                     "<form id=\"f\"><input name=\"code\" placeholder=\"joining code\" autocomplete=\"off\"> "
                     "<button type=\"submit\">Sign in</button></form><p id=\"msg\"></p>"
                     "<script nonce=\"%s\">document.getElementById('f').addEventListener('submit',async e=>{"
                     "e.preventDefault();const fd=new FormData(e.target);"
                     "const r=await fetch('/v1/session',{method:'POST',headers:{'content-type':'application/json'},"
                     "body:JSON.stringify({code:fd.get('code'),client_name:'browser'})});"
                     "if(r.ok){window.location.reload();}else{document.getElementById('msg').textContent="
                     "'sign-in failed ('+r.status+')';}});</script>" % (html.escape(why), nonce), nonce)

    def _page_get(self, headers) -> tuple:
        who = self._who(headers)
        if who is None:
            return self._sign_in("Sign in with a joining code from a device of your account.")
        if not who["active"]:
            raise Refused("pair_account_inactive", 403)
        if AA.MANAGE_DEVICES not in who["capabilities"]:
            raise Refused("pair_not_permitted", 403)
        if not 0 <= self.clock() - who["opened_at"] <= PH.RECENT_SESSION_SECONDS:
            return self._sign_in("Connecting a machine needs a sign-in from the last five minutes. "
                                 "Use a fresh joining code.")
        return (200, "<h1>Connect a machine</h1><p>Enter the code your machine shows.</p>"
                     "<form method=\"post\"><input type=\"hidden\" name=\"csrf\" value=\"%s\">"
                     "<input type=\"hidden\" name=\"action\" value=\"lookup\">"
                     "<input name=\"user_code\" placeholder=\"XXXX-XXXX\" autocomplete=\"off\"> "
                     "<button type=\"submit\">Continue</button></form>" % self._csrf(who["session"]))

    def _page_post(self, headers, form: dict) -> tuple:
        who = self._who(headers)
        if who is None:
            raise Refused("pair_not_permitted", 403, "not signed in")
        if not self._csrf_ok(who["session"], form.get("csrf", "")):
            raise Refused("pair_not_permitted", 403, "the form expired; open the page again")
        if not who["active"]:
            raise Refused("pair_account_inactive", 403)
        if AA.MANAGE_DEVICES not in who["capabilities"]:
            raise Refused("pair_not_permitted", 403)
        code = form.get("user_code", "")
        action = form.get("action", "")
        if action == "lookup":
            rec = self.store.lookup(code, who["session"])
            fp = WP.fingerprint_of_csr(rec["csr"].encode("ascii"))
            plat = ", ".join("%s=%s" % (k, v) for k, v in sorted(rec["platform"].items()))
            hidden = ("<input type=\"hidden\" name=\"csrf\" value=\"%s\"><input type=\"hidden\" name=\"user_code\" "
                      "value=\"%s\">" % (self._csrf(who["session"]), html.escape(code, quote=True)))
            return (200, "<h1>Connect this machine?</h1>"
                         "<p>Name: <b>%s</b></p><p>Key fingerprint (must match what the machine printed):</p>"
                         "<p class=\"fp\">%s</p><p>It says about itself (not verified): <code>%s</code></p>"
                         "<p>At most %d run(s) at once, %d queued. It will only run work you approve, one call at a "
                         "time, and only after AgentNode has observed it isolates that work.</p>"
                         "<form method=\"post\">%s<input type=\"hidden\" name=\"action\" value=\"approve\">"
                         "<button type=\"submit\">Approve</button></form>"
                         "<form method=\"post\">%s<input type=\"hidden\" name=\"action\" value=\"deny\">"
                         "<button type=\"submit\">Deny</button></form>"
                         % (html.escape(rec["name"]), html.escape(fp), html.escape(plat), rec["concurrency"],
                            rec["queue"], hidden, hidden))
        if action in ("approve", "deny"):
            rec = self.store.decide(code, who["session"], approve=action == "approve", account=who["account"],
                                    approver=who["client_id"], session_opened_at=who["opened_at"])
            if action == "deny":
                return 200, "<h1>Denied</h1><p>The machine was not connected.</p>"
            return (200, "<h1>Approved</h1><p>Return to your machine; it finishes by itself. It appears in your "
                         "device list as <b>%s</b> and can be withdrawn there at any time.</p>"
                    % html.escape(rec["name"]))
        raise Refused("pair_request_invalid")


def _say(refused: Refused) -> str:
    return {"pair_user_code_invalid": "That code is not a pending pairing.",
            "pair_locked": "Too many wrong codes. Try again in 15 minutes.",
            "pair_code_expired": "That code has expired. Start the pairing on the machine again.",
            "pair_not_permitted": "This device may not connect machines, or the sign-in is too old.",
            "pair_account_inactive": "This account is suspended.",
            "origin_forbidden": "Refused: this request did not come from this page."}.get(refused.code, refused.code)


# ---------------------------------------------------------------------- wiring
def build(config_path: str):
    """(asgi_app, hub, edge, relay) from `agent.json`. Nothing starts without the config."""
    from agentnode_sdk.remote_mcp import reverse_edge as RE
    from agentnode_sdk.remote_mcp import reverse_hub as R
    from agentnode_sdk.remote_mcp import targets as T
    from agentnode_sdk.worker.tls import TlsSettings

    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    mirror = Path(cfg["mirror_dir"])
    authority = AA.Authority(mirror / "authority.json")
    op_pub = bytes.fromhex((mirror / "operator_pub.hex").read_text(encoding="utf-8").strip())
    register = T.Register(str(mirror), op_pub, pin_clock=False)
    tls = TlsSettings(certificate=cfg["tls"]["certificate"], key=cfg["tls"]["key"],
                      anchor=str(mirror / "trust" / "ca.pem"), deployment=cfg["deployment"],
                      accept=frozenset(cfg["accept"]), revocation_list=str(mirror / "trust" / "revoked.crl"),
                      floor=str(mirror / "trust" / "gateway.floor"),
                      identity_tombstones=str(mirror / "trust" / "revoked-identities.json"), tombstones_required=True)
    view = R.target_view(register)

    def kill() -> str:
        return authority.stopped()
    eligible = R.eligibility(view, R.reverse_device_authority(authority.reverse_device_error), kill)
    hub = R.ReverseHub(None, tls, targets=view, eligible=eligible,
                       max_total=int(cfg.get("max_total") or R.DEFAULT_MAX_TOTAL))
    edge = RE.EdgeServer(hub, host=cfg["host"], path=cfg["edge_socket"], group=cfg.get("edge_group", ""))
    relay = R.RelayServer(hub, cfg["relay_socket"])
    helper_socket = cfg["helper_socket"]
    store = PairingStore(cfg["store"], helper=lambda aid: PH.call(helper_socket, aid))
    secret_path = Path(cfg["store"]) / "form-secret"
    if not secret_path.exists():
        fd = os.open(str(secret_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(secrets.token_bytes(32))
    app = AgentApp(store=store, authority=authority, host=cfg["host"], edge_trust=lambda: tls.trust("gateway"),
                   deployment=cfg["deployment"], secret=secret_path.read_bytes())

    async def start():
        hub.start()
        edge.start()
        relay.start()
        if cfg.get("relay_group"):
            import grp
            os.chown(cfg["relay_socket"], -1, grp.getgrnam(cfg["relay_group"]).gr_gid)
            os.chmod(cfg["relay_socket"], 0o660)

    async def stop():
        relay.stop()
        edge.stop()
        hub.stop()
    app.lifespan_hooks.append((start, stop))
    return app, hub, edge, relay


def main(argv=None) -> int:
    import argparse

    import uvicorn

    p = argparse.ArgumentParser(prog="rmcp-agent")
    p.add_argument("config")
    args = p.parse_args(argv)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("rmcp-agent never runs as root", flush=True)
        return 2
    app, _hub, _edge, _relay = build(args.config)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    # the pairing routes on LOOPBACK only (nginx proxies the exact /agent/v1/pair/* and /agent/pair locations to it)
    host, _, port = str(cfg["http_listen"]).rpartition(":")
    import ipaddress
    if not ipaddress.ip_address(host).is_loopback:
        print("rmcp-agent's pairing routes listen on loopback only", flush=True)
        return 2
    uvicorn.run(app, host=host, port=int(port), log_level="warning", proxy_headers=False)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
