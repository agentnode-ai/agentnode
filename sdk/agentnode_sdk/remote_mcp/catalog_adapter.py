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
from typing import Any, Callable

from agentnode_sdk.capability.approval import ApprovalError
from agentnode_sdk.capability.usability import UsabilityClient
from agentnode_sdk.remote_mcp.catalog import (META_TOOLS, Catalog, Entry, RunNotAdmitted, Snapshot,
                                              canonical)
from agentnode_sdk.remote_mcp.catalog_audit import CatalogLedger, digest_of

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


class CatalogMCPAdapter:
    #: the transport passes the authenticated principal to ``handle_for`` (never ``handle``)
    account_aware = True

    def __init__(self, *, catalog: Catalog, client: UsabilityClient, ledger: CatalogLedger,
                 approvals_dir: str, stand: str, cursor_key: bytes,
                 barrier: Callable[[str], None] | None = None):
        self.catalog = catalog
        self.client = client
        self.ledger = ledger
        self.approvals_dir = approvals_dir
        self.stand = stand
        self._cursor_key = cursor_key
        self._barrier = barrier or (lambda name: None)

    # ------------------------------------------------------------------ transport surface
    def handle(self, msg):  # pragma: no cover - the transport never calls it for this adapter
        raise RuntimeError("the catalog adapter answers only authenticated requests (handle_for)")

    @staticmethod
    def is_discovery_call(msg: dict) -> bool:
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        return msg.get("method") == "tools/call" and params.get("name") in ("agentnode_search", "agentnode_describe")

    def handle_for(self, principal, msg: dict, *, era: str = "modern") -> dict | None:
        mid = msg.get("id")
        method = msg.get("method")
        ctx = {"account": principal.principal_id, "token_id": principal.token_id, "era": era}
        if method == "tools/list":
            return self._tools_list(ctx, mid, msg.get("params") if isinstance(msg.get("params"), dict) else {})
        if method == "tools/call":
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            return self._tools_call(ctx, mid, params)
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
        items: list[dict[str, Any]] = [{"name": n, **META_SCHEMAS[n]} for n in META_TOOLS]
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
    def _tools_call(self, ctx, mid, params) -> dict:
        name = params.get("name")
        arguments = params.get("arguments", {})
        meta = params.get("_meta")
        if isinstance(meta, dict) and any(isinstance(k, str) and k.startswith(RESERVED_META_PREFIX) for k in meta):
            self.ledger.append("catalog_decision", request="tools/call", tool=_safe(name), decision="refused",
                               reason="routing_claim", **ctx)
            return _rpc_error(mid, -32602, "routing claims are not accepted")
        if name == "agentnode_search":
            return self._search(ctx, mid, arguments)
        if name == "agentnode_describe":
            return self._describe(ctx, mid, arguments)
        if name == "agentnode_run":
            if not isinstance(arguments, dict) or set(arguments) - {"tool", "arguments"} \
                    or not isinstance(arguments.get("tool"), str):
                return self._tool_error(ctx, mid, "agentnode_run", "bad_arguments",
                                        "agentnode_run needs {tool: string, arguments: object}")
            target = arguments["tool"]
            if target in META_TOOLS:
                return self._tool_error(ctx, mid, target, "meta_recursion", "meta-tools cannot be run")
            return self._execute(ctx, mid, target, arguments.get("arguments", {}), via="meta")
        return self._execute(ctx, mid, name, arguments, via="direct")

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
        return _tool_result(mid, self._projection(entry))

    def nonce_for(self, account: str, entry: Entry, arguments: dict) -> str:
        return hashlib.sha256(_NONCE_DOMAIN + canonical({
            "account": account, "tool": entry.tool, "capability_id": entry.capability_id,
            "version": entry.version, "manifest_digest": entry.manifest_digest,
            "artifact_digest": entry.artifact_digest, "tree_digest": entry.tree_digest,
            "export": entry.export, "arguments": arguments})).hexdigest()

    def _execute(self, ctx, mid, name, arguments, *, via: str) -> dict:
        """The ONE execution path for direct calls and ``agentnode_run``."""
        snap: Snapshot = self.catalog.load()
        entry = snap.resolve(ctx["account"], name) if isinstance(name, str) else None
        base = {"request": "tools/call", "via": via, "tool": _safe(name), "generation": snap.generation, **ctx}
        if entry is None:
            # unknown, unpublished and inactive names look identical (no catalog probing)
            self.ledger.append("catalog_decision", decision="refused", reason="unknown_tool", **base)
            return _rpc_error(mid, -32602, "unknown tool")
        if not isinstance(arguments, dict):
            return self._tool_error(ctx, mid, entry.tool, "bad_arguments", "arguments must be an object",
                                    via=via, generation=snap.generation)
        try:
            canonical(arguments)                     # NaN / Infinity / non-JSON refused
        except (TypeError, ValueError):
            return self._tool_error(ctx, mid, entry.tool, "bad_arguments", "arguments must be finite JSON",
                                    via=via, generation=snap.generation)
        base.update(binding=entry.to_dict(), arguments=digest_of(arguments))
        nonce = self.nonce_for(ctx["account"], entry, arguments)
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
        store = self.client.approval_store
        store.load_dir(self.approvals_dir)
        approval_id = store.find_unconsumed_for(inv.invocation_hash)
        if approval_id is None:
            req = self.client.approval_required(inv).to_dict()
            req["next_step"] = APPROVAL_COMMAND.format(stand=self.stand, invocation_hash=inv.invocation_hash,
                                                       audience=entry.capability_id)
            req["catalog_tool"] = entry.tool
            self.ledger.append("catalog_decision", decision="approval_required", **base)
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": f"approval required. Next step:\n  {req['next_step']}"}],
                "structuredContent": req}}
        self._barrier("before_admission")
        try:
            run_token, admitted_gen = self.catalog.admit(ctx["account"], entry)
        except RunNotAdmitted as exc:
            self.ledger.append("catalog_decision", decision="refused", reason=exc.code, **base)
            return _tool_error_result(mid, f"refused: {exc.code}")
        try:
            # recorded BEFORE dispatch: an unrecordable admission never reaches the worker
            self.ledger.append("catalog_decision", decision="admitted", admitted_generation=admitted_gen, **base)
            self._barrier("after_admission")
            try:
                res = self.client.submit(inv, approval_id)
            except ApprovalError as exc:
                self.ledger.record_terminal("catalog_terminal", key=inv.invocation_hash, decision="refused",
                                            reason="approval", detail=str(exc)[:200],
                                            admitted_generation=admitted_gen, **base)
                return _tool_error_result(mid, f"ApprovalError: {exc}")
            except Exception as exc:  # noqa: BLE001 -- a worker/execution failure, recorded and surfaced
                self.ledger.record_terminal("catalog_terminal", key=inv.invocation_hash, decision="error",
                                            reason=type(exc).__name__, admitted_generation=admitted_gen, **base)
                return _tool_error_result(mid, f"{type(exc).__name__}: {exc}")
            self.ledger.record_terminal(
                "catalog_terminal", key=inv.invocation_hash, decision="ran", admitted_generation=admitted_gen,
                capability_audit={"ledger_id": res.audit_entry.ledger_id, "seq": res.audit_entry.seq},
                terminal_status="ok" if res.exec_result.ok else "error", **base)
        finally:
            self.catalog.finish(run_token)
        call = res.exec_result.result.get("call") or {}
        text = ""
        if isinstance(call, dict):
            for item in call.get("content") or []:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    break
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "isError": not res.exec_result.ok,
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"call": call, "invocation_hash": inv.invocation_hash,
                                  "audit_seq": res.audit_entry.seq,
                                  "achieved_isolation": res.exec_result.achieved_isolation,
                                  "catalog": {"tool": entry.tool, "generation": admitted_gen}}}}

    def _tool_error(self, ctx, mid, tool, reason, text, **extra) -> dict:
        self.ledger.append("catalog_decision", request="tools/call", tool=_safe(tool), decision="refused",
                           reason=reason, **extra, **ctx)
        return _tool_error_result(mid, text)


def _safe(name: Any) -> str:
    return name[:80] if isinstance(name, str) else repr(type(name).__name__)


def _rpc_error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _tool_result(mid, structured: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": {
        "isError": False, "content": [{"type": "text", "text": json.dumps(structured, sort_keys=True)}],
        "structuredContent": structured}}


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
