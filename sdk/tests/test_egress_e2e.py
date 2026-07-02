"""Stage 2: daemon-gated end-to-end egress test — repeats the Stage-0A bypass matrix
through the real lifecycle module.

SKIPPED unless AGENTNODE_EGRESS_E2E=1 AND a container runtime + the pinned image are
available. Never runs in the default suite. It creates throwaway agentnode-egress-*
resources and tears them down via stop_egress_proxy().
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from agentnode_sdk.sandbox import egress
from agentnode_sdk.sandbox.container_backend import ContainerBackend, _BASE_IMAGE

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTNODE_EGRESS_E2E") != "1",
    reason="set AGENTNODE_EGRESS_E2E=1 (needs docker/podman + pinned image) to run",
)

# FULL Stage-0A bypass matrix executed INSIDE a throwaway container on the internal net.
# Emits a single "RESULT <json>" line the host parses. Values: raw/direct -> "blocked:<exc>"
# or "BYPASS[:status]"; proxied -> "ALLOWED:<status>" or "refused:<exc>".
_MATRIX = r'''
import os, socket, ssl, json, urllib.request
for k in list(os.environ):
    if k.lower() in ("http_proxy","https_proxy","no_proxy"): os.environ.pop(k, None)
R={}
def raw(host,port,key):
    try: infos=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except Exception as e: R[key]="blocked:"+type(e).__name__; return
    try:
        s=socket.socket(infos[0][0],socket.SOCK_STREAM); s.settimeout(8); s.connect(infos[0][4]); s.close()
        R[key]="BYPASS"
    except Exception as e: R[key]="blocked:"+type(e).__name__
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
def direct(url,key):
    try:
        op=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ctx))
        r=op.open(url,timeout=10); R[key]="BYPASS:"+str(r.status)
    except Exception as e: R[key]="blocked:"+type(e).__name__
def via(url,key):
    try:
        op=urllib.request.build_opener(urllib.request.ProxyHandler({"https":"http://egress-proxy:8888"}),urllib.request.HTTPSHandler(context=ctx))
        r=op.open(url,timeout=15); R[key]="ALLOWED:"+str(r.status)
    except Exception as e: R[key]="refused:"+type(e).__name__
raw("1.1.1.1",443,"T1")
raw("8.8.8.8",443,"T2")
raw("example.com",443,"T3")
direct("https://example.com","T4")
via("https://example.com","T5")
via("https://google.com","T6")
print("RESULT "+json.dumps(R))
'''


def _has_runtime() -> bool:
    return ContainerBackend().check_available().available


def test_egress_e2e_matrix():
    if not _has_runtime():
        pytest.skip("no container runtime + pinned image available")
    handle = egress.start_egress_proxy(["example.com"])
    try:
        rt = handle.runtime
        cp = subprocess.run(
            [rt, "run", "--rm", "--network", handle.int_net, "--label", "agentnode-egress",
             _BASE_IMAGE, "python", "-c", _MATRIX],
            capture_output=True, text=True, timeout=120,
        )
        line = next((ln for ln in cp.stdout.splitlines() if ln.startswith("RESULT ")), None)
        assert line is not None, f"no RESULT line; stdout={cp.stdout!r} stderr={cp.stderr!r}"
        result = json.loads(line[len("RESULT "):])
        assert set(result) == {"T1", "T2", "T3", "T4", "T5", "T6"}, result
        # direct raw / no-proxy egress (incl. resolution) must all be blocked
        for key in ("T1", "T2", "T3", "T4"):
            assert result[key].startswith("blocked"), (key, result[key])
        # only the allowlisted CONNECT works; the non-allowlisted one is refused
        assert result["T5"].startswith("ALLOWED"), result["T5"]
        assert result["T6"].startswith("refused"), result["T6"]
        # nothing escaped the topology
        assert not any(v.startswith("BYPASS") for v in result.values()), result
    finally:
        egress.stop_egress_proxy(handle)
    # nothing of ours should remain
    left = subprocess.run(
        [handle.runtime, "ps", "-a", "--filter", "name=" + handle.proxy_name, "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert left == ""
