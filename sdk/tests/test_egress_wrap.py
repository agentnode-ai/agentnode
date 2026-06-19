"""Stage 1 (INERT): unit tests for the egress ProcessSpec/wrap_command mode.

Pure argv construction — NO docker daemon. Proves Design A (proven in Stage 0A) is
modeled at the spec/argv layer and that wrap_command stays fail-closed:
an incomplete egress handle, OR any unknown network mode, MUST raise — never yield
an open-network argv. Nothing here starts a proxy or a container (that is Stage 2),
and no product code path passes network="egress" yet (mcp_runner is untouched).
"""
from __future__ import annotations

import pytest

from agentnode_sdk.sandbox.container_backend import ContainerBackend
from agentnode_sdk.sandbox.types import EgressSpec, ProcessSpec, SandboxRequiredError

_HARDENED = (
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--read-only",
)


def _be() -> ContainerBackend:
    # force the runtime so wrap_command is pure (no daemon probe / no pull)
    return ContainerBackend(runtime="docker")


def _egress_spec(**over) -> ProcessSpec:
    kw = dict(
        command=["python", "-c", "print(1)"],
        network="egress",
        egress=EgressSpec(
            network_name="agentnode-egress-spike-int-x",
            proxy_url="http://spikeproxy:8888",
            allowed_domains=("api.example.com",),
        ),
    )
    kw.update(over)
    return ProcessSpec(**kw)


def test_egress_emits_internal_network_and_controlled_proxy_env():
    argv = _be().wrap_command(_egress_spec())
    # joins the pre-created internal network (topological enforcement)
    assert "--network" in argv
    assert "agentnode-egress-spike-int-x" in argv
    # controlled proxy env present (upper + lower case); NO_PROXY emptied
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert f"{var}=http://spikeproxy:8888" in argv
    assert "NO_PROXY=" in argv and "no_proxy=" in argv


def test_egress_missing_egress_raises():
    with pytest.raises(SandboxRequiredError):
        _be().wrap_command(ProcessSpec(command=["x"], network="egress", egress=None))


def test_egress_empty_network_name_raises():
    spec = ProcessSpec(command=["x"], network="egress",
                       egress=EgressSpec(network_name="", proxy_url="http://p:8888"))
    with pytest.raises(SandboxRequiredError):
        _be().wrap_command(spec)


def test_egress_empty_proxy_url_raises():
    spec = ProcessSpec(command=["x"], network="egress",
                       egress=EgressSpec(network_name="net", proxy_url=""))
    with pytest.raises(SandboxRequiredError):
        _be().wrap_command(spec)


def test_egress_keeps_hardening():
    argv = _be().wrap_command(_egress_spec())
    for flag in _HARDENED:
        assert flag in argv
    assert "--user" in argv and "1000:1000" in argv


def test_egress_never_falls_back_to_open_network():
    # an incomplete handle must RAISE, never silently produce an open-network argv
    with pytest.raises(SandboxRequiredError):
        _be().wrap_command(ProcessSpec(command=["x"], network="egress", egress=None))


def test_unknown_network_mode_raises():
    # the second correction: anything outside the explicit set is fail-closed
    with pytest.raises(SandboxRequiredError):
        _be().wrap_command(ProcessSpec(command=["x"], network="bogus"))


def test_default_network_is_open_no_flag():
    # "default" stays an explicit, allowed open mode (no --network flag)
    argv = _be().wrap_command(ProcessSpec(command=["x"], network="default"))
    assert "--network" not in argv


def test_egress_ignores_caller_supplied_proxy_env():
    spec = _egress_spec(env={
        "HTTP_PROXY": "http://evil:9999",
        "HTTPS_PROXY": "http://evil:9999",
        "NO_PROXY": "evil.test",
        "SOME_KEY": "ok",
    })
    argv = _be().wrap_command(spec)
    # caller-supplied proxy values must NOT leak through; controlled ones win
    assert "HTTP_PROXY=http://evil:9999" not in argv
    assert "HTTPS_PROXY=http://evil:9999" not in argv
    assert "NO_PROXY=evil.test" not in argv
    assert "HTTP_PROXY=http://spikeproxy:8888" in argv
    assert "NO_PROXY=" in argv
    # non-proxy env still passes through untouched
    assert "SOME_KEY=ok" in argv
