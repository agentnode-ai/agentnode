"""Where the worker is, as something somebody wrote down -- not as something inferred.

`worker/tls.py` used to settle this on its own: any address that was not a literal loopback IP
was refused, with the message "crossing a machine boundary is a separate decision, not a
setting". That was right about the principle and it is kept here. What it could not do was let
the decision ever be TAKEN.

So the decision is now expressible, and it is expressible in exactly one way: the topology is
DECLARED, and the address must agree with the declaration. Neither alone is enough.

    single-host-development   a unix socket, or `tcps://` to a literal loopback address
    separate-worker-host      `tcps://` to a literal address that is NOT loopback

A declaration that disagrees with its address is refused at startup, on both sides, naming both.
That is the point of the pairing: an address is a thing that can be mistyped, and a mistyped
address must not be able to move the security boundary. Somebody has to have written down that
the worker is on another machine before a connection to another machine is possible, and then
the address has to match what they wrote.

## Why a name is still refused

A DNS name resolves to wherever its owner points it, and it can be repointed without anybody
touching this deployment's configuration. The address is therefore a literal IP in both
topologies. The certificate is what establishes WHO answered -- the address only establishes
where this side looked -- but an address that can be moved underneath you turns "who answered"
into a question asked of a different machine than the operator believes. See `pki/identity.py`
for what actually proves identity.

## Why there is no fall back

Once `separate-worker-host` is declared there is no unix socket and no in-process worker to fall
back TO: those address classes are not permitted under that declaration, so a failure to reach
the remote worker is a failure, not a quiet switch to a worker on this machine. A fallback would
be the worst possible behaviour here -- it would run foreign code on the control plane's kernel
precisely when the machine that was supposed to run it could not be reached.

This module deliberately does not import anything from `gateway`. It is one of the pieces that
has to be installable on a machine that has no control plane on it at all.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

#: The two arrangements, as `worker/__init__.py` names them. Imported from there rather than
#: restated, so there is one list.
from agentnode_sdk.worker import SEPARATE_WORKER_HOST, SINGLE_HOST_DEVELOPMENT, TOPOLOGIES

#: The configuration key that carries the declaration, on the gateway side.
DECLARED_KEY = "worker_topology"

#: What an address IS, before any question of whether it is allowed.
IN_PROCESS = "in-process"
UNIX = "unix"
LOOPBACK_TCPS = "loopback-tcps"
REMOTE_TCPS = "remote-tcps"

#: Which address classes each declaration permits. A class not listed is refused, so adding a
#: transport later is a deliberate edit here rather than something that starts working by itself.
PERMITTED = {
    SINGLE_HOST_DEVELOPMENT: (IN_PROCESS, UNIX, LOOPBACK_TCPS),
    SEPARATE_WORKER_HOST: (REMOTE_TCPS,),
}


class TopologyRefused(Exception):
    """A declaration and an address that do not belong together, or a missing declaration.

    Carries `cause` so a caller can tell the cases apart without reading English, and
    `what_to_do` because a refusal at startup that does not say what to change is an outage with
    extra steps.
    """

    def __init__(self, cause: str, because: str, what_to_do: str) -> None:
        super().__init__(because)
        self.cause = cause
        self.because = because
        self.what_to_do = what_to_do


#: The causes, as stable strings. `R12` asks for each distinct cause to be distinguishable
#: without reading the sentence, and these are what a refusal is keyed on.
NOT_DECLARED = "topology_not_declared"
UNKNOWN_TOPOLOGY = "topology_not_recognised"
ADDRESS_UNUSABLE = "address_unusable"
WILDCARD = "address_is_every_interface"
DISAGREES = "topology_disagrees_with_address"


def is_loopback(host: str) -> bool:
    """True only for a literal address that cannot leave the machine.

    A local copy of the same judgement `gateway/transport.py` makes, on purpose: this module is
    part of what a worker host installs, and a worker host has no gateway package on it. Unlike
    that one, this does NOT accept the name "localhost" -- in this file an address is a literal
    or it is refused, so there is no name to make an exception for.
    """
    h = (host or "").strip().strip("[]")
    if not h:
        return False
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def address_class(address: str) -> str:
    """What kind of address this is. Raises `TopologyRefused` if it is not one this build has.

    An empty address means the worker is in this process -- the default build, where there is no
    transport at all.
    """
    if not (address or "").strip():
        return IN_PROCESS
    parsed = urlparse(address)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("unix", "unix+stream"):
        return UNIX
    if scheme != "tcps":
        raise TopologyRefused(
            ADDRESS_UNUSABLE,
            "a worker address is a unix socket or a tcps:// address, and %r is neither."
            % (address[:60],),
            "Set worker_address to unix:///run/agentnode/worker.sock for a worker on this "
            "machine, or tcps://<literal-ip>:<port> for one on another machine.")
    host = (parsed.hostname or "").strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise TopologyRefused(
            ADDRESS_UNUSABLE,
            "a worker address names a literal IP address, and %r is a name. A name resolves to "
            "wherever its owner points it, which is not a thing to decide a machine boundary on."
            % (host,),
            "Put the worker's literal IP address in worker_address, not its name.") from None
    if parsed.port is None:
        raise TopologyRefused(
            ADDRESS_UNUSABLE,
            "a tcps:// worker address names its port, and %r does not." % (address[:60],),
            "Write the address as tcps://<literal-ip>:<port>.")
    # EVERY INTERFACE IS NOT AN ADDRESS. 0.0.0.0 and :: are refused in BOTH topologies, and the
    # reason is the listener rather than the client: a worker that binds every interface is
    # reachable from wherever the machine is reachable from, which on a cloud host means the
    # public internet. The whole point of the remote arrangement is that the worker answers on
    # one private address and nowhere else, so the wildcard is not a permissive setting here --
    # it is the one bind that would undo the arrangement.
    if ipaddress.ip_address(host).is_unspecified:
        raise TopologyRefused(
            WILDCARD,
            "%r means every interface, which is not an address a worker may be reached at or "
            "listen on. A worker answers on one address." % (host,),
            "Name the one address the worker should answer on -- its private address on the "
            "network it shares with the control plane, or a loopback address for a worker on "
            "this machine.")
    return LOOPBACK_TCPS if is_loopback(host) else REMOTE_TCPS


def check(declared: str, address: str, *, where: str = "this side") -> str:
    """Judge a declaration against an address. Returns the address class, or refuses.

    `where` names the side doing the checking, because both do it independently and a refusal
    that does not say which one is speaking is harder to act on than it needs to be.
    """
    kind = address_class(address)

    #: An in-process worker is the default build and declares nothing: there is no transport, no
    #: address and no boundary. Requiring a declaration for it would break every existing
    #: installation to no purpose.
    if kind == IN_PROCESS and not (declared or "").strip():
        return kind

    if not (declared or "").strip():
        raise TopologyRefused(
            NOT_DECLARED,
            "%s has a worker address configured (%r) but no %s, so nothing says whether the "
            "worker is meant to be on this machine or another one."
            % (where, (address or "")[:60], DECLARED_KEY),
            'Add "%s": "%s" for a worker on this machine, or "%s" for one on another machine. '
            "It is written down rather than worked out from the address, so that changing the "
            "address cannot move the boundary on its own." % (
                DECLARED_KEY, SINGLE_HOST_DEVELOPMENT, SEPARATE_WORKER_HOST))

    if declared not in TOPOLOGIES:
        raise TopologyRefused(
            UNKNOWN_TOPOLOGY,
            "%s declares the topology %r, which this build does not have." % (where, declared),
            "Use one of: %s." % ", ".join(TOPOLOGIES))

    allowed = PERMITTED[declared]
    if kind not in allowed:
        raise TopologyRefused(
            DISAGREES,
            "%s declares %r, which is served over %s, and the configured address is %s (%r). "
            "The declaration and the address have to agree: a machine boundary is crossed "
            "because somebody decided it, and then only to the address they decided on."
            % (where, declared, _listed(allowed), kind, (address or "")[:60]),
            _what_to_do(declared, kind))
    return kind


def _listed(items) -> str:
    """"a, b or c" -- so a refusal reads as a sentence rather than as a tuple."""
    items = list(items)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " or " + items[-1]


def _what_to_do(declared: str, kind: str) -> str:
    if declared == SEPARATE_WORKER_HOST and kind in (IN_PROCESS, UNIX, LOOPBACK_TCPS):
        return (
            "Either point worker_address at the other machine's literal IP address, or -- if "
            "the worker really is on this machine -- change %s to %r. Do not leave it as it is: "
            "a worker on this machine under a %r declaration would run foreign code on the "
            "control plane's kernel while every record said otherwise."
            % (DECLARED_KEY, SINGLE_HOST_DEVELOPMENT, SEPARATE_WORKER_HOST))
    if declared == SINGLE_HOST_DEVELOPMENT and kind == REMOTE_TCPS:
        return (
            "Either point worker_address back at this machine, or -- if the worker really is on "
            "another machine -- change %s to %r, which is a decision about where foreign code "
            "runs and should be taken deliberately." % (DECLARED_KEY, SEPARATE_WORKER_HOST))
    return "Make %s and worker_address describe the same arrangement." % DECLARED_KEY


def declared_for(address: str) -> str:
    """The declaration an address WOULD need. For an installer or a diagnostic to suggest -- it
    is deliberately not used to supply a missing declaration, because that would be inferring
    the boundary again."""
    kind = address_class(address)
    for topology, allowed in PERMITTED.items():
        if kind in allowed:
            return topology
    return ""
