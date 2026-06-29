"""Stage 3B-2a: interactive consent-prompt UI (cli_consent_callback). Returns (approved, lifetime).

Shows env-key NAMES + sealed domains + presets — never a secret value. INERT: the prompt exists +
is tested but is NOT wired into the live MCP start path (that is 3B-2b).
"""
from __future__ import annotations

import builtins

import pytest

from agentnode_sdk.cli.mcp_commands import cli_consent_callback
from agentnode_sdk.runtimes import mcp_consent_store as store
from agentnode_sdk.runtimes.mcp_consent import build_consent_identity


def _id():
    return build_consent_identity(
        "gh-mcp", "1.2.3", "sha256:" + "a" * 64, ["GITHUB_TOKEN"], ["api.github.com"])


def _feed(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(it))


@pytest.mark.parametrize("ans,expected", [
    ("1", store.LIFETIME_THIS_RUN),
    ("2", store.LIFETIME_7D),
    ("3", store.LIFETIME_30D),
    ("4", store.LIFETIME_90D),
    ("", store.LIFETIME_90D),  # empty == default
])
def test_preset_choices(monkeypatch, ans, expected):
    _feed(monkeypatch, [ans])
    approved, lifetime = cli_consent_callback(_id())
    assert approved is True and lifetime == expected


def test_default_is_90d_never_forever(monkeypatch):
    _feed(monkeypatch, [""])
    approved, lifetime = cli_consent_callback(_id())
    assert approved is True
    assert lifetime == store.LIFETIME_90D
    assert lifetime != store.LIFETIME_FOREVER


def test_deny(monkeypatch):
    _feed(monkeypatch, ["n"])
    assert cli_consent_callback(_id()) == (False, store.DEFAULT_LIFETIME)


def test_forever_requires_explicit_confirmation(monkeypatch):
    _feed(monkeypatch, ["5", "forever"])
    approved, lifetime = cli_consent_callback(_id())
    assert approved is True and lifetime == store.LIFETIME_FOREVER


def test_forever_unconfirmed_denies(monkeypatch):
    _feed(monkeypatch, ["5", "yes"])  # not the literal 'forever'
    assert cli_consent_callback(_id()) == (False, store.DEFAULT_LIFETIME)


def test_unrecognized_answer_denies(monkeypatch):
    _feed(monkeypatch, ["99"])
    assert cli_consent_callback(_id()) == (False, store.DEFAULT_LIFETIME)


def test_eof_denies(monkeypatch):
    def _raise(*a, **k):
        raise EOFError
    monkeypatch.setattr(builtins, "input", _raise)
    assert cli_consent_callback(_id()) == (False, store.DEFAULT_LIFETIME)


def test_prompt_shows_names_and_domains_no_secret_value(monkeypatch, capsys):
    _feed(monkeypatch, ["4"])
    cli_consent_callback(_id())
    err = capsys.readouterr().err
    assert "GITHUB_TOKEN" in err        # env-key NAME shown
    assert "api.github.com" in err      # sealed domain shown
    assert "1.2.3" in err               # version shown
    # no secret value channel exists; assert no obvious secret patterns leak
    assert "ghp_" not in err and "Bearer " not in err
