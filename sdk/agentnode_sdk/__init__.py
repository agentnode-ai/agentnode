"""AgentNode Python SDK — discover, resolve, and install AI agent capabilities."""

# THE EXCEPTION TYPES FIRST, and outside the guard below, because they need nothing but the
# standard library and the refusal this module may have to raise has to be a real AgentNodeError
# rather than something invented on the spot.
from agentnode_sdk.exceptions import (
    AgentNodeError,
    AgentNodeToolError,
    AuthError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)

#: WHICH DEPENDENCY THIS INSTALLATION IS MISSING, or "" when nothing is.
#:
#: WHY THIS EXISTS. `cli/main.py` does `import agentnode_sdk`, and importing any name of this
#: package executes this file -- which imports the client, the async client and the installer, each
#: of which imports `httpx` at module scope. On a host whose install was interrupted after the
#: package arrived and before its dependencies did, that raised `ModuleNotFoundError` three imports
#: deep and the WHOLE CLI died before argparse existed. The third beta acceptance measured exactly
#: that: `agentnode gateway doctor` -- the published way to ask whether a host is ready -- answered
#: with a traceback. A host that cannot say it is not ready is PR7 of that profile at WARN.
_MISSING_DEPENDENCY = ""
# EVERYTHING THAT CAN NEED A THIRD-PARTY PACKAGE, under one guard.
#
# `ModuleNotFoundError` ONLY, and re-raised at once when the missing module is one of ours. A
# missing third-party package means the dependencies never arrived -- an incomplete installation,
# which this package can report. A missing `agentnode_sdk` submodule means this BUILD is wrong, and
# swallowing that would hide the one class of error that must never be hidden.
try:
    from agentnode_sdk.async_client import AsyncAgentNode
    from agentnode_sdk.client import AgentNode, AgentNodeClient
    from agentnode_sdk.config import load_config, config_path, installation_behavior_label
    from agentnode_sdk.detect import detect_gap
    from agentnode_sdk.installer import load_tool
    from agentnode_sdk.skill import load_skill, SkillContext, SkillAsset
    from agentnode_sdk.models import (
        CanInstallResult,
        DetectAndInstallResult,
        DetectedGap,
        InstallMetadata,
        InstallResult,
        PackageDetail,
        ResolvedPackage,
        ResolveResult,
        RunToolResult,
        SearchHit,
        SearchResult,
        SmartRunResult,
    )
    from agentnode_sdk.compatibility import recommend_model
    from agentnode_sdk.risk_profile import RiskProfile, compute_risk_profile, get_risk_profile
    from agentnode_sdk.policy import PolicyResult, check_install, check_run
    from agentnode_sdk.runner import run_tool
    from agentnode_sdk.runtime import AgentNodeRuntime

    # Convenience aliases
    Client = AgentNodeClient
    ToolError = AgentNodeToolError
except ModuleNotFoundError as _missing:                       # noqa: BLE001
    _name = str(getattr(_missing, "name", "") or "")
    if _name.split(".")[0] == "agentnode_sdk":
        raise
    _MISSING_DEPENDENCY = _name or str(_missing)

#: THE ONE PLACE THE VERSION IS WRITTEN. `pyproject.toml` builds the wheel's version from this
#: line rather than carrying its own copy, because it did carry one: the project said 0.25.0
#: while this said 0.24.1, so the gateway announced 0.24.1 to every client it answered while
#: running the 0.25.0 build. Two copies of one number drift, and the drift is invisible until
#: something reports the wrong one.
__version__ = "0.25.0"
__all__ = [
    "AgentNode",
    "AsyncAgentNode",
    "AgentNodeClient",
    "Client",
    "load_config",
    "config_path",
    "installation_behavior_label",
    "detect_gap",
    "load_tool",
    "load_skill",
    "SkillContext",
    "SkillAsset",
    "run_tool",
    "AgentNodeError",
    "AgentNodeToolError",
    "ToolError",
    "NotFoundError",
    "AuthError",
    "RateLimitError",
    "ValidationError",
    "PackageDetail",
    "SearchResult",
    "SearchHit",
    "ResolveResult",
    "ResolvedPackage",
    "InstallMetadata",
    "InstallResult",
    "CanInstallResult",
    "RunToolResult",
    "DetectedGap",
    "DetectAndInstallResult",
    "SmartRunResult",
    "AgentNodeRuntime",
    "PolicyResult",
    "check_install",
    "check_run",
    "recommend_model",
    "RiskProfile",
    "compute_risk_profile",
    "get_risk_profile",
]


def what_this_installation_is_missing() -> str:
    """The third-party package this installation needs and does not have, or "".

    Read by the readiness paths -- `gateway doctor`, `gateway status`, `worker preflight` -- so that
    a host whose install was interrupted can SAY SO in words and in a machine-readable form, rather
    than dying three imports deep on a package nobody asked it about.
    """
    return _MISSING_DEPENDENCY


if _MISSING_DEPENDENCY:
    # BIND EVERY PUBLIC NAME THAT DID NOT GET ONE, to something that explains itself.
    #
    # Driven by `__all__` rather than by a hand-written list, so the stub set cannot drift from the
    # public API -- a name added above and forgotten here would be an `AttributeError` on an
    # incomplete installation instead of the sentence this is for.
    #
    # A STUB THAT RAISES, never one that returns something. A silent no-op would be worse than the
    # traceback it replaces: the traceback at least said what was wrong.
    def _needs_the_missing_package(_name):
        def refuse(*_args, **_kwargs):
            # `AgentNodeError(code, message)`, which is the signature it has. The first version of
            # this passed one string and the stub raised `TypeError` instead of saying anything.
            raise AgentNodeError(
                "installation_incomplete",
                "%s needs the %r package, which this installation does not have. The install "
                "was interrupted before its dependencies arrived; re-run the installer for this "
                "host, or `pip install %s`. Ask this host what it is missing with "
                "`agentnode gateway doctor`."
                % (_name, _MISSING_DEPENDENCY, _MISSING_DEPENDENCY))
        refuse.__name__ = str(_name)
        refuse.__doc__ = ("Unavailable: this installation is missing %r."
                          % _MISSING_DEPENDENCY)
        return refuse

    for _public in __all__:
        if _public not in globals():
            globals()[_public] = _needs_the_missing_package(_public)
    del _public
