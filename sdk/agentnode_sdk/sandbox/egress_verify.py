"""Measure the boundary before the first foreign process runs, and refuse the run if it is not there.

## Why this exists

An earlier version of the pre-start check asked three questions -- no default route, a resolver that is
only the proxy, the proxy answering -- and an independent review was right that all three can pass while an
unintended path exists. A missing default route is a SYMPTOM of the boundary; it is not the boundary.

So this exercises the negative paths. It starts one short-lived container on the payload's own network,
with the payload's own image and no capability, and tries the things that must not work. If any of them
works, or if the probe cannot be run at all, the run is refused: an unmeasurable boundary is not a
boundary, and a boundary that is measured and absent is worse than one nobody looked at.

## What it costs, said plainly

One extra container start per egress job, a few seconds. Every destination it tries is refused by the
kernel in microseconds when the topology is right, so the cost is the container start and not the probes.

## What it cannot establish

It measures the namespace the payload is ABOUT to get, from inside a sibling container on the same
network -- which is the same namespace arrangement, not the same namespace. A runtime that assigned the
payload a different network than the one it was told to would defeat it, which is why
``start_egress_proxy`` separately reads the membership back by id, and why the payload's own network
argument comes from the same handle this verified.
"""
from __future__ import annotations

import json
import os
import subprocess

from agentnode_sdk.sandbox.types import SandboxRequiredError

#: The probe. It runs inside the payload's network, prints one JSON object, and exits. It is embedded
#: rather than mounted: a mounted file is subject to the uid the image runs as and to SELinux relabelling,
#: and standard input is subject to neither.
PROBE = r'''
import json, os, socket
def connect(host, port, family=socket.AF_UNSPEC, kind=socket.SOCK_STREAM, send=None):
    try:
        infos = socket.getaddrinfo(host, port, family, kind)
    except Exception as exc:
        return {"outcome": "no-dns", "detail": repr(exc)}
    for af, sort, proto, _c, addr in infos:
        s = socket.socket(af, sort, proto)
        s.settimeout(2.0)
        try:
            if kind == socket.SOCK_DGRAM:
                s.sendto(send or b"\x00", addr)
                s.recv(256)
                return {"outcome": "answered", "detail": str(addr)}
            s.connect(addr)
            return {"outcome": "connected", "detail": str(addr)}
        except socket.timeout:
            return {"outcome": "silent", "detail": str(addr)}
        except Exception as exc:
            last = {"outcome": "refused", "detail": "%s %r" % (addr, exc)}
        finally:
            try:
                s.close()
            except Exception:
                pass
    return last
A_DNS_QUERY = bytes([0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1])
PROXY = os.environ.get("AGENTNODE_VERIFY_PROXY", "")
GATEWAY = os.environ.get("AGENTNODE_VERIFY_GATEWAY", "")
said = {"must_fail": {}, "must_work": {}}
said["must_fail"]["public_ipv4"] = connect("1.1.1.1", 443, socket.AF_INET)
said["must_fail"]["public_ipv6"] = connect("2606:4700:4700::1111", 443, socket.AF_INET6)
said["must_fail"]["ipv4_mapped_ipv6"] = connect("::ffff:1.1.1.1", 443)
said["must_fail"]["udp_resolver"] = connect("1.1.1.1", 53, socket.AF_UNSPEC,
                                           socket.SOCK_DGRAM, A_DNS_QUERY)
said["must_fail"]["cloud_metadata"] = connect("169.254.169.254", 80, socket.AF_INET)
said["must_fail"]["a_public_name"] = connect("pypi.org", 443)
if GATEWAY:
    said["must_fail"]["the_networks_gateway"] = connect(GATEWAY, 53, socket.AF_INET,
                                                        socket.SOCK_DGRAM, A_DNS_QUERY)
if PROXY:
    host, _, port = PROXY.rpartition(":")
    host = host.replace("http://", "")
    said["must_work"]["the_proxy"] = connect(host, int(port or 8888), socket.AF_INET)
print("AGENTNODE_VERIFY " + json.dumps(said))
'''

#: An outcome that means the destination was NOT reached. "silent" is not among them on purpose: a send
#: that neither failed nor answered says nothing, and nothing is not a refusal.
_REFUSALS = ("refused", "no-dns")


def verify_the_boundary(handle, *, backend=None, timeout: float = 30.0) -> tuple:
    """Return readings as pairs. Raise SandboxRequiredError if the boundary is not measurably there."""
    from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE, _HARDENED_FLAGS

    rt = handle.runtime
    gateway = ""
    for name, value in (handle.readings or ()):
        if name == "internal_network_subnets":
            try:
                for entry in json.loads(value or "[]"):
                    gateway = gateway or str(entry.get("gateway") or "")
            except ValueError:
                pass
    argv = [rt, "run", "--rm", "-i", "--network", handle.int_net]
    argv += list(_HARDENED_FLAGS)
    argv += ["-e", "AGENTNODE_VERIFY_PROXY=" + str(handle.spec.proxy_url or ""),
             "-e", "AGENTNODE_VERIFY_GATEWAY=" + gateway,
             _BASE_IMAGE, "python", "-"]
    try:
        done = subprocess.run(argv, input=PROBE, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:                                      # noqa: BLE001
        raise SandboxRequiredError(
            "the boundary could not be measured at all (%r), so this run is refused rather than started "
            "on a route nobody checked" % (exc,)) from exc
    line = next((ln for ln in (done.stdout or "").splitlines()
                 if ln.startswith("AGENTNODE_VERIFY ")), "")
    if not line:
        raise SandboxRequiredError(
            "the boundary probe produced no reading (exit %s), so this run is refused: an unmeasurable "
            "boundary is not a boundary. stderr: %s"
            % (done.returncode, (done.stderr or "")[-400:]))
    try:
        said = json.loads(line[len("AGENTNODE_VERIFY "):])
    except ValueError as exc:
        raise SandboxRequiredError("the boundary probe's reading was unreadable: %r" % (exc,)) from exc

    reached = [name for name, got in (said.get("must_fail") or {}).items()
               if str(got.get("outcome")) not in _REFUSALS]
    worked = said.get("must_work") or {}
    if reached:
        raise SandboxRequiredError(
            "this run is refused: from the network it would have been given, %s was reachable. "
            "The readings: %s" % (", ".join(sorted(reached)), json.dumps(said, sort_keys=True)))
    if "the_proxy" in worked and str(worked["the_proxy"].get("outcome")) != "connected":
        raise SandboxRequiredError(
            "this run is refused: the one way out it was given does not answer. The readings: %s"
            % json.dumps(said, sort_keys=True))
    return (
        ("boundary_probe_exit", done.returncode),
        ("boundary_probe_runtime", os.path.basename(str(rt or ""))),
        ("boundary_must_fail", json.dumps(said.get("must_fail") or {}, sort_keys=True)),
        ("boundary_must_work", json.dumps(worked, sort_keys=True)),
    )
