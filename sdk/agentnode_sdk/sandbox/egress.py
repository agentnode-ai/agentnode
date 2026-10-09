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
#: Just the value, for reading a label BACK off a candidate rather than asking a runtime to
#: filter on it. One place writes the word; both uses read it from here.
_COMPONENT_VALUE = _COMPONENT.split("=", 1)[1]
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


# ----------------------------------------------------------------------------
# what is left, and whether it really went
#
# CU6 and CU7 of frozen/cleanup.json, and both exist because of F37. The sweep below used to do
# `_safe(lambda: _run([rt, "rm", "-f", cid]))` and then append the id to a `removed` list -- so a
# removal the runtime REFUSED was recorded as done. On the real worker `podman rm -f` answered
# "could not be stopped: sending SIGKILL to container ...: operation not permitted" four times, the
# sweep reported four containers removed, and the worker served with four proxies still there. Nothing
# in the record said otherwise.
#
# So: every removal is read back, and every listing is parsed into the shape a row has. An answer that
# cannot be parsed is UNKNOWN, and unknown is not none -- which is the rule container_backend already
# states for a single container ("unknown must never be read as absent") and which an inventory needs
# just as much: a count of this bundle's own that read a stray runtime notice as a container reported
# one on a host that had none.
# ----------------------------------------------------------------------------

#: Lines a runtime writes that are not rows. Checked as a prefix on the first token, so a row whose id
#: happens to contain one of these words is still a row.
#: WHAT A ROW LOOKS LIKE, as an allow-list. Every listing this module reads is asked for ONE field, so
#: a row is one token of the shape asked for, and anything else is output nobody can place. This used
#: to be a DENY-list -- a line was a row unless its first token began with one of a handful of runtime
#: notice prefixes -- and an independent review found the hole that shape always has: `Cannot connect
#: to the runtime` begins with none of them, so it counted as a resource. CU7 says a listing is parsed
#: into rows that match what the thing looks like and that anything else makes the answer UNKNOWN; a
#: deny-list cannot say that, because it is a list of the surprises somebody has already had.
_AN_ID = re.compile(r"^[0-9a-fA-F]{6,64}$")
_A_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,126}$")


def _rows_of(text: str, looks_like=None) -> tuple:
    """``(rows, unreadable)``: one token per row, matching the shape asked for. Nothing is guessed.

    `looks_like` is the shape the CALLER asked the runtime for -- an id where it asked for ids, a name
    where it asked for names -- because only the caller knows which of the two it requested.
    """
    shape = looks_like if looks_like is not None else _A_NAME
    rows, unreadable = [], []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        tokens = line.split()
        if len(tokens) != 1 or not shape.match(tokens[0]):
            unreadable.append(line.strip()[:200])
            continue
        rows.append(tokens[0])
    return rows, unreadable


def _state_of(rt: str, kind: str, name: str) -> str:
    """PRESENT, ABSENT or UNKNOWN for one named container or network, asked of the runtime."""
    from agentnode_sdk.sandbox.container_backend import ABSENT, PRESENT, UNKNOWN

    if kind == "container":
        argv = [rt, "ps", "-a", "--filter", "name=" + name, "--format", "{{.Names}}"]
    else:
        argv = [rt, "network", "ls", "--filter", "name=" + name, "--format", "{{.Name}}"]
    try:
        said = _run(argv).stdout
    except Exception:                                             # noqa: BLE001
        return UNKNOWN
    rows, unreadable = _rows_of(said, _A_NAME)
    if unreadable:
        return UNKNOWN
    # The listing was FILTERED by this name, and both runtimes filter by substring, so every row the
    # filter can have produced contains it. A row that does not is output nobody can place -- and
    # reading it as "another container, so mine is absent" is the same mistake as counting it as mine.
    if any(name not in row for row in rows):
        return UNKNOWN
    return PRESENT if any(row == name for row in rows) else ABSENT


def state_of(runtime: str, kind: str, name: str) -> str:
    """The public name for one read-back, so the worker does not grow a second implementation of it."""
    return _state_of(runtime, kind, name)


def _resolver_dir() -> str:
    """Where a rootless podman keeps one file per network for ``aardvark-dns``."""
    run_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    if not run_dir:
        return ""
    return os.path.join(run_dir, "containers", "networks", "aardvark-dns")


#: A resolver entry is a FILE NAMED AFTER A NETWORK and it carries no label, so this is the one place
#: ownership is decided by a name -- and only by a name this module itself composes, below.
_OUR_NETWORK_NAME = re.compile(r"^agentnode-egress-[0-9a-f]{4,32}-(int|ext)$")


def _resolver_entries(runtime: str = "") -> tuple:
    """``([(name, path)], unreadable)`` for entries belonging to networks this module names.

    THREE OUTCOMES, not two. A directory that is not there is an ANSWER -- nothing is in it, which is
    the ordinary case on a host whose runtime is docker and which has no such resolver at all. A
    directory that IS there and cannot be listed is not an answer, and neither is a rootless podman
    whose runtime directory cannot even be located, because that is exactly where its resolver keeps
    one file per network. Both of those used to return an empty list, which made an unreadable
    inventory look provably empty and would let a migration through: CU7, and the second half of the
    independent review's RR-01.
    """
    where = _resolver_dir()
    if not where:
        if "podman" in (runtime or "").lower():
            return [], ["the resolver directory could not be located: XDG_RUNTIME_DIR is not set, "
                        "and a rootless podman keeps one file per network under it"]
        return [], []
    if not os.path.isdir(where):
        return [], []
    out = []
    try:
        names = sorted(os.listdir(where))
    except OSError as exc:
        return [], ["the resolver directory %s could not be listed: %s" % (where, str(exc)[:120])]
    for name in names:
        if name == "aardvark.pid" or not _OUR_NETWORK_NAME.match(name):
            continue
        out.append((name, os.path.join(where, name)))
    return out, []


def _remove_one(rt: str, kind: str, name: str) -> dict:
    """Remove one resource, then ASK whether it is gone. The runtime's own words travel with a failure.

    `network rm -f` is podman's; docker's `network rm` has no such flag and fails on it, so the flag is
    passed only where it exists. The sweep used to pass it unconditionally.
    """
    from agentnode_sdk.sandbox.container_backend import ABSENT, PRESENT

    said = ""
    if kind == "container":
        argv = [rt, "rm", "-f", name]
    else:
        argv = [rt, "network", "rm"] + (["-f"] if _is_podman(rt) else []) + [name]
    try:
        _run(argv)
    except subprocess.CalledProcessError as exc:
        said = ((exc.stderr or "") + " " + (exc.stdout or "")).strip()[:240]
    except Exception as exc:                                      # noqa: BLE001
        said = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    state = _state_of(rt, kind, name)
    return {"kind": kind, "name": name, "state": state, "why": said,
            "gone": state == ABSENT, "still_there": state == PRESENT}


def _remove_resolver_entry(name: str, path: str) -> dict:
    """Remove one resolver entry and read it back from the filesystem.

    WHY THIS EXISTS AT ALL. Nothing in this product mentioned these files before. In F37 four networks
    were removed while containers were still attached to them, each left its entry behind, and
    `aardvark-dns` then refused to start: "failed to bind udp listener on 10.89.1.1:53: Cannot assign
    requested address". With no resolver, no container on any custom network can resolve a name -- and
    the egress proxy, fail-closed on a resolve failure, answered 403 for a host on its own allowlist.
    """
    from agentnode_sdk.sandbox.container_backend import ABSENT, PRESENT

    said = ""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        said = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    gone = not os.path.exists(path)
    return {"kind": "resolver-entry", "name": name, "state": ABSENT if gone else PRESENT,
            "why": said, "gone": gone, "still_there": not gone}


def what_is_left_of_ours(runtime: str = "") -> dict:
    """Every resource of this component the runtime admits to, with UNKNOWN kept apart from none.

    The answer a migration and a readiness decision are allowed to act on. `asked` false means the
    question could not be put at all, which is not an empty answer either.
    """
    rt = runtime or ""
    if not rt:
        avail = get_default_backend().check_available()
        if not avail.available:
            return {"asked": False, "reason": avail.reason or "no runtime",
                    "containers": [], "networks": [], "resolver_entries": [], "unreadable": []}
        rt = avail.backend
    out = {"asked": True, "runtime": rt, "containers": [], "networks": [],
           "resolver_entries": [], "unreadable": [], "left_alone": [], "reason": ""}
    # IDS FROM THE LISTING, LABELS FROM `inspect` -- the same two steps the networks below use, and
    # for the same reason. `ps --format '{{index .Labels "k"}}'` works on podman, where `.Labels` is a
    # map, and FAILS on docker, where it is a comma-separated string: the runtime exits non-zero, the
    # whole inventory becomes unaskable, and the gate then refuses to serve saying this worker owns
    # something it could not remove -- on a host where it owns nothing. The Docker lane found exactly
    # that, which is what the profile asked it to be checked against. `inspect`'s `.Config.Labels` is
    # a map in both runtimes, and the `--filter` is a label filter both accept.
    try:
        listed = _run([rt, "ps", "-a", "--filter", "label=" + _COMPONENT,
                       "--format", "{{.ID}}"]).stdout
    except Exception as exc:                                      # noqa: BLE001
        out["asked"] = False
        out["reason"] = "the runtime would not list its containers: %s" % str(exc)[:200]
        return out
    rows, unreadable = _rows_of(listed, _AN_ID)
    out["unreadable"] += ["containers: " + u for u in unreadable]
    for row in rows:
        cid = row.split()[0]
        try:
            labels = _container_labels(rt, cid)
        except Exception:                                         # noqa: BLE001
            # Not "it is not ours". A container whose labels cannot be read is one nobody can place,
            # and CU7 says an answer that could not be read is not an empty one.
            out["unreadable"].append("containers: %s could not be inspected" % cid)
            continue
        if str(labels.get("agentnode.component", "")) != _COMPONENT_VALUE:
            out["left_alone"].append(cid)
            continue
        out["containers"].append({"id": cid, "run": str(labels.get("agentnode.run", ""))})
    try:
        nets = _run([rt, "network", "ls", "--filter", "label=" + _COMPONENT,
                     "--format", "{{.ID}}"]).stdout
    except Exception as exc:                                      # noqa: BLE001
        out["unreadable"].append("networks: could not be listed: %s" % str(exc)[:160])
        nets = ""
    rows, unreadable = _rows_of(nets, _AN_ID)
    out["unreadable"] += ["networks: " + u for u in unreadable]
    for row in rows:
        nid = row.split()[0]
        try:
            facts = _network_facts(rt, nid)
        except Exception:                                         # noqa: BLE001
            out["unreadable"].append("networks: %s could not be inspected" % nid)
            continue
        labels = facts.get("labels") or {}
        if str(labels.get("agentnode.component", "")) != _COMPONENT_VALUE:
            out["left_alone"].append(nid)
            continue
        out["networks"].append({"id": nid, "run": str(labels.get("agentnode.run", ""))})
    entries, cannot_read = _resolver_entries(rt)
    out["unreadable"] += ["resolver: " + u for u in cannot_read]
    for name, path in entries:
        out["resolver_entries"].append({"name": name, "path": path})
    return out


def nothing_of_ours_is_left(runtime: str = "") -> tuple:
    """``(True, inventory)`` only when the inventory is empty AND every part of it could be read."""
    got = what_is_left_of_ours(runtime)
    if not got.get("asked"):
        return False, got
    empty = not (got["containers"] or got["networks"] or got["resolver_entries"])
    return bool(empty and not got["unreadable"]), got


def remove_everything_of_ours(runtime: str = "", *, keep_runs=()) -> dict:
    """Remove every resource of this component, in an order that cannot orphan one, reading each back.

    Containers first, then the networks they sat on, then the resolver entries of networks that are no
    longer there. That order is CU2: removing a network while a container is still attached to it is
    what left the entries behind in F37.
    """
    keep = {str(r) for r in keep_runs}
    got = what_is_left_of_ours(runtime)
    rt = got.get("runtime") or runtime
    out = {"asked": bool(got.get("asked")), "runtime": rt, "removed": [], "failed": [],
           "left_alone": list(got.get("left_alone") or []),
           "unreadable": list(got.get("unreadable") or []), "kept": sorted(keep),
           "reason": got.get("reason", "")}
    if not out["asked"]:
        out["clean"] = False
        return out
    for container in got["containers"]:
        if container["run"] and container["run"] in keep:
            continue
        said = _remove_one(rt, "container", container["id"])
        (out["removed"] if said["gone"] else out["failed"]).append(said)
    for network in got["networks"]:
        if keep and network["run"] in keep:
            continue
        said = _remove_one(rt, "network", network["id"])
        (out["removed"] if said["gone"] else out["failed"]).append(said)
    # The entries are re-read AFTER the networks have gone: an entry whose network still exists is not
    # a leftover, and removing it would take the resolver away from a live network.
    live = {str(n.get("id")) for n in what_is_left_of_ours(rt).get("networks") or []}
    entries, cannot_read = _resolver_entries(rt)
    out["unreadable"] = list(out.get("unreadable") or []) + ["resolver: " + u for u in cannot_read]
    for name, path in entries:
        if _state_of(rt, "network", name) == "present" or name in live:
            continue
        said = _remove_resolver_entry(name, path)
        (out["removed"] if said["gone"] else out["failed"]).append(said)
    clean, after = nothing_of_ours_is_left(rt)
    out["clean"] = bool(clean and not out["failed"])
    out["afterwards"] = {"containers": len(after.get("containers") or []),
                         "networks": len(after.get("networks") or []),
                         "resolver_entries": len(after.get("resolver_entries") or []),
                         "unreadable": list(after.get("unreadable") or [])}
    return out


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


def _container_labels(rt: str, cid: str) -> dict:
    """The labels of one container, asked a way BOTH runtimes answer.

    Deliberately not from `ps --format`: see the comment at the listing in `what_is_left_of_ours`.
    A container with no labels at all answers null, which is an empty mapping and not a failure.
    """
    raw = _json_of([rt, "inspect", cid, "--format", "{{json .Config.Labels}}"])
    return raw if isinstance(raw, dict) else {}


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


def _teardown(rt: str, proxy_name, nets) -> dict:
    """Teardown of ONLY our own named resources, read back, and it SAYS what happened to each.

    It used to be `_safe(...)` around each removal and nothing else: a failure was swallowed and the
    caller could not tell a teardown that worked from one that did not. The caller now gets a report,
    and `complete` is false when anything is still there or anything could not be asked.
    """
    said = []
    if proxy_name:
        said.append(_remove_one(rt, "container", proxy_name))
    for n in nets:
        if n:
            said.append(_remove_one(rt, "network", n))
    # The resolver entries of exactly these networks, and only once their network is gone.
    entries, _cannot_read = _resolver_entries(rt)
    for name, path in entries:
        if name not in set(nets or ()):
            continue
        if _state_of(rt, "network", name) == "present":
            continue
        said.append(_remove_resolver_entry(name, path))
    return {"resources": said,
            "complete": all(x["gone"] for x in said) if said else True,
            "still_there": [x["name"] for x in said if x["still_there"]]}


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


def stop_egress_proxy(handle: EgressHandle) -> dict:
    """Idempotent teardown of ONLY this handle's own proxy, two networks and resolver entries.

    Returns the report rather than nothing, so a run can record whether its own route out went away.
    Idempotent: a second call removes nothing and reports every resource as already gone.
    """
    said = _teardown(handle.runtime, handle.proxy_name, [handle.int_net, handle.ext_net])
    with _live_lock:
        _live.discard(handle)
    return said


def remove_what_no_run_is_waiting_for(runtime: str = "", *, keep_runs=()) -> dict:
    """Remove egress resources carrying this component's label that belong to no live run.

    This is restart reconciliation, and it selects on the LABEL rather than on a name pattern: a network
    called something familiar is not adopted, and one that was renamed is not orphaned. A caller that
    knows which runs are live passes them in ``keep_runs``; everything else labelled as this component's
    is a leftover of a worker that is gone.

    WHAT CHANGED AND WHY. It now delegates to `remove_everything_of_ours`, which reads every removal
    back. The old version appended an id to `containers` after a `_safe(...)` removal whether or not the
    runtime had done it -- which is how four containers the account could not remove were reported as
    removed (F37, and EG12 of the previous arc's frozen profile, which an independent review failed on
    exactly this). `containers` and `networks` still carry what was REMOVED, so a caller that only reads
    those sees what it always saw; `failed`, `unreadable` and `clean` are what a caller has to read to
    know the answer.
    """
    said = remove_everything_of_ours(runtime, keep_runs=keep_runs)
    out = dict(said)
    out["containers"] = [x["name"] for x in said["removed"] if x["kind"] == "container"]
    out["networks"] = [x["name"] for x in said["removed"] if x["kind"] == "network"]
    out["resolver_entries"] = [x["name"] for x in said["removed"]
                               if x["kind"] == "resolver-entry"]
    return out


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
