"""SDK-free in-container wrapper for a sandboxed agent (Sprint B1).

Runs INSIDE the sandbox via ``python -c WRAPPER_SOURCE``. It imports the agent's
installed entrypoint from the read-only ``/pack`` volume (``PYTHONPATH=/pack``)
and speaks the agent RPC protocol over the process's real stdin/stdout, while
redirecting the agent's own stdout/stderr to a capture buffer so they cannot
corrupt the control channel. Bidirectional production form of
``python_runner._CONTAINER_WRAPPER`` — importing an installed entrypoint, not
injected source.

Before loading the agent, it installs a process-spawn guard that neutralizes
fork/exec/subprocess at the Python level (defense-in-depth; the container flags
— cap-drop-ALL, read-only rootfs, noexec /tmp, pids-limit, network none — are
the real boundary).

Protocol (one JSON object per line):
  host -> container: {"entrypoint":"module:func","goal":...,"kwargs":{...}}  (init)
  container -> host: {"id","type":"run_tool"|"call_llm", ...}               (request)
  host -> container: {"id","ok",true/false,"result"/"completion"/"error"}
  container -> host: {"id":0,"type":"result","ok",..,"value"/"error"}       (final)

NOT wired into ``run_agent`` in B1 — only the backend session can use it.
"""
from __future__ import annotations

WRAPPER_SOURCE = r'''
import sys, json, io, importlib, os

_real_out = sys.stdout
_real_err = sys.stderr
_real_in = sys.stdin

# Defense-in-depth (NOT the security boundary — the container is: --cap-drop=ALL,
# --read-only, noexec /tmp, --pids-limit, --network none). This Python-level guard
# neutralizes process-spawning APIs before the agent's code loads, so a sandboxed
# agent cannot fork/exec even inside the locked container. A determined native/
# ctypes call could bypass it, but the container flags contain that too; this only
# removes the easy footgun. Threads are unaffected (CPython threads don't os.fork).
def _install_process_guard():
    def _blocked(*_a, **_k):
        raise PermissionError(
            "process spawning is disabled for sandboxed agents (fork/exec/subprocess)"
        )
    for _name in (
        "fork", "forkpty", "system", "posix_spawn", "posix_spawnp",
        "exec", "execl", "execle", "execlp", "execlpe",
        "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle",
        "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
    ):
        if hasattr(os, _name):
            try:
                setattr(os, _name, _blocked)
            except Exception:
                pass
    try:
        import subprocess as _sp
        _sp.Popen = _blocked
        _sp.run = _blocked
        _sp.call = _blocked
        _sp.check_call = _blocked
        _sp.check_output = _blocked
        _sp.getoutput = _blocked
        _sp.getstatusoutput = _blocked
    except Exception:
        pass

_install_process_guard()

def _send(req):
    _real_out.write(json.dumps(req) + "\n")
    _real_out.flush()
    line = _real_in.readline()
    if not line:
        raise RuntimeError("host closed the control channel")
    return json.loads(line)

class _Ctx:
    def __init__(self, goal):
        self.goal = goal
        self._id = 0
    def _rpc(self, _type, **fields):
        self._id += 1
        fields["id"] = self._id
        fields["type"] = _type
        resp = _send(fields)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "rpc error"))
        return resp
    def run_tool(self, slug, tool_name=None, **kwargs):
        return self._rpc("run_tool", slug=slug, tool_name=tool_name, kwargs=kwargs)["result"]
    def call_llm(self, messages):
        return self._rpc("call_llm", messages=messages)["completion"]

def _load(entrypoint):
    if ":" in entrypoint:
        module_path, func_name = entrypoint.rsplit(":", 1)
    else:
        module_path, func_name = entrypoint, "run"
    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name, None)
    if not callable(func):
        raise RuntimeError("agent entrypoint '" + str(entrypoint) + "' not found")
    return func

def _main():
    init = json.loads(_real_in.readline())
    func = _load(init["entrypoint"])
    # Redirect the agent's own stdout/stderr so neither can corrupt the control
    # channel (Python-level only; native fd-1 writes are a known, measured risk).
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        value = func(_Ctx(init.get("goal")), **(init.get("kwargs") or {}))
        out = {"id": 0, "type": "result", "ok": True, "value": value}
    except Exception as exc:
        out = {"id": 0, "type": "result", "ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    finally:
        sys.stdout = _real_out
        sys.stderr = _real_err
    _real_out.write(json.dumps(out) + "\n")
    _real_out.flush()

_main()
'''
