"""Egress proxy + network lifecycle (Stage 2) for the proven Design A.

INERT: nothing in the product run paths calls this yet. It only provides the
capability to back a ``ProcessSpec.egress`` handle with a real Docker ``--internal``
network + a dual-homed CONNECT proxy. Credentialed MCPs remain refused (mcp_runner
unchanged); wiring + consent are Stage 3.

Security model (honest): the boundary is the TOPOLOGY — the payload container joins
only the ``--internal`` network (no route to the host/internet); the dual-homed proxy
is its sole egress and enforces a strict, exact-match allowlist (proven in Stage 0A).
The proxy env merely routes; it is not the boundary. Allowlist domains are validated
fail-closed HERE (defense-in-depth), independently of any later manifest validation.
"""
from __future__ import annotations

import atexit
import inspect as _inspect
import ipaddress
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from agentnode_sdk.sandbox import egress_proxy as _egress_proxy_mod
from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE, _HARDENED_FLAGS
from agentnode_sdk.sandbox.policy import get_default_backend
from agentnode_sdk.sandbox.types import EgressSpec, SandboxRequiredError

_PROXY_ALIAS = "egress-proxy"
_PROXY_PORT = 8888
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class EgressHandle:
    int_net: str
    ext_net: str
    proxy_name: str
    runtime: str
    spec: EgressSpec


# ----------------------------------------------------------------------------
# fail-closed allowlist validation (Stage 2, defense-in-depth)
# ----------------------------------------------------------------------------

def validate_allowed_domains(domains) -> tuple:
    """Return a canonical, de-duplicated tuple of bare hostnames, or raise ValueError.

    Rejects: empty input, non-strings, scheme/port/path/wildcard/@/whitespace,
    IP literals (incl. loopback/private/link-local/metadata), ``localhost``,
    single-label names, and invalid labels. Hosts are lowercased + trailing-dot
    stripped (so uppercase is normalized, not rejected).
    """
    if not domains:
        raise ValueError("allowed_domains must be non-empty (an egress proxy needs an allowlist)")
    out = []
    for d in domains:
        if not isinstance(d, str):
            raise ValueError(f"domain must be a string: {d!r}")
        h = d.strip().lower().rstrip(".")
        if not h:
            raise ValueError(f"empty domain: {d!r}")
        if any(c in h for c in ("/", ":", "*", "?", "#", "@", "\\", " ", "\t")):
            raise ValueError(f"bare hostname only (no scheme/port/path/wildcard): {d!r}")
        try:
            ipaddress.ip_address(h)
        except ValueError:
            pass  # not an IP literal -> good
        else:
            raise ValueError(f"IP literals are not allowed (use a hostname): {d!r}")
        if h == "localhost":
            raise ValueError("localhost is not allowed")
        labels = h.split(".")
        if len(labels) < 2:
            raise ValueError(f"need a fully-qualified domain (>=2 labels): {d!r}")
        for lab in labels:
            if not _LABEL.match(lab):
                raise ValueError(f"invalid domain label {lab!r} in {d!r}")
        out.append(h)
    seen = set()
    res = []
    for h in out:
        if h not in seen:
            seen.add(h)
            res.append(h)
    return tuple(res)


# ----------------------------------------------------------------------------
# lifecycle
# ----------------------------------------------------------------------------

_live = set()
_live_lock = threading.Lock()


def _run(argv, timeout: float = 30.0):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True)


def _safe(fn) -> None:
    try:
        fn()
    except Exception:
        pass


def _proxy_argv(rt: str, name: str, ext_net: str, domains: tuple) -> list:
    argv = [rt, "run", "-d", "--name", name, "--label", "agentnode-egress",
            "--network", ext_net]
    argv += list(_HARDENED_FLAGS)
    argv += ["-e", "EGRESS_ALLOWLIST=" + ",".join(domains)]
    argv += [_BASE_IMAGE, "python", "-c", _inspect.getsource(_egress_proxy_mod)]
    return argv


def _wait_healthy(rt: str, name: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        running = _run([rt, "inspect", "-f", "{{.State.Running}}", name]).stdout.strip()
        if running != "true":
            raise SandboxRequiredError(f"egress proxy {name} exited during startup")
        cp = _run([rt, "logs", name])
        if "egress-proxy listening" in (cp.stdout + cp.stderr):
            return
        time.sleep(0.2)
    raise SandboxRequiredError(f"egress proxy {name} did not become healthy in {timeout}s")


def _teardown(rt: str, proxy_name, nets) -> None:
    """Best-effort teardown of ONLY our own named resources. Never broad / prefix sweep."""
    if proxy_name:
        _safe(lambda: _run([rt, "rm", "-f", proxy_name]))
    for n in nets:
        _safe(lambda n=n: _run([rt, "network", "rm", n]))


def start_egress_proxy(allowed_domains, *, backend=None, health_timeout: float = 10.0) -> EgressHandle:
    """Create the internal+egress networks and a dual-homed CONNECT proxy.

    FAIL-CLOSED: invalid/empty ``allowed_domains`` -> ValueError BEFORE any docker call;
    unavailable backend -> SandboxRequiredError BEFORE creating resources; a failure in
    any creation step tears down what was already created and re-raises. Never returns a
    partially-built handle. The returned handle is registered for atexit teardown.
    """
    domains = validate_allowed_domains(allowed_domains)  # ValueError before any docker
    be = backend or get_default_backend()
    avail = be.check_available()
    if not avail.available:
        raise SandboxRequiredError(
            "egress proxy requires a container runtime + the pinned image: "
            + (avail.reason or "unavailable")
        )
    rt = avail.backend
    token = uuid4().hex[:8]
    int_net = f"agentnode-egress-{token}-int"
    ext_net = f"agentnode-egress-{token}-ext"
    proxy = f"agentnode-egress-{token}-proxy"
    nets = []
    proxy_started = False
    try:
        _run([rt, "network", "create", "--internal", int_net]); nets.append(int_net)
        _run([rt, "network", "create", ext_net]); nets.append(ext_net)
        _run(_proxy_argv(rt, proxy, ext_net, domains)); proxy_started = True
        _run([rt, "network", "connect", "--alias", _PROXY_ALIAS, int_net, proxy])
        _wait_healthy(rt, proxy, health_timeout)
    except Exception:
        _teardown(rt, proxy if proxy_started else None, nets)
        raise
    handle = EgressHandle(
        int_net=int_net, ext_net=ext_net, proxy_name=proxy, runtime=rt,
        spec=EgressSpec(
            network_name=int_net,
            proxy_url=f"http://{_PROXY_ALIAS}:{_PROXY_PORT}",
            allowed_domains=domains,
        ),
    )
    with _live_lock:
        _live.add(handle)
    return handle


def stop_egress_proxy(handle: EgressHandle) -> None:
    """Idempotent, best-effort teardown of ONLY this handle's own proxy + two networks."""
    _teardown(handle.runtime, handle.proxy_name, [handle.int_net, handle.ext_net])
    with _live_lock:
        _live.discard(handle)


@contextmanager
def egress_proxy(allowed_domains, **kw):
    handle = start_egress_proxy(allowed_domains, **kw)
    try:
        yield handle
    finally:
        stop_egress_proxy(handle)


def _atexit_teardown() -> None:
    with _live_lock:
        handles = list(_live)
    for h in handles:
        _safe(lambda h=h: stop_egress_proxy(h))


atexit.register(_atexit_teardown)
