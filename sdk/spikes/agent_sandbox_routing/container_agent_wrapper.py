"""SDK-free in-container wrapper (throwaway spike).

Runs INSIDE the sandbox via ``python3 -c WRAPPER_SOURCE``. It speaks a tiny
newline-delimited JSON protocol over the process's real stdin/stdout (the control
channel) while redirecting the agent's own stdout/stderr to a capture buffer so
they cannot corrupt the protocol — the same "save the real stdout" trick used by
``python_runner._CONTAINER_WRAPPER``, extended to be bidirectional.

Protocol (one JSON object per line):
  host -> container: {"agent_source","function","goal","kwargs"}        (init)
  container -> host: {"id","type":"run_tool"|"call_llm", ...}           (request)
  host -> container: {"id","ok",true/false,"result"/"completion"/"error"}
  container -> host: {"id":0,"type":"result","ok",..,"value"/"error"}   (final)
"""
from __future__ import annotations

WRAPPER_SOURCE = r'''
import sys, json, io

_real_out = sys.stdout
_real_err = sys.stderr
_real_in = sys.stdin

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

def _main():
    init = json.loads(_real_in.readline())
    ns = {}
    exec(init["agent_source"], ns)
    func = ns.get(init.get("function", "run"))
    if not callable(func):
        raise RuntimeError("agent 'run' function not found")
    # Redirect the agent's own stdout/stderr so neither can corrupt the control
    # channel (Python-level only; native code writing to fd 1/2 is a known risk
    # this spike measures).
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
