"""The account-aware catalog adapter behind the remote MCP transport.

The transport authenticates; this adapter decides, per AUTHENTICATED ACCOUNT, what the account may
see and call, and runs a call through the one frozen ``prepare -> human approval -> submit`` path
on the existing mTLS worker. It replaces the single-capability wiring in production; nothing reads
``capability_id.txt`` here.

* ``tools/list`` is the account's active entries (explicit list, or the default), after the three
  fixed meta-tools, ordered by tool name over ONE verified catalog snapshot, paginated at 50 with an
  authenticated cursor bound to (format, operation, account, generation, page size, offset, query).
* ``agentnode_search`` / ``agentnode_describe`` return a constructed, safe projection of the
  account's ACTIVE entries only; ``agentnode_run`` resolves its target exactly like a direct call
  and goes through the SAME :meth:`_execute`, so both yield the identical invocation hash.
* Account binding (Q-CAT-0001 C4): the server computes the invocation nonce over account, public
  tool name, capability id, exact version, manifest digest, artifact digest, full tree digest,
  internal export and canonical arguments. The nonce is inside the frozen ``Invocation.binding()``,
  so the invocation hash -- and with it the single-use human approval -- belongs to exactly one
  account, one catalog entry and one argument set. No client field takes part in resolution.
* Run admission is linearised against revocation (:meth:`Catalog.admit`, under the catalog lock):
  a call not admitted before a revocation commits never reaches the worker and keeps its approval
  unconsumed; an admitted call completes and is audited.
* Every decision is recorded in the signed catalog ledger BEFORE it takes effect; a run's terminal
  event is recorded afterwards and never lost (recovery file).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any, Callable

from agentnode_sdk.capability.approval import ApprovalError
from agentnode_sdk.capability.usability import UsabilityClient
from agentnode_sdk.remote_mcp.catalog import (META_TOOLS, Catalog, Entry, RunNotAdmitted, Snapshot,
                                              canonical)
from agentnode_sdk.remote_mcp.catalog_audit import CatalogLedger, digest_of
from agentnode_sdk.remote_mcp.target_router import TARGET_META_KEY, TargetRefusal
from agentnode_sdk.remote_mcp.targets import TargetsUnavailable

PAGE_SIZE = 50
CURSOR_VERSION = 1
MAX_CURSOR_CHARS = 1024
MAX_QUERY_CHARS = 200
#: the namespaced key that carries the catalog generation in modern list results
GENERATION_META_KEY = "net.agentnode/catalogGeneration"
#: client routing claims are never accepted: any _meta key under this prefix is refused
RESERVED_META_PREFIX = "agentnode/"
_NONCE_DOMAIN = b"agentnode.catalog.v1/nonce\n"
_CURSOR_DOMAIN = b"agentnode.catalog.v1/cursor\n"
APPROVAL_COMMAND = ("python -m agentnode_sdk.capability.usability_cli approve --stand {stand} "
                    "--invocation-hash {invocation_hash} --audience {audience} --actor <your-name>")

META_SCHEMAS = {
    "agentnode_search": {
        "description": "Search the AgentNode tools active for your account. Returns a page of matches.",
        "inputSchema": {"type": "object", "additionalProperties": False,
                        "properties": {"query": {"type": "string", "maxLength": MAX_QUERY_CHARS},
                                       "cursor": {"type": "string"}}}},
    "agentnode_describe": {
        "description": "Describe one AgentNode tool active for your account (schema, version, permissions).",
        "inputSchema": {"type": "object", "additionalProperties": False, "required": ["tool"],
                        "properties": {"tool": {"type": "string"}}}},
    "agentnode_run": {
        "description": "Run one AgentNode tool active for your account. A human must approve each call.",
        "inputSchema": {"type": "object", "additionalProperties": False, "required": ["tool", "arguments"],
                        "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}}}},
}
#: With execution targets (target-router), `agentnode_run` also takes an optional target WISH. It is
#: resolved and authorized by the server exactly like a direct call's `_meta` wish, never trusted.
RUN_SCHEMA_WITH_TARGET = {
    "description": ("Run one AgentNode tool active for your account. A human must approve each call. "
                    "`target` optionally asks for one of your execution targets (see agentnode_describe)."),
    "inputSchema": {"type": "object", "additionalProperties": False, "required": ["tool", "arguments"],
                    "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"},
                                   "target": {"type": "string", "maxLength": 64}}}}
CUSTOMER_LABEL = ("executed on a customer-controlled worker; isolation is observed by the control plane, "
                  "not proven")


class CatalogMCPAdapter:
    #: the transport passes the authenticated principal to ``handle_for`` (never ``handle``)
    account_aware = True

    def __init__(self, *, catalog: Catalog, client: UsabilityClient, ledger: CatalogLedger,
                 approvals_dir: str, stand: str, cursor_key: bytes,
                 barrier: Callable[[str], None] | None = None, router=None,
                 authority: Callable[[Any], str | None] | None = None,
                 kill_switch: Callable[[], str] | None = None, reconcile_wait: float = 10.0):
        self.catalog = catalog
        self.client = client
        self.ledger = ledger
        self.approvals_dir = approvals_dir
        self.stand = stand
        self._cursor_key = cursor_key
        self._barrier = barrier or (lambda name: None)
        #: Target-router: where a call runs. None keeps the single configured worker (the stand).
        self.router = router
        #: The live account/device re-check, run again at admission (T13). None: not available here.
        self.authority = authority
        #: The gateway kill switch: "" when taking work, else the operator's reason (T13).
        self.kill_switch = kill_switch
        self.reconcile_wait = reconcile_wait
        self._indeterminate_dir = os.path.join(stand, "targets", "indeterminate")
        # what THIS process admitted is in flight, not abandoned: only earlier admissions are settled
        self._started_at = time.time()
        self._settlement_pending = router is not None
        self._settlement_lock = threading.Lock()

    # ------------------------------------------------------------------ transport surface
    def handle(self, msg):  # pragma: no cover - the transport never calls it for this adapter
        raise RuntimeError("the catalog adapter answers only authenticated requests (handle_for)")

    @staticmethod
    def is_discovery_call(msg: dict) -> bool:
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        return msg.get("method") == "tools/call" and params.get("name") in ("agentnode_search", "agentnode_describe")

    def handle_for(self, principal, msg: dict, *, era: str = "modern", grant=None) -> dict | None:
        mid = msg.get("id")
        method = msg.get("method")
        ctx = {"account": principal.principal_id, "token_id": principal.token_id, "era": era}
        if method == "tools/list":
            return self._tools_list(ctx, mid, msg.get("params") if isinstance(msg.get("params"), dict) else {})
        if method == "tools/call":
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            return self._tools_call(ctx, mid, params, principal=principal, grant=grant)
        if mid is None:
            return None
        return _rpc_error(mid, -32601, f"method not found: {method}")

    def record_limit_refusal(self, principal, msg: dict, reason: str, era: str) -> None:
        """A transport limit refused this request (rate, concurrency, queue): record it with its
        stable reason code. Raises AuditUnavailable if it cannot be recorded."""
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        fields = {"request": _safe(msg.get("method")), "decision": "refused", "reason": reason,
                  "account": principal.principal_id, "token_id": principal.token_id, "era": era}
        if msg.get("method") == "tools/call":
            fields["tool"] = _safe(params.get("name"))
        self.ledger.append("catalog_decision", **fields)

    # ------------------------------------------------------------------ list
    def _tool_descriptor(self, entry: Entry) -> dict[str, Any]:
        man = self.client.index.resolve(entry.capability_id, entry.version).manifest
        tool = next(t for t in man["tools"] if t["name"] == entry.export)
        return {"name": entry.tool, "description": tool.get("description", ""),
                "inputSchema": tool.get("input_schema") or {"type": "object"}}

    def _tools_list(self, ctx, mid, params) -> dict:
        snap = self.catalog.load()
        schemas = dict(META_SCHEMAS, agentnode_run=RUN_SCHEMA_WITH_TARGET) if self.router else META_SCHEMAS
        items: list[dict[str, Any]] = [{"name": n, **schemas[n]} for n in META_TOOLS]
        items += [self._tool_descriptor(e) for e in snap.active_entries(ctx["account"])]
        try:
            offset = self._read_cursor(params.get("cursor"), "tools/list", ctx["account"], snap.generation,
                                       len(items), "")
        except ValueError as exc:
            self.ledger.append("catalog_decision", request="tools/list", decision="refused",
                               reason="invalid_cursor", generation=snap.generation, **ctx)
            return _rpc_error(mid, -32602, f"invalid cursor: {exc}")
        page = items[offset:offset + PAGE_SIZE]
        result: dict[str, Any] = {"tools": page, "_meta": {GENERATION_META_KEY: snap.generation}}
        if offset + PAGE_SIZE < len(items):
            result["nextCursor"] = self._cursor("tools/list", ctx["account"], snap.generation,
                                                offset + PAGE_SIZE, "")
        self.ledger.append("catalog_decision", request="tools/list", decision="listed",
                           generation=snap.generation, offset=offset, count=len(page), **ctx)
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    # ------------------------------------------------------------------ cursor
    def _cursor(self, op: str, account: str, generation: int, offset: int, query_digest: str) -> str:
        payload = canonical({"v": CURSOR_VERSION, "op": op, "acct": account, "gen": generation,
                             "ps": PAGE_SIZE, "off": offset, "q": query_digest})
        mac = hmac.new(self._cursor_key, _CURSOR_DOMAIN + payload, hashlib.sha256).digest()
        return _b64(payload) + "." + _b64(mac)

    def _read_cursor(self, cursor: Any, op: str, account: str, generation: int, total: int,
                     query_digest: str) -> int:
        if cursor is None:
            return 0
        if not isinstance(cursor, str) or not cursor or len(cursor) > MAX_CURSOR_CHARS or cursor.count(".") != 1:
            raise ValueError("malformed")
        p64, m64 = cursor.split(".")
        try:
            payload, mac = _unb64(p64), _unb64(m64)
        except ValueError as exc:
            raise ValueError("malformed") from exc
        want = hmac.new(self._cursor_key, _CURSOR_DOMAIN + payload, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, want):
            raise ValueError("not issued by this server")
        try:
            body = json.loads(payload.decode("utf-8"))
        except ValueError as exc:
            raise ValueError("malformed") from exc
        if not isinstance(body, dict) or set(body) != {"v", "op", "acct", "gen", "ps", "off", "q"}:
            raise ValueError("malformed")
        if body["v"] != CURSOR_VERSION or body["op"] != op or body["ps"] != PAGE_SIZE:
            raise ValueError("not a cursor for this operation")
        if body["acct"] != account:
            raise ValueError("not a cursor for this account")
        if body["gen"] != generation:
            raise ValueError("stale: the catalog changed, start again without a cursor")
        if body["q"] != query_digest:
            raise ValueError("not a cursor for this query")
        off = body["off"]
        if not isinstance(off, int) or isinstance(off, bool) or off <= 0 or off % PAGE_SIZE or off >= total:
            raise ValueError("offset out of range")
        return off

    # ------------------------------------------------------------------ calls
    def _tools_call(self, ctx, mid, params, *, principal=None, grant=None) -> dict:
        name = params.get("name")
        arguments = params.get("arguments", {})
        meta = params.get("_meta")
        if isinstance(meta, dict) and any(isinstance(k, str) and k.startswith(RESERVED_META_PREFIX) for k in meta):
            self.ledger.append("catalog_decision", request="tools/call", tool=_safe(name), decision="refused",
                               reason="routing_claim", **ctx)
            return _rpc_error(mid, -32602, "routing claims are not accepted")
        meta_wish = meta.get(TARGET_META_KEY) if (self.router and isinstance(meta, dict)) else None
        if name == "agentnode_search":
            return self._search(ctx, mid, arguments)
        if name == "agentnode_describe":
            return self._describe(ctx, mid, arguments)
        if name == "agentnode_run":
            allowed = {"tool", "arguments", "target"} if self.router else {"tool", "arguments"}
            if not isinstance(arguments, dict) or set(arguments) - allowed \
                    or not isinstance(arguments.get("tool"), str):
                return self._tool_error(ctx, mid, "agentnode_run", "bad_arguments",
                                        "agentnode_run needs {tool: string, arguments: object}")
            target = arguments["tool"]
            if target in META_TOOLS:
                return self._tool_error(ctx, mid, target, "meta_recursion", "meta-tools cannot be run")
            wish = arguments.get("target")
            if wish is not None and meta_wish is not None and wish != meta_wish:
                # two different wishes for one call: neither is chosen over the other
                self._tool_error(ctx, mid, target, "target_bad_wish", "refused: target_bad_wish")
                return _refusal_result(mid, "target_bad_wish")
            return self._execute(ctx, mid, target, arguments.get("arguments", {}), route="meta",
                                 principal=principal, wish=wish if wish is not None else meta_wish, grant=grant)
        return self._execute(ctx, mid, name, arguments, route="direct", principal=principal, wish=meta_wish,
                             grant=grant)

    def plan(self, principal, msg: dict) -> tuple[str, int, int] | None:
        """For the transport's scheduler: the (target id, concurrency, queue) this call would run on,
        or None when it will be answered without running anything (unknown tool, refusal, discovery).
        Advisory only: the call is resolved again, and judged again under the lock, when it runs."""
        if self.router is None or msg.get("method") != "tools/call":
            return None
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        name, args, meta = params.get("name"), params.get("arguments", {}), params.get("_meta")
        wish = meta.get(TARGET_META_KEY) if isinstance(meta, dict) else None
        if name == "agentnode_run" and isinstance(args, dict):
            name, wish = args.get("tool"), args.get("target", wish)
        if not isinstance(name, str) or name in META_TOOLS:
            return None
        try:
            entry = self.catalog.load().resolve(principal.principal_id, name)
            if entry is None:
                return None
            res = self.router.resolve(principal.principal_id, wish, entry)
        except Exception:                                      # noqa: BLE001 - the call itself will say why
            return None
        lim = res.target.limits
        return res.target_id, int(lim["concurrency"]), int(lim["queue"])

    def _projection(self, entry: Entry) -> dict[str, Any]:
        man = self.client.index.resolve(entry.capability_id, entry.version).manifest
        tool = next(t for t in man["tools"] if t["name"] == entry.export)
        perms = man.get("permissions") or {}
        registry = ((man.get("source") or {}).get("package") or {}).get("registry") or {}
        return {"tool": entry.tool, "description": tool.get("description", ""),
                "input_schema": tool.get("input_schema") or {"type": "object"},
                "capability_id": entry.capability_id, "version": entry.version,
                "tree_digest_prefix": entry.tree_digest[:19],
                "requested_permissions": {"network_domains": list((perms.get("network") or {}).get("allowed_domains") or []),
                                          "env_keys": list(perms.get("env_keys") or []),
                                          "code_execution": bool(perms.get("code_execution")),
                                          "user_approval": perms.get("user_approval", "required")},
                "signer_role": "importing_publisher",
                "upstream_signature_status": registry.get("upstream_signature_status", "not recorded")}

    def _search(self, ctx, mid, args) -> dict:
        if not isinstance(args, dict) or set(args) - {"query", "cursor"}:
            return self._tool_error(ctx, mid, "agentnode_search", "bad_arguments", "agentnode_search takes {query, cursor}")
        query = args.get("query", "")
        if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
            return self._tool_error(ctx, mid, "agentnode_search", "bad_arguments", "query must be a short string")
        q = " ".join(query.lower().split())
        qd = hashlib.sha256(q.encode("utf-8")).hexdigest()
        snap = self.catalog.load()
        hits = [self._projection(e) for e in snap.active_entries(ctx["account"])]
        if q:
            hits = [h for h in hits if q in (h["tool"] + " " + h["description"] + " " + h["capability_id"]).lower()]
        try:
            offset = self._read_cursor(args.get("cursor"), "search", ctx["account"], snap.generation, len(hits), qd)
        except ValueError as exc:
            self.ledger.append("catalog_decision", request="search", decision="refused", reason="invalid_cursor",
                               generation=snap.generation, query=digest_of(q), **ctx)
            return _rpc_error(mid, -32602, f"invalid cursor: {exc}")
        page = hits[offset:offset + PAGE_SIZE]
        out: dict[str, Any] = {"results": page, "generation": snap.generation}
        if offset + PAGE_SIZE < len(hits):
            out["nextCursor"] = self._cursor("search", ctx["account"], snap.generation, offset + PAGE_SIZE, qd)
        self.ledger.append("catalog_decision", request="search", decision="listed", generation=snap.generation,
                           query=digest_of(q), offset=offset, count=len(page), **ctx)
        return _tool_result(mid, out)

    def _describe(self, ctx, mid, args) -> dict:
        if not isinstance(args, dict) or set(args) != {"tool"} or not isinstance(args["tool"], str):
            return self._tool_error(ctx, mid, "agentnode_describe", "bad_arguments", "agentnode_describe takes {tool}")
        snap = self.catalog.load()
        entry = snap.resolve(ctx["account"], args["tool"])
        if entry is None:
            return self._tool_error(ctx, mid, "agentnode_describe", "unknown_tool", "unknown tool",
                                    generation=snap.generation)
        self.ledger.append("catalog_decision", request="describe", tool=entry.tool, decision="described",
                           generation=snap.generation, binding=entry.to_dict(), **ctx)
        projection = self._projection(entry)
        if self.router is not None:
            projection["targets"] = self._target_projection(ctx["account"], entry)
        return _tool_result(mid, projection)

    def _target_projection(self, account: str, entry: Entry) -> list[dict[str, Any]]:
        """Codex D9: only the account's own view -- id, type, status, eligibility with reason codes,
        assurance class and its expiry. Never an address, URI, instance, pair, raw property or count."""
        from agentnode_sdk.remote_mcp import target_router as R
        snap = self.router.register.load()
        default_id, _ = snap.default_for(account)
        out = []
        for t in snap.visible_targets(account):
            try:
                R._eligible(t, entry, snap.now, self.router.acks.load(), self.router._deployment_trust())
                eligible, reasons = True, []
            except TargetRefusal as refusal:
                eligible, reasons = False, [refusal.code]
            out.append({"target_id": t.target_id, "type": t.type, "status": t.effective_status(snap.now),
                        "eligible": eligible, "reasons": reasons, "default": t.target_id == default_id,
                        "assurance": t.assurance,
                        "attestation_expires_at": (t.attestation or {}).get("expires_at")})
        return out

    def nonce_for(self, account: str, entry: Entry, arguments: dict, target: dict | None = None) -> str:
        body = {"account": account, "tool": entry.tool, "capability_id": entry.capability_id,
                "version": entry.version, "manifest_digest": entry.manifest_digest,
                "artifact_digest": entry.artifact_digest, "tree_digest": entry.tree_digest,
                "export": entry.export, "arguments": arguments}
        if target is not None:
            # target-router D2: the approval belongs to exactly one target binding as well
            body["target"] = target
        return hashlib.sha256(_NONCE_DOMAIN + canonical(body)).hexdigest()

    def _execute(self, ctx, mid, name, arguments, *, route: str, principal=None, wish=None, grant=None) -> dict:
        """The ONE execution path for direct calls and ``agentnode_run``."""
        snap: Snapshot = self.catalog.load()
        entry = snap.resolve(ctx["account"], name) if isinstance(name, str) else None
        base = {"request": "tools/call", "route": route, "tool": _safe(name), "generation": snap.generation, **ctx}
        if entry is None:
            # unknown, unpublished and inactive names look identical (no catalog probing)
            self.ledger.append("catalog_decision", decision="refused", reason="unknown_tool", **base)
            return _rpc_error(mid, -32602, "unknown tool")
        if not isinstance(arguments, dict):
            return self._tool_error(ctx, mid, entry.tool, "bad_arguments", "arguments must be an object",
                                    route=route, generation=snap.generation)
        try:
            canonical(arguments)                     # NaN / Infinity / non-JSON refused
        except (TypeError, ValueError):
            return self._tool_error(ctx, mid, entry.tool, "bad_arguments", "arguments must be finite JSON",
                                    route=route, generation=snap.generation)
        base.update(binding=entry.to_dict(), arguments=digest_of(arguments))

        # ---- WHERE (target-router): one resolver, a wish is only a wish, no fallback
        resolution = None
        if self.router is not None:
            if self._settlement_pending:
                self.settle_earlier_dispatches()               # still unreadable: resolve() refuses (503)
            try:
                resolution = self.router.resolve(ctx["account"], wish, entry)
            except TargetRefusal as refusal:
                return self._target_refused(mid, base, refusal, grant)
            base.update(self._target_fields(resolution))

        nonce = self.nonce_for(ctx["account"], entry, arguments, resolution.binding if resolution else None)
        try:
            inv = self.client.prepare(entry.capability_id, entry.export, arguments, version=entry.version, nonce=nonce)
        except Exception as exc:  # noqa: BLE001
            self.ledger.append("catalog_decision", decision="refused", reason="prepare_failed",
                               detail=type(exc).__name__, **base)
            return _tool_error_result(mid, "refused: the capability could not be prepared")
        if inv.manifest_digest != entry.manifest_digest or inv.artifact_digest.get("value") != entry.artifact_digest:
            self.ledger.append("catalog_decision", decision="refused", reason="catalog_changed",
                               invocation_hash=inv.invocation_hash, **base)
            return _tool_error_result(mid, "refused: catalog_changed")
        base["invocation_hash"] = inv.invocation_hash
        if resolution is not None and self._is_indeterminate(inv.invocation_hash):
            # an earlier dispatch of this exact invocation has no settled outcome: nothing is sent
            # again until it is reconciled (Codex D8 point 10)
            self.ledger.append("catalog_decision", decision="refused", reason="dispatch_indeterminate", **base)
            return _refusal_result(mid, "dispatch_indeterminate")
        store = self.client.approval_store
        store.load_dir(self.approvals_dir)
        approval_id = _unspent_approval(store, inv.invocation_hash)
        if approval_id is None:
            req = self.client.approval_required(inv).to_dict()
            req["next_step"] = APPROVAL_COMMAND.format(stand=self.stand, invocation_hash=inv.invocation_hash,
                                                       audience=entry.capability_id)
            req["catalog_tool"] = entry.tool
            if resolution is not None:
                req["target"] = self._target_view(resolution)
            self.ledger.append("catalog_decision", decision="approval_required", **base)
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": f"approval required. Next step:\n  {req['next_step']}"}],
                "structuredContent": req}}

        worker = None
        if resolution is not None:
            # A fresh connection to the target BEFORE the approval can be consumed: an offline target,
            # a wrong identity or unusable trust is refused here and the approval survives.
            try:
                self.router.preflight(resolution.target)
            except TargetRefusal as refusal:
                return self._target_refused(mid, base, refusal, grant)
            worker = self.router.worker(resolution.target)

        self._barrier("before_admission")
        try:
            run_token, admitted_gen = self.catalog.admit(
                ctx["account"], entry,
                also=(lambda: self._admission_checks(ctx, principal, entry, resolution)) if resolution else None)
        except RunNotAdmitted as exc:
            # with the queue times, so the audit shows a call that WAITED and was then judged again
            waited = ({"queued_at": round(grant.queued_at, 3), "granted_at": round(grant.granted_at, 3)}
                      if grant is not None else {})
            self.ledger.append("catalog_decision", decision="refused", reason=exc.code, **waited, **base)
            return _refusal_result(mid, exc.code)
        dispatch_id = ("cap-" + hashlib.sha256((inv.invocation_hash + "|" + approval_id).encode("ascii")).hexdigest()[:32]
                       if resolution is not None else None)
        try:
            # recorded BEFORE dispatch: an unrecordable admission never reaches the worker
            timing = {}
            if grant is not None:
                timing = {"queued_at": round(grant.queued_at, 3), "granted_at": round(grant.granted_at, 3)}
            self.ledger.append("catalog_decision", decision="admitted", admitted_generation=admitted_gen,
                               dispatch_id=dispatch_id, intent_at=round(time.time(), 3), **timing, **base)
            self._barrier("after_admission")
            try:
                if resolution is not None:
                    expected = resolution.target.identity_uri

                    def proved(run_id, _w=worker, _expected=expected):
                        who = _w.who_ran(run_id)[1] if hasattr(_w, "who_ran") else ""
                        return ("mtls:" + who) if who and who == _expected else ""
                    res = self.client.submit(inv, approval_id, worker=worker, worker_identity=proved,
                                             run_id=dispatch_id)
                else:
                    res = self.client.submit(inv, approval_id)
            except ApprovalError as exc:
                self.ledger.record_terminal("catalog_terminal", key=inv.invocation_hash, decision="refused",
                                            reason="approval", detail=str(exc)[:200],
                                            admitted_generation=admitted_gen, dispatch_id=dispatch_id, **base)
                return _tool_error_result(mid, f"ApprovalError: {exc}")
            except Exception as exc:  # noqa: BLE001 -- a worker/execution failure, recorded and surfaced
                if resolution is not None and _transport_lost(exc):
                    return self._reconcile(mid, inv, worker, dispatch_id, admitted_gen, base, resolution)
                self.ledger.record_terminal("catalog_terminal", key=inv.invocation_hash, decision="error",
                                            reason=type(exc).__name__, admitted_generation=admitted_gen,
                                            dispatch_id=dispatch_id, **base)
                return _tool_error_result(mid, f"{type(exc).__name__}: {exc}")
            self.ledger.record_terminal(
                "catalog_terminal", key=inv.invocation_hash, decision="ran", admitted_generation=admitted_gen,
                capability_audit={"ledger_id": res.audit_entry.ledger_id, "seq": res.audit_entry.seq},
                terminal_status="ok" if res.exec_result.ok else "error", dispatch_id=dispatch_id,
                finished_at=round(time.time(), 3), **base)
        finally:
            self.catalog.finish(run_token)
        call = res.exec_result.result.get("call") or {}
        text = ""
        if isinstance(call, dict):
            for item in call.get("content") or []:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    break
        if not text and not res.exec_result.ok:
            # a run that produced no tool result (timeout, crash): say why instead of an empty error
            text = str(res.exec_result.result.get("error") or "the run failed")
        structured = {"call": call, "invocation_hash": inv.invocation_hash,
                      "audit_seq": res.audit_entry.seq,
                      "achieved_isolation": res.exec_result.achieved_isolation,
                      "catalog": {"tool": entry.tool, "generation": admitted_gen}}
        if resolution is not None:
            structured["target"] = self._target_view(resolution)
            structured["dispatch_id"] = dispatch_id
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "isError": not res.exec_result.ok,
            "content": [{"type": "text", "text": text}],
            "structuredContent": structured}}

    # ------------------------------------------------------------------ target helpers
    @staticmethod
    def _target_fields(resolution) -> dict[str, Any]:
        t = resolution.target
        return {"target_id": t.target_id, "target_type": t.type, "selection_reason": resolution.selection_reason,
                "target_generation": t.facts_generation, "endpoint_digest": t.endpoint_digest,
                "properties_digest": t.properties_digest, "requirements_digest": resolution.requirements_digest,
                "assurance": t.assurance, "attestation_expires_at": (t.attestation or {}).get("expires_at"),
                "worker_identity_uri": t.identity_uri}

    @staticmethod
    def _target_view(resolution) -> dict[str, Any]:
        t = resolution.target
        view = {"target_id": t.target_id, "type": t.type, "assurance": t.assurance,
                "selection_reason": resolution.selection_reason}
        if t.type == "customer":
            view["note"] = CUSTOMER_LABEL
        return view

    def _target_refused(self, mid, base: dict, refusal: TargetRefusal, grant=None) -> dict:
        fields = dict(base)
        if grant is not None:                                  # it waited in the queue, then was judged
            fields.update(queued_at=round(grant.queued_at, 3), granted_at=round(grant.granted_at, 3))
        if refusal.target_id:
            fields["target_id"] = refusal.target_id
        if refusal.selection_reason:
            fields["selection_reason"] = refusal.selection_reason
        self.ledger.append("catalog_decision", decision="refused", reason=refusal.code, **fields)
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "isError": True, "content": [{"type": "text", "text": f"refused: {refusal.code}"}],
            "structuredContent": {"status": "refused", "code": refusal.code}}}

    def _admission_checks(self, ctx, principal, entry, resolution) -> None:
        """Under the catalog lock, right before the dispatch intent (T13): the account's live
        authority, the kill switch and the exact target binding. Unreadable state refuses."""
        if self.authority is not None:
            reason = self.authority(principal) if principal is not None else "authority_unverifiable"
            if reason:
                raise RunNotAdmitted("authority_changed")
        if self.kill_switch is not None:
            try:
                stopped = self.kill_switch()
            except Exception:                                  # noqa: BLE001 - unreadable is stopped
                stopped = "unreadable"
            if stopped:
                raise RunNotAdmitted("kill_switch")
        try:
            self.router.recheck(ctx["account"], resolution, entry)
        except TargetRefusal as refusal:
            raise RunNotAdmitted(refusal.code) from refusal

    def _is_indeterminate(self, invocation_hash: str) -> bool:
        return os.path.exists(os.path.join(self._indeterminate_dir, invocation_hash + ".json"))

    def _mark_indeterminate(self, invocation_hash: str, record: dict) -> None:
        os.makedirs(self._indeterminate_dir, exist_ok=True)
        path = os.path.join(self._indeterminate_dir, invocation_hash + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())

    def _reconcile(self, mid, inv, worker, dispatch_id, admitted_gen, base, resolution) -> dict:
        """The connection carrying the run was lost: ask the SAME target what became of THIS dispatch
        id. Never resent, never sent elsewhere. Exactly one terminal event either way."""
        try:
            known = worker.result(dispatch_id, wait=self.reconcile_wait)
        except Exception as exc:                               # noqa: BLE001
            known = None
            detail = type(exc).__name__
        else:
            detail = ""
        if known is not None and known.known and known.never_ran:
            self.ledger.record_terminal("catalog_terminal", key=inv.invocation_hash, decision="not_started",
                                        reason="transport_lost", reconciliation="never_ran",
                                        admitted_generation=admitted_gen, dispatch_id=dispatch_id, **base)
            return _refusal_result(mid, "not_started", "refused: not_started (the worker never ran it)")
        if known is not None and known.known and known.outcome is not None:
            outcome = known.outcome if isinstance(known.outcome, dict) else {}
            from agentnode_sdk.capability.tree_execution import _parse_result
            result = _parse_result(str(outcome.get("stdout") or ""))
            self.ledger.record_terminal("catalog_terminal", key=inv.invocation_hash, decision="ran",
                                        reason="transport_lost", reconciliation="outcome_recovered",
                                        outcome_digest=digest_of(outcome), terminal_status="ok" if result.get("ok") else "error",
                                        admitted_generation=admitted_gen, dispatch_id=dispatch_id, **base)
            call = result.get("call") or {}
            text = next((i.get("text", "") for i in (call.get("content") or []) if i.get("type") == "text"), "")
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "isError": not result.get("ok"), "content": [{"type": "text", "text": text or str(result.get("error") or "")}],
                "structuredContent": {"call": call, "invocation_hash": inv.invocation_hash, "reconciled": True,
                                      "target": self._target_view(resolution), "dispatch_id": dispatch_id}}}
        self._mark_indeterminate(inv.invocation_hash, {"dispatch_id": dispatch_id, "target_id": resolution.target_id,
                                                       "at": round(time.time(), 3)})
        self.ledger.record_terminal("catalog_terminal", key=inv.invocation_hash, decision="indeterminate",
                                    reason="transport_lost", reconciliation=detail or "unknown",
                                    admitted_generation=admitted_gen, dispatch_id=dispatch_id, **base)
        return _refusal_result(mid, "dispatch_indeterminate", "refused: dispatch_indeterminate (reconciliation required)")

    def settle_earlier_dispatches(self) -> list[dict[str, Any]] | None:
        """Run `recover_after_restart` once per process; if the register cannot be read yet, stay
        pending (every target run is refused meanwhile) and try again before the next target run."""
        if not self._settlement_pending:
            return []
        with self._settlement_lock:
            if not self._settlement_pending:
                return []
            try:
                settled = self.recover_after_restart()
            except TargetsUnavailable:
                return None
            self._settlement_pending = False
            return settled

    def recover_after_restart(self) -> list[dict[str, Any]]:
        """After a control-plane restart (target-router R2, Codex D8 "recovery from a crash between
        steps is by dispatch_id"): every dispatch that an EARLIER process admitted and never ended is
        settled from ITS target's journal by its dispatch id -- never resent, never sent elsewhere.

        * the worker never ran it: `not_started`; its approval is spent here if the crash came before
          the approval was, so the same dispatch id can never be admitted (and terminated) twice;
        * the worker has its outcome: `ran`, reconciliation `outcome_recovered`;
        * anything else (no answer, still running, unknown): `indeterminate`, and the invocation is
          blocked until `targets_cli reconcile-dispatch` settles it.
        A dispatch admitted by THIS process (intent after its start) is in flight and is not touched."""
        if self.router is None:
            return []
        rows = [e["body"] for e in self.ledger._entries()]
        if self.ledger.recovery.is_dir():                      # terminals waiting to be re-ingested count
            rows += [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.ledger.recovery.glob("*.json"))]
        ended = {b.get("dispatch_id") for b in rows if b.get("kind") == "catalog_terminal" and b.get("dispatch_id")}
        open_ = [b for b in rows if b.get("decision") == "admitted" and b.get("dispatch_id")
                 and b["dispatch_id"] not in ended and float(b.get("intent_at") or 0) < self._started_at]
        settled = []
        store = self.client.approval_store
        store.load_dir(self.approvals_dir)
        snap = self.router.register.load()
        for b in open_:
            d, h = b["dispatch_id"], b["invocation_hash"]
            base = {k: b[k] for k in b if k not in ("kind", "recorded_at", "decision", "admitted_generation",
                                                    "dispatch_id", "intent_at", "queued_at", "granted_at",
                                                    "recovered")}
            target = snap.target(b.get("target_id", ""))
            known, detail = None, "target_unknown"
            if target is not None:
                try:
                    known, detail = self.router.worker(target).result(d, wait=self.reconcile_wait), ""
                except Exception as exc:                       # noqa: BLE001 - not answered: indeterminate
                    detail = type(exc).__name__
            common = dict(reason="control_restart", admitted_generation=b.get("admitted_generation"),
                          dispatch_id=d, **base)
            if known is not None and known.known and known.never_ran:
                for aid in [a for a, ap in store._issued.items() if ap.invocation_hash == h]:
                    if "cap-" + hashlib.sha256((h + "|" + aid).encode("ascii")).hexdigest()[:32] == d \
                            and not store._already_consumed_durably(aid):
                        store._mark_consumed_durably(aid)     # this dispatch id is never admitted again
                self.ledger.record_terminal("catalog_terminal", key=h, decision="not_started",
                                            reconciliation="never_ran", **common)
                settled.append({"dispatch_id": d, "decision": "not_started"})
            elif known is not None and known.known and known.outcome is not None:
                outcome = known.outcome if isinstance(known.outcome, dict) else {}
                self.ledger.record_terminal("catalog_terminal", key=h, decision="ran",
                                            reconciliation="outcome_recovered", outcome_digest=digest_of(outcome),
                                            **common)
                settled.append({"dispatch_id": d, "decision": "ran"})
            else:
                self._mark_indeterminate(h, {"dispatch_id": d, "target_id": b.get("target_id"),
                                             "at": round(time.time(), 3)})
                self.ledger.record_terminal("catalog_terminal", key=h, decision="indeterminate",
                                            reconciliation=detail or "unknown", **common)
                settled.append({"dispatch_id": d, "decision": "indeterminate"})
        return settled

    def _tool_error(self, ctx, mid, tool, reason, text, **extra) -> dict:
        self.ledger.append("catalog_decision", request="tools/call", tool=_safe(tool), decision="refused",
                           reason=reason, **extra, **ctx)
        return _tool_error_result(mid, text)


def _unspent_approval(store, invocation_hash: str) -> str | None:
    """An approval for this invocation that is not spent -- on DISK as well: one spent before this
    process started is not offered again (a replay after a restart would otherwise be admitted under
    the dispatch id of the run that spent it, and give that dispatch a second terminal)."""
    while True:
        aid = store.find_unconsumed_for(invocation_hash)
        if aid is None or not store._already_consumed_durably(aid):
            return aid
        store._consumed.add(aid)


def _transport_lost(exc: BaseException) -> bool:
    """Did the run's connection fail (the outcome is then ambiguous), rather than the run itself?"""
    from agentnode_sdk.worker import WorkerUnreachable
    seen = exc
    while seen is not None:
        if isinstance(seen, WorkerUnreachable):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _safe(name: Any) -> str:
    return name[:80] if isinstance(name, str) else repr(type(name).__name__)


def _rpc_error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _tool_result(mid, structured: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": {
        "isError": False, "content": [{"type": "text", "text": json.dumps(structured, sort_keys=True)}],
        "structuredContent": structured}}


def _refusal_result(mid, code: str, text: str = "") -> dict:
    """A target-router refusal: the text a person reads and the stable code a client branches on."""
    out = _tool_error_result(mid, text or f"refused: {code}")
    out["result"]["structuredContent"] = {"status": "refused", "code": code}
    return out


def _tool_error_result(mid, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": text}]}}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not text or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in text):
        raise ValueError("bad base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cursor_key_from(server_key: bytes) -> bytes:
    """A dedicated, domain-separated cursor key derived from the stand server secret."""
    return hmac.new(server_key, b"agentnode.catalog.v1/cursor-key", hashlib.sha256).digest()
