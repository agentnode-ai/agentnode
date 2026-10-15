"""Model-2 execution over the accepted, UNMODIFIED Worker/Job path (S7), and S8.

The capability's tool call runs by composition over the existing worker ``run``
verb: the imported MCP server is delivered as the job's artifact (base64 on
stdin, via the gateway's own ``challenge.bootstrap`` / ``on_stdin``), and a small
per-run program -- built from the tested ``_handshake`` exchange -- runs the
server inside the worker's sandbox and performs initialize -> initialized ->
tools/call, printing one result line. The worker protocol is untouched.

There is NO local fallback. If there is no worker, or the worker cannot isolate,
or the worker refuses/loses the job, the capability REFUSES -- it never runs the
MCP server as a local capability subprocess.

Only ``agentnode_executed_mcp`` (model 2) executes. Models 1 and 3 raise
``NotImplementedModel`` (a stable NOT_IMPLEMENTED) and never substitute model 2.
"""
from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentnode_sdk.capability.contract import Invocation
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


class ExecutionError(Exception):
    """A refusal at execution: a bad worker, a worker that cannot isolate, or a lost/failed job."""


class NotImplementedModel(ExecutionError):
    """Execution model declared but not implemented in this slice. Stable, never substituted."""

    status = "NOT_IMPLEMENTED"

    def __init__(self, model: str):
        super().__init__(f"execution model {model!r} is NOT_IMPLEMENTED in slice 1")
        self.model = model


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    result: dict[str, Any]
    achieved_isolation: dict[str, Any]
    outcome_stdout: str
    outcome_stderr: str
    exit_code: int | None
    run_id: str


def _handshake_source_without_main() -> str:
    """The tested _handshake module source, minus its __main__ guard, to embed in the run program."""
    src = Path(__file__).with_name("_handshake.py").read_text(encoding="utf-8")
    marker = "\nif __name__ =="
    return src.split(marker)[0]


def build_run_program(server_src: bytes, tool: str, arguments: dict[str, Any]) -> bytes:
    """A self-contained python program that runs *server_src* in-sandbox and calls *tool*.

    Reuses the tested ``run_exchange`` (embedded), spawns the server with the same
    interpreter that runs this program, performs the handshake + one tools/call,
    and prints exactly one ``CAPRESULT <json>`` line.
    """
    head = _handshake_source_without_main()
    tail = (
        "\n\n# --- per-run capability driver (model 2, in-sandbox) ---\n"
        "import base64 as _b64, tempfile as _tmp, os as _os, sys as _sys, json as _json\n"
        f"_SERVER = _b64.b64decode({base64.b64encode(server_src).decode('ascii')!r})\n"
        f"_TOOL = {tool!r}\n"
        f"_ARGS = _json.loads({json.dumps(json.dumps(arguments))})\n"
        "_d = _tmp.mkdtemp(); _p = _os.path.join(_d, 'server.py')\n"
        "open(_p, 'wb').write(_SERVER)\n"
        "try:\n"
        "    _res = run_exchange([_sys.executable, _p], call={'name': _TOOL, 'arguments': _ARGS}, timeout=30.0)\n"
        "    print('" + _RESULT_SENTINEL + "' + _json.dumps({'ok': True, 'protocol': _res.get('protocol'),\n"
        "        'server_info': _res.get('server_info'), 'call': _res.get('call')}))\n"
        "except Exception as _e:\n"
        "    print('" + _RESULT_SENTINEL + "' + _json.dumps({'ok': False,\n"
        "        'error': type(_e).__name__ + ': ' + str(_e)}))\n"
    )
    return (head + tail).encode("utf-8")


def _build_job(invocation: Invocation, server_src: bytes, limits: Limits) -> Job:
    program = build_run_program(server_src, invocation.tool_name, invocation.arguments)
    payload = base64.b64encode(program).decode("ascii")
    # Reuse the accepted delivery: artifact (base64) on stdin behind two bootstrap lines, and the
    # gateway's own bootstrap command. The two lines are ignored by our program; they exist so the
    # bootstrap's two readline()s do not eat the payload.
    stdin = ch.on_stdin("", "", payload)
    return Job(
        run_id="cap-" + uuid.uuid4().hex,
        container_name="agentnode-cap-" + uuid.uuid4().hex[:12],
        command=tuple(ch.bootstrap(command_was_given=False)),
        artifact=program,
        stdin=stdin,
        network="none",               # this credential-free tool needs no route out
        allowed_domains=(),
        limits=limits,
    )


def execute(
    invocation: Invocation,
    index: LocalIndex,
    worker: Any,
    *,
    effective_grant: dict[str, Any],
    limits: Limits | None = None,
) -> ExecResult:
    """Run the capability's tool call on *worker*. Refuse rather than ever run locally.

    *worker* is any accepted ``Worker`` (e.g. ``LocalWorker`` over a real podman
    backend). This function builds a ``Job`` and calls ``worker.run(job)``; it has
    no code path that runs the MCP server outside the worker.
    """
    if invocation.execution_model != "agentnode_executed_mcp":
        raise NotImplementedModel(invocation.execution_model)

    if worker is None:
        raise ExecutionError("no worker: a capability run never falls back to local execution")

    # The worker must be able to isolate; model-2 isolation is only what it actually reports.
    iso = worker.can_it_isolate()
    if not getattr(iso, "available", False):
        raise ExecutionError(
            f"worker cannot isolate (backend={getattr(iso, 'backend', 'none')!r}); refusing "
            f"model-2 execution rather than running without the sandbox")

    resolved = index.resolve(invocation.capability_id, invocation.capability_version)
    server_sha = resolved.manifest["source"]["package"]["sha256"]
    server_src = index.get_blob(server_sha)

    job = _build_job(invocation, server_src, limits or Limits())
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


def _parse_result(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith(_RESULT_SENTINEL):
            try:
                return json.loads(line[len(_RESULT_SENTINEL):])
            except ValueError:
                break
    return {"ok": False, "error": "no CAPRESULT line in worker stdout"}
