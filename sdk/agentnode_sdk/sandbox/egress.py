"""Egress proxy + network lifecycle for Design A: the topology is the boundary.

A payload that is allowed to reach something joins ONLY an ``--internal`` network, which has no route
out, and a dual-homed CONNECT proxy on a second network is its sole way through. The proxy enforces an
exact-match allowlist. The proxy environment merely routes; it is not the boundary.

## What the spike on the real worker host established, and what it changed here

Stage 0A proved this with Docker. It was measured again on rootless podman 5.8.2 with netavark, on the
machine that runs foreign code, as the account that runs it:

  * the payload's namespace has one interface and one route -- its own subnet. No default route, IPv4 or
    IPv6, read from the kernel side with ``nsenter`` rather than from the payload;
  * every direct destination -- public IPv4 and IPv6 literals, IPv4-mapped IPv6, UDP to a resolver,
    UDP/443, cloud metadata on both families, the worker's own private address and management port, the
    other network's gateway, and the proxy's address on the OTHER network -- answers ``Network is
    unreachable`` from the kernel;
  * through the proxy, a name that is not on the allowlist, a private address, the metadata address and
    localhost are all refused;
  * two concurrent runs cannot reach each other's proxy or network.

And it found one channel this module used to leave open. **FINDING-EGRESS-1**: on a default internal
network the payload can reach the container DNS resolver, on the internal bridge's own gateway address,
UDP/53 -- a host-side process. It answered. That resolver was load-bearing only because the payload
reached the proxy by the NAME ``egress-proxy``.

So on podman the internal network is now created with ``--disable-dns``, which makes the runtime assign
that bridge no gateway address at all, and the payload is given the proxy's own address instead of a
name. There is then no host-side address in the payload's subnet to reach. Measured: with DNS disabled
the runtime reports dns false and no gateway, and an allowed CONNECT, a refused CONNECT and a real
package install all still work.

Docker has no equivalent flag and serves its embedded DNS inside the container, so on Docker the alias is
kept and the difference is recorded in the handle's readings rather than papered over.

## Ownership is by label and by runtime id, never by name

A name is a string anybody can choose. Every network and container created here carries labels naming the
run, the account and the worker epoch, and the handle keeps the ids the runtime assigned. Teardown and
reconciliation select on those labels and ids, so a resource that happens to share a name is not adopted
and one whose name was changed is not orphaned.
"""
from __future__ import annotations

import atexit
import inspect as _inspect
import json
import os
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import uuid4

from agentnode_sdk.sandbox import egress_proxy as _egress_proxy_mod
from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE, _HARDENED_FLAGS
from agentnode_sdk.sandbox.policy import get_default_backend
from agentnode_sdk.sandbox.types import EgressSpec, SandboxRequiredError

_PROXY_ALIAS = "egress-proxy"
_PROXY_PORT = 8888

#: Every resource this module creates carries this. Reconciliation selects on it, so it never has to
#: guess from a name.
_COMPONENT = "agentnode.component=egress"
_UNATTRIBUTED = "unattributed"


@dataclass(frozen=True)
class EgressOwner:
    """Whose run a network and a proxy belong to. Carried in labels, not in names."""

    run: str = _UNATTRIBUTED
    account: str = _UNATTRIBUTED
    epoch: str = _UNATTRIBUTED

    def as_labels(self) -> list:
        out = ["--label", _COMPONENT]
        for key, value in (("run", self.run), ("account", self.account), ("epoch", self.epoch)):
            out += ["--label", "agentnode.%s=%s" % (key, self._clean(value))]
        return out

    @staticmethod
    def _clean(value) -> str:
        """A label value is not a place to smuggle anything: what goes in is what reads back."""
        text = re.sub(r"[^A-Za-z0-9._:-]", "_", str(value or _UNATTRIBUTED))[:128]
        return text or _UNATTRIBUTED


@dataclass(frozen=True)
class EgressHandle:
    int_net: str
    ext_net: str
    proxy_name: str
    runtime: str
    spec: EgressSpec
    int_net_id: str = ""
    ext_net_id: str = ""
    proxy_id: str = ""
    owner: EgressOwner = field(default_factory=EgressOwner)
    #: What the RUNTIME said about what was created, as pairs so this stays hashable. This is the
    #: material a run's record binds: not what was asked for -- what is there.
    readings: tuple = ()

    def as_record(self) -> dict:
        """What a run's signed record keeps about its route out."""
        return {
            "internal_network": {"name": self.int_net, "id": self.int_net_id},
            "external_network": {"name": self.ext_net, "id": self.ext_net_id},
            "proxy": {"name": self.proxy_name, "id": self.proxy_id, "url": self.spec.proxy_url},
            "allowed_destinations": list(self.spec.allowed_domains or ()),
            "owner": {"run": self.owner.run, "account": self.owner.account,
                      "epoch": self.owner.epoch},
            "runtime": os.path.basename(str(self.runtime or "")),
            "readings": dict(self.readings),
        }


# ----------------------------------------------------------------------------
# fail-closed allowlist validation
# ----------------------------------------------------------------------------

def validate_allowed_domains(domains) -> tuple:
    """Return a canonical, de-duplicated tuple of bare hostnames, or raise ValueError.

    Delegates to the shared, lifecycle-free ``domain_policy`` canonicaliser so egress and the
    install-seal share ONE source of truth. ``DomainPolicyError`` subclasses ``ValueError``.
    """
    from agentnode_sdk.sandbox.domain_policy import canonicalize_allowed_domains
    return canonicalize_allowed_domains(domains)


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
    except Exception:                                             # noqa: BLE001
        pass


def _is_podman(runtime: str) -> bool:
    return os.path.basename(str(runtime or "")) == "podman"


def _json_of(argv) -> dict:
    """``inspect --format '{{json .}}'``, parsed. Read tolerantly: podman and docker disagree on names."""
    out = _run(argv).stdout.strip()
    if not out:
        return {}
    try:
        parsed = json.loads(out)
    except ValueError:
        return {}
    if isinstance(parsed, list):
        return parsed[0] if parsed and isinstance(parsed[0], dict) else {}
    return parsed if isinstance(parsed, dict) else {}


def _first(mapping: dict, *names, default=None):
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def _network_facts(rt: str, name: str) -> dict:
    """What the runtime says a network IS, read back after creating it."""
    raw = _json_of([rt, "network", "inspect", name, "--format", "{{json .}}"])
    subnets = []
    for entry in (_first(raw, "subnets", "Subnets", default=[]) or []):
        if isinstance(entry, dict):
            subnets.append({"subnet": _first(entry, "subnet", "Subnet", default=""),
                            "gateway": _first(entry, "gateway", "Gateway", default="")})
    if not subnets:
        ipam = _first(raw, "IPAM", default={}) or {}            # docker keeps them here
        for entry in (ipam.get("Config") or []):
            if isinstance(entry, dict):
                subnets.append({"subnet": entry.get("Subnet", ""),
                                "gateway": entry.get("Gateway", "")})
    return {
        "id": str(_first(raw, "id", "Id", "ID", default="") or ""),
        "internal": bool(_first(raw, "internal", "Internal", default=False)),
        "dns_enabled": bool(_first(raw, "dns_enabled", "DNSEnabled", default=False)),
        "ipv6_enabled": bool(_first(raw, "ipv6_enabled", "IPv6Enabled", "EnableIPv6", default=False)),
        "subnets": subnets,
        "labels": dict(_first(raw, "labels", "Labels", default={}) or {}),
    }


def _proxy_addresses(rt: str, name: str) -> dict:
    """``{network: {"id":…, "v4":…, "v6":…}}`` for the proxy, read back from the runtime."""
    raw = _json_of([rt, "inspect", name, "--format", "{{json .NetworkSettings.Networks}}"])
    out = {}
    for net, info in (raw or {}).items():
        if not isinstance(info, dict):
            continue
        out[str(net)] = {"id": str(_first(info, "NetworkID", "networkID", default="") or ""),
                         "v4": str(_first(info, "IPAddress", "ipAddress", default="") or ""),
                         "v6": str(_first(info, "GlobalIPv6Address", default="") or "")}
    return out


def _proxy_argv(rt: str, name: str, ext_net: str, domains: tuple, owner: EgressOwner) -> list:
    argv = [rt, "run", "-d", "--name", name, "--label", "agentnode-egress"]
    argv += owner.as_labels() + ["--label", "agentnode.kind=egress-proxy"]
    argv += ["--network", ext_net]
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
    """Best-effort teardown of ONLY our own named resources. Never a broad or prefix sweep."""
    if proxy_name:
        _safe(lambda: _run([rt, "rm", "-f", proxy_name]))
    for n in nets:
        _safe(lambda n=n: _run([rt, "network", "rm", n]))


def start_egress_proxy(allowed_domains, *, backend=None, health_timeout: float = 10.0,
                       owner: EgressOwner | None = None) -> EgressHandle:
    """Create the internal and external networks and a dual-homed CONNECT proxy.

    FAIL-CLOSED throughout: an invalid or empty allowlist raises ValueError before any runtime call; an
    unavailable backend raises SandboxRequiredError before anything is created; a failure in any step
    tears down what was already created and re-raises. It never returns a partially built handle.

    What it also does, and what the earlier version did not: it reads back from the runtime what was
    actually created, refuses if that disagrees with what was asked for, and puts the ids and those
    readings on the handle so a run's record can bind them.
    """
    domains = validate_allowed_domains(allowed_domains)      # ValueError before any runtime call
    who = owner or EgressOwner()
    be = backend or get_default_backend()
    avail = be.check_available()
    if not avail.available:
        raise SandboxRequiredError(
            "egress proxy requires a container runtime + the pinned image: "
            + (avail.reason or "unavailable"))
    rt = avail.backend
    token = uuid4().hex[:8]
    int_net = f"agentnode-egress-{token}-int"
    ext_net = f"agentnode-egress-{token}-ext"
    proxy = f"agentnode-egress-{token}-proxy"
    nets = []
    proxy_started = False
    try:
        # FINDING-EGRESS-1: on podman, DNS off means the bridge gets no gateway address either, so the
        # payload has no host-side address in its own subnet at all. Docker has no such flag.
        internal_argv = [rt, "network", "create", "--internal"]
        if _is_podman(rt):
            internal_argv.append("--disable-dns")
        internal_argv += who.as_labels() + ["--label", "agentnode.kind=egress-internal", int_net]
        _run(internal_argv)
        nets.append(int_net)
        _run([rt, "network", "create"] + who.as_labels()
             + ["--label", "agentnode.kind=egress-external", ext_net])
        nets.append(ext_net)
        _run(_proxy_argv(rt, proxy, ext_net, domains, who))
        proxy_started = True
        join = [rt, "network", "connect"]
        if not _is_podman(rt):
            join += ["--alias", _PROXY_ALIAS]   # an alias means something only where a resolver answers
        _run(join + [int_net, proxy])
        _wait_healthy(rt, proxy, health_timeout)

        # ---- read back what exists, and refuse if it is not what was asked for --------------------
        int_facts = _network_facts(rt, int_net)
        ext_facts = _network_facts(rt, ext_net)
        addresses = _proxy_addresses(rt, proxy)
        proxy_id = _run([rt, "inspect", proxy, "--format", "{{.Id}}"]).stdout.strip()
        if not int_facts.get("internal"):
            raise SandboxRequiredError(
                "the network meant to have no route out does not report itself internal, so the "
                "boundary is not there. Nothing is started on it.")
        if _is_podman(rt) and int_facts.get("dns_enabled"):
            raise SandboxRequiredError(
                "the payload network still carries a resolver, which is a host-side process inside the "
                "payload's own subnet (FINDING-EGRESS-1). Nothing is started on it.")
        if set(addresses) != {int_net, ext_net}:
            raise SandboxRequiredError(
                "the proxy is attached to %r rather than to exactly its own two networks, so what it can "
                "reach is not what was arranged. Nothing is started." % (sorted(addresses),))
        inside = addresses.get(int_net, {}).get("v4", "")
        if not inside:
            raise SandboxRequiredError(
                "the runtime gave the proxy no address on the payload's network, so the payload would "
                "have no way through. Nothing is started.")
        proxy_url = (f"http://{inside}:{_PROXY_PORT}" if _is_podman(rt)
                     else f"http://{_PROXY_ALIAS}:{_PROXY_PORT}")
        readings = (
            ("internal_network_internal", int_facts.get("internal")),
            ("internal_network_dns_enabled", int_facts.get("dns_enabled")),
            ("internal_network_ipv6_enabled", int_facts.get("ipv6_enabled")),
            ("internal_network_subnets", json.dumps(int_facts.get("subnets"), sort_keys=True)),
            ("external_network_internal", ext_facts.get("internal")),
            ("external_network_ipv6_enabled", ext_facts.get("ipv6_enabled")),
            ("proxy_networks", json.dumps(addresses, sort_keys=True)),
            ("proxy_reached_by", "address" if _is_podman(rt) else "alias"),
            ("labels_on_the_internal_network", json.dumps(int_facts.get("labels"), sort_keys=True)),
        )
    except Exception:
        _teardown(rt, proxy if proxy_started else None, nets)
        raise
    handle = EgressHandle(
        int_net=int_net, ext_net=ext_net, proxy_name=proxy, runtime=rt,
        int_net_id=int_facts.get("id", ""), ext_net_id=ext_facts.get("id", ""),
        proxy_id=proxy_id, owner=who, readings=readings,
        spec=EgressSpec(network_name=int_net, proxy_url=proxy_url, allowed_domains=domains),
    )
    with _live_lock:
        _live.add(handle)
    return handle


def stop_egress_proxy(handle: EgressHandle) -> None:
    """Idempotent, best-effort teardown of ONLY this handle's own proxy and two networks."""
    _teardown(handle.runtime, handle.proxy_name, [handle.int_net, handle.ext_net])
    with _live_lock:
        _live.discard(handle)


def remove_what_no_run_is_waiting_for(runtime: str = "", *, keep_runs=()) -> dict:
    """Remove egress resources carrying this component's label that belong to no live run.

    This is restart reconciliation, and it selects on the LABEL rather than on a name pattern: a network
    called something familiar is not adopted, and one that was renamed is not orphaned. A caller that
    knows which runs are live passes them in ``keep_runs``; everything else labelled as this component's
    is a leftover of a worker that is gone.
    """
    rt = runtime or ""
    if not rt:
        avail = get_default_backend().check_available()
        if not avail.available:
            return {"asked": False, "reason": avail.reason or "no runtime"}
        rt = avail.backend
    keep = {str(r) for r in keep_runs}
    removed = {"asked": True, "containers": [], "networks": [], "kept": sorted(keep)}
    try:
        listed = _run([rt, "ps", "-a", "--filter", "label=" + _COMPONENT,
                       "--format", '{{.ID}} {{index .Labels "agentnode.run"}}']).stdout
    except Exception as exc:                                      # noqa: BLE001
        return {"asked": False, "reason": str(exc)}
    for line in listed.splitlines():
        parts = line.split()
        if not parts:
            continue
        cid = parts[0]
        run = parts[1] if len(parts) > 1 else ""
        if run and run in keep:
            continue
        _safe(lambda cid=cid: _run([rt, "rm", "-f", cid]))
        removed["containers"].append(cid)
    try:
        nets = _run([rt, "network", "ls", "--filter", "label=" + _COMPONENT,
                     "--format", "{{.ID}}"]).stdout
    except Exception:                                             # noqa: BLE001
        nets = ""
    for nid in [n for n in nets.split() if n]:
        try:
            facts = _network_facts(rt, nid)
        except Exception:                                         # noqa: BLE001
            facts = {}
        if str(facts.get("labels", {}).get("agentnode.run", "")) in keep and keep:
            continue
        _safe(lambda nid=nid: _run([rt, "network", "rm", "-f", nid]))
        removed["networks"].append(nid)
    return removed


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
