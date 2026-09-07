"""Where the EM-3A contract stops being types and starts deciding runs.

`contract.py` carries the rule that a lower-precedence scope may only narrow. Until something
calls it, that rule protects nothing: an unimported module is not a policy. This is the caller.

The shape it enforces:

    granted = merge_policies({
        Scope.USER:    what the person running this allows,
        Scope.PACKAGE: what the package ASKS for,          # untrusted, a request
    })

`Scope.PACKAGE` is the lowest scope in the contract, so a package declaration can only ever
tighten what the user already permitted. That is the whole point: before this, the declared
``network_level`` in the lockfile *was* the grant, and the user's configuration could only veto
the run as a whole. Now it is an input to a fold that cannot widen.

What this module does NOT claim: it is a client-side composition. It decides what the local
sandbox is asked to enforce. There is no provider or operator scope yet, and no server re-decides
anything -- those arrive with EM-3C and EM-3E. Nothing here is a server-side guarantee.
"""
from __future__ import annotations

from agentnode_sdk.sandbox.contract import (
    NetworkRules,
    SandboxPolicy,
    Scope,
    merge_policies,
)

#: The declared levels a package may ask for. Anything else is not a narrower request that can
#: be honoured -- it is a value this build does not understand, and an unknown request is denied
#: rather than guessed at.
KNOWN_NETWORK_LEVELS = frozenset({"none", "restricted", "unrestricted"})

#: Spellings the CLI produces when it rewrites a manifest into lockfile form. They are
#: translation artefacts of that one step, never something a publisher declares, and they map to
#: exactly the level they were translated from.
#:
#: ``full`` and ``limited`` are deliberately NOT here. They were recognised by an older mapping
#: that granted every named level the same open network, so a package could declare either and be
#: given everything. They are refused now rather than aliased: silently reinterpreting a level
#: whose meaning was never enforced would carry that mistake forward under a new name.
_LEVEL_ALIASES = {"internal": "restricted", "external": "unrestricted"}


class NetworkRequestError(ValueError):
    """A package asked for something this build cannot turn into an enforceable request."""


def normalise_level(raw: object) -> str:
    """Map a declared value to one of the three levels, or raise.

    Missing and empty mean ``none``: the absence of a request is not a request for everything.
    An unrecognised value raises -- it is not silently treated as ``none`` either, because a
    package that declares something meaningless should be visibly refused rather than quietly
    run with less than it believes it has.
    """
    if raw is None:
        return "none"
    if not isinstance(raw, str):
        raise NetworkRequestError(
            f"network_level must be a string, not {type(raw).__name__}"
        )
    value = raw.strip().lower()
    if not value:
        return "none"
    value = _LEVEL_ALIASES.get(value, value)
    if value not in KNOWN_NETWORK_LEVELS:
        raise NetworkRequestError(
            f"network_level {raw!r} is not one of {sorted(KNOWN_NETWORK_LEVELS)}"
        )
    return value


def package_request(entry: dict | None) -> SandboxPolicy:
    """What the package ASKS for, read from its sealed lockfile permissions.

    This is `Scope.PACKAGE`: the lowest precedence there is. Whatever it says, the fold can only
    use it to tighten.
    """
    perms = ((entry or {}).get("permissions") or {}) if isinstance(entry, dict) else {}
    level = normalise_level(perms.get("network_level"))
    if level == "none":
        network = NetworkRules(enabled=False, allowed_destinations=frozenset())
    elif level == "unrestricted":
        network = NetworkRules(enabled=True, allowed_destinations=None)
    else:  # restricted
        # A restricted request without an enforceable allowlist is refused here, before anything
        # runs. It is NOT quietly turned into "no network": the package asked for something this
        # build cannot carry out, and saying so is the honest answer. The domains are put through
        # the same canonicaliser the credentialed path uses, so an unusable hostname -- an IP
        # literal, localhost, a single label -- is rejected at declaration time rather than
        # reaching the proxy.
        from agentnode_sdk.sandbox.domain_policy import (
            DomainPolicyError,
            canonicalize_allowed_domains,
        )

        raw_domains = perms.get("allowed_domains")
        if not isinstance(raw_domains, (list, tuple)) or not raw_domains:
            raise NetworkRequestError(
                "network_level 'restricted' requires a non-empty permissions.allowed_domains; "
                "without it there is no restriction to enforce"
            )
        try:
            domains = canonicalize_allowed_domains(list(raw_domains))
        except DomainPolicyError as exc:
            raise NetworkRequestError(
                f"permissions.allowed_domains is not usable: {exc}"
            ) from exc
        network = NetworkRules(enabled=True, allowed_destinations=frozenset(domains))
    return SandboxPolicy(network=network)


def user_policy(config: dict | None = None) -> SandboxPolicy:
    """What the person running this allows, at `Scope.USER`.

    Today the only network-relevant user setting is the ``permissions.network`` gate that
    :mod:`agentnode_sdk.policy` already enforces as an admission decision, so this scope starts
    permissive and lets `check_run` keep owning the veto. It exists as a real scope so that a
    narrowing user setting has somewhere to live the moment one is added -- and so the fold has a
    higher scope above PACKAGE from the start, rather than PACKAGE being alone and therefore
    unconstrained by construction.
    """
    cfg = config if isinstance(config, dict) else {}
    perms = cfg.get("permissions") or {}
    if perms.get("network") == "deny":
        return SandboxPolicy(network=NetworkRules(enabled=False,
                                                  allowed_destinations=frozenset()))
    return SandboxPolicy(network=NetworkRules(enabled=True, allowed_destinations=None))


def compose(entry: dict | None, config: dict | None = None) -> SandboxPolicy:
    """The granted policy: the user's, narrowed by what the package asked for."""
    return merge_policies({
        Scope.USER: user_policy(config),
        Scope.PACKAGE: package_request(entry),
    })


def network_mode(granted: SandboxPolicy) -> tuple[str, tuple[str, ...]]:
    """Turn the granted network policy into a `ProcessSpec.network` mode and its allowlist.

    Three outcomes, and they are observably different at the container:

    * ``("none", ())``    -- ``--network none``: no socket at all.
    * ``("egress", (…,))``-- an internal network with no route out, reachable only through a
      CONNECT proxy bound to exactly these hosts.
    * ``("default", ())`` -- the engine's default bridge: open outbound.

    An enabled network whose allowlist has been narrowed to nothing is NOT open, and it is not
    an error either: it is what the fold produces when a user permits ``{a}`` and a package asks
    for ``{b}``. There is genuinely nowhere left to reach, so it becomes ``none``. That is a
    different case from a package *declaring* ``restricted`` with no allowlist at all, which
    :func:`package_request` refuses outright -- one is a narrowing that arrived at nothing, the
    other is a declaration that never said anything enforceable.
    """
    net = granted.network
    if not net.enabled:
        return "none", ()
    if net.allowed_destinations is None:
        return "default", ()
    domains = tuple(sorted(net.allowed_destinations))
    if not domains:
        return "none", ()
    return "egress", domains
