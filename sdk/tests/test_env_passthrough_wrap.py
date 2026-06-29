"""Stage 3B-2a: name-only secret pass-through in the REAL wrap_command argv (inert; pure argv).

Proves the product mechanism — `--env NAME` (never KEY=value), egress-only, disjoint from literal
env, validated names — WITHOUT any live secret flow (wrap_command is pure argv construction).
"""
from __future__ import annotations

import pytest

from agentnode_sdk.sandbox.container_backend import ContainerBackend
from agentnode_sdk.sandbox.types import EgressSpec, ProcessSpec, SandboxRequiredError


def _be():
    return ContainerBackend(runtime="docker")


def _egress():
    return EgressSpec(
        network_name="agentnode-egress-tok-int",
        proxy_url="http://egress-proxy:8888",
        allowed_domains=("api.github.com",),
    )


def _spec(**over):
    kw = dict(command=["python", "-c", "x"], network="egress", egress=_egress(), name="c")
    kw.update(over)
    return ProcessSpec(**kw)


def test_env_passthrough_emits_name_only():
    argv = _be().wrap_command(_spec(env_passthrough=["GITHUB_TOKEN"]))
    assert "--env" in argv
    i = argv.index("--env")
    assert argv[i + 1] == "GITHUB_TOKEN"          # NAME only
    assert "GITHUB_TOKEN=" not in " ".join(argv)  # never KEY=value


def test_secret_value_never_emitted_for_passthrough():
    argv = _be().wrap_command(_spec(env_passthrough=["SECRET_X"]))
    assert "--env" in argv and argv[argv.index("--env") + 1] == "SECRET_X"
    assert all("SECRET_X=" not in a for a in argv)  # no value form anywhere on argv


@pytest.mark.parametrize("net", ["none", "restricted", "default"])
def test_env_passthrough_requires_egress(net):
    spec = ProcessSpec(command=["x"], network=net, env_passthrough=["TOK"], name="c")
    with pytest.raises(SandboxRequiredError):
        _be().wrap_command(spec)


def test_env_passthrough_disjoint_from_literal_env():
    spec = _spec(env={"TOK": "literal-value"}, env_passthrough=["TOK"])
    with pytest.raises(SandboxRequiredError):
        _be().wrap_command(spec)


@pytest.mark.parametrize("bad", ["1ABC", "A-B", "A B", "", "FOO=BAR", "a.b", "TÖKEN"])
def test_env_passthrough_invalid_name_refused(bad):
    with pytest.raises(SandboxRequiredError):
        _be().wrap_command(_spec(env_passthrough=[bad]))


def test_invalid_name_error_is_value_free():
    # A caller might mistakenly pass a secret VALUE instead of a NAME — the error must NOT echo it.
    secretish = "ghp_secret-value-that-must-not-appear"
    with pytest.raises(SandboxRequiredError) as ei:
        _be().wrap_command(_spec(env_passthrough=[secretish]))
    msg = str(ei.value)
    assert secretish not in msg
    assert "ghp_" not in msg and "secret-value" not in msg


def test_invalid_nonstring_error_is_value_free():
    # A non-str entry must not leak via repr() either.
    sentinel = "ghp_nonstring_secret_must_not_appear"

    class _Leaky:
        def __repr__(self):
            return sentinel

        def __str__(self):
            return sentinel

    with pytest.raises(SandboxRequiredError) as ei:
        _be().wrap_command(_spec(env_passthrough=[_Leaky()]))
    assert sentinel not in str(ei.value)
    assert "ghp_" not in str(ei.value)


def test_env_passthrough_dedup():
    argv = _be().wrap_command(_spec(env_passthrough=["TOK", "TOK", "TOK"]))
    assert argv.count("TOK") == 1  # a single `--env TOK`


def test_multiple_names_each_emitted():
    argv = _be().wrap_command(_spec(env_passthrough=["A_TOKEN", "B_KEY"]))
    j = " ".join(argv)
    assert "--env A_TOKEN" in j and "--env B_KEY" in j
    assert "A_TOKEN=" not in j and "B_KEY=" not in j


def test_no_env_passthrough_means_no_env_flag():
    argv = _be().wrap_command(ProcessSpec(command=["x"], network="none", name="c"))
    assert "--env" not in argv
