"""Model-2 execution of a REAL multi-file MCP server over the UNMODIFIED Worker/Job path.

Slice 2, mechanism (b). The imported server's whole byte-pinned tree is delivered as the
job's artifact (the per-run driver carries the gzip-compressed deterministic tar inside
itself); a small sealed driver -- built from the tested slice-1 ``_handshake`` plus
``tree_pack`` -- materializes the tree in an in-sandbox temp dir, RE-VERIFIES it against the
pinned tree-digest, then runs the pinned entry with the image's own interpreter (``node`` or
``python3``), performs initialize -> initialized -> tools/call and prints one result line.

This runs via ``worker.run(Job)`` exactly as slice 1 did: the frozen ``Job``/``Outcome``,
worker, gateway transport, sandbox backend and base image are byte-untouched. There is NO
mount, NO volume, NO alternate execution surface, and NO local fallback. If the worker is
unavailable, cannot isolate, or the tree fails re-verification, the capability REFUSES.
"""
from __future__ import annotations

import base64
import gzip
import json
import uuid
from pathlib import Path
from typing import Any

from agentnode_sdk.capability.contract import Invocation
from agentnode_sdk.capability.execution import (
    ExecResult,
    ExecutionError,
    NotImplementedModel,
    _parse_result,
)
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.gateway import challenge as ch
from agentnode_sdk.worker import (
    Job,
    JobFailed,
    Limits,
    NoRuntimeThere,
    WorkerUnreachable,
)

_RESULT_SENTINEL = "CAPRESULT "


def _module_source_without_main(module_file: str) -> str:
    """The module source, minus its ``__main__`` guard and any ``from __future__`` line.

    Future-import lines are stripped because two embedded modules are concatenated into one
    program; a ``from __future__`` that is no longer at the top of the file is a SyntaxError,
    and none of the embedded runtime behaviour depends on them.
    """
    src = Path(module_file).read_text(encoding="utf-8").split("\nif __name__ ==")[0]
    return "\n".join(line for line in src.splitlines()
                     if not line.startswith("from __future__ import"))


def build_tree_run_program(tar_bytes: bytes, *, interpreter: str, entry: str,
                           expected_tree_digest: str, tool: str, arguments: dict[str, Any],
                           timeout: float = 90.0) -> bytes:
    """A self-contained python program that materializes + verifies *tar_bytes* in-sandbox and runs the server.

    Embeds the tested ``_handshake`` and ``tree_pack`` (so extraction is guarded and the
    digest definition is the one used at import), gunzips+extracts the tree, refuses on a
    tree-digest mismatch (the delivered bytes are not the pinned bytes), then performs one
    ``tools/call`` with the image's interpreter and prints exactly one ``CAPRESULT`` line.
    """
    from agentnode_sdk.capability import _handshake, tree_pack

    head = (
        _module_source_without_main(_handshake.__file__)
        + "\n\n# --- embedded tree_pack ---\n"
        + _module_source_without_main(tree_pack.__file__)
    )
    gz = gzip.compress(tar_bytes, 9)
    tail = (
        "\n\n# --- per-run capability driver (model 2, in-sandbox, multi-file tree) ---\n"
        "import base64 as _b64, gzip as _gz, tempfile as _tmp, os as _os, sys as _sys, json as _json\n"
        f"_GZTAR = _b64.b64decode({base64.b64encode(gz).decode('ascii')!r})\n"
        f"_EXPECT = {expected_tree_digest!r}\n"
        f"_INTERP = {interpreter!r}\n"
        f"_ENTRY = {entry!r}\n"
        f"_TOOL = {tool!r}\n"
        f"_ARGS = _json.loads({json.dumps(json.dumps(arguments))})\n"
        f"_TIMEOUT = {float(timeout)!r}\n"
        "def _emit(obj):\n"
        "    print('" + _RESULT_SENTINEL + "' + _json.dumps(obj))\n"
        "_d = _tmp.mkdtemp()\n"
        "try:\n"
        "    _tar = _gz.decompress(_GZTAR)\n"
        "    safe_extract(_tar, _d)\n"
        "    _got = tree_digest_of_dir(_d)\n"
        "    if _got != _EXPECT:\n"
        "        _emit({'ok': False, 'error': 'artifact tree-digest mismatch: delivered bytes are not the pinned tree'})\n"
        "    else:\n"
        "        _cmd = [(_sys.executable if _INTERP.startswith('python') else _INTERP), _os.path.join(_d, _ENTRY)]\n"
        "        _res = run_exchange(_cmd, call={'name': _TOOL, 'arguments': _ARGS}, timeout=_TIMEOUT)\n"
        "        _emit({'ok': True, 'protocol': _res.get('protocol'), 'server_info': _res.get('server_info'), 'call': _res.get('call')})\n"
        "except Exception as _e:\n"
        "    _emit({'ok': False, 'error': type(_e).__name__ + ': ' + str(_e)})\n"
    )
    return (head + tail).encode("utf-8")


def _build_job(invocation: Invocation, tar_bytes: bytes, *, interpreter: str, entry: str,
               expected_tree_digest: str, limits: Limits) -> Job:
    program = build_tree_run_program(
        tar_bytes, interpreter=interpreter, entry=entry,
        expected_tree_digest=expected_tree_digest,
        tool=invocation.tool_name, arguments=invocation.arguments,
        timeout=max(30.0, float(limits.wall_clock_s) - 10.0),
    )
    payload = base64.b64encode(program).decode("ascii")
    stdin = ch.on_stdin("", "", payload)
    return Job(
        run_id="cap-" + uuid.uuid4().hex,
        container_name="agentnode-cap-" + uuid.uuid4().hex[:12],
        command=tuple(ch.bootstrap(command_was_given=False)),
        artifact=program,
        stdin=stdin,
        network="none",               # this credential-free server needs no route out
        allowed_domains=(),
        limits=limits,
    )


def execute_tree(
    invocation: Invocation,
    index: LocalIndex,
    worker: Any,
    *,
    effective_grant: dict[str, Any],
    limits: Limits | None = None,
) -> ExecResult:
    """Run the real multi-file capability's tool call on *worker*. Refuse rather than run locally.

    Reuses the slice-1 ``ExecResult`` and refusal semantics; the only difference from
    ``execution.execute`` is a multi-file (tree) payload with an in-sandbox re-verification
    of the pinned tree-digest. No local execution path exists here.
    """
    if invocation.execution_model != "agentnode_executed_mcp":
        raise NotImplementedModel(invocation.execution_model)

    invocation.assert_consistent()

    if worker is None:
        raise ExecutionError("no worker: a capability run never falls back to local execution")

    iso = worker.can_it_isolate()
    if not getattr(iso, "available", False):
        raise ExecutionError(
            f"worker cannot isolate (backend={getattr(iso, 'backend', 'none')!r}); refusing "
            f"model-2 execution rather than running without the sandbox")

    # This slice runs a credential-free, network-free server. A declared egress is NOT
    # silently honoured over the Job path here -- refuse rather than run a weakened call.
    domains = set((effective_grant.get("network") or {}).get("allowed_domains") or [])
    if domains:
        raise ExecutionError(
            "this capability declares network egress, which slice-2 (b) execution does not "
            "provide over the Job path; refusing rather than running a weakened call")

    resolved = index.resolve(invocation.capability_id, invocation.capability_version)
    pkg = resolved.manifest["source"]["package"]
    if pkg.get("kind") != "vendored-tree":
        raise ExecutionError(f"tree execution requires a vendored-tree package, got {pkg.get('kind')!r}")
    tar_bytes = index.get_blob(pkg["sha256"])

    lim = limits or Limits(memory_mb=1024, wall_clock_s=120, storage_mb=512)
    job = _build_job(invocation, tar_bytes, interpreter=pkg["interpreter"], entry=pkg["entry"],
                     expected_tree_digest=pkg["tree_digest"], limits=lim)
    try:
        outcome = worker.run(job)
    except (WorkerUnreachable, NoRuntimeThere) as exc:
        raise ExecutionError(f"worker unavailable; refusing (no local fallback): {exc}") from exc
    except JobFailed as exc:
        raise ExecutionError(f"job failed on the worker: {exc}") from exc

    result = _parse_result(outcome.stdout or "")
    achieved = {
        "backend": getattr(iso, "backend", "none"),
        "available": bool(getattr(iso, "available", False)),
        "source": "worker.can_it_isolate() reported for this run",
        "network": job.network,
        "note": "achieved isolation is what the worker reported/enforced for this run; not a "
                "publisher-asserted guarantee",
    }
    return ExecResult(
        ok=bool(result.get("ok")),
        result=result,
        achieved_isolation=achieved,
        outcome_stdout=outcome.stdout or "",
        outcome_stderr=outcome.stderr or "",
        exit_code=outcome.exit_code,
        run_id=job.run_id,
    )
