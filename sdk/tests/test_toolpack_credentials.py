"""Credentialed toolpacks — Slice A: declared env_requirements at install/run.

Everything here is NAMES/presence only; no test ever handles a secret value.
"""

from agentnode_sdk.lock_integrity import CANONICAL_FIELDS, SENSITIVE_FIELDS
from agentnode_sdk.runtimes.toolpack_credentials import (
    declared_env_names,
    missing_env_message,
    missing_required_env,
    required_env_names,
)

REQS = [
    {"name": "AHREFS_API_KEY", "required": True, "description": "Ahrefs API key"},
    {"name": "OPTIONAL_TOKEN", "required": False},
    {"name": "AHREFS_API_KEY"},  # duplicate collapses
    "garbage",  # non-dict ignored
    {"required": True},  # nameless ignored
]


def test_declared_and_required_names():
    entry = {"env_requirements": REQS}
    assert declared_env_names(entry) == ["AHREFS_API_KEY", "OPTIONAL_TOKEN"]
    assert required_env_names(entry) == ["AHREFS_API_KEY"]


def test_missing_required_flag_defaults_to_required():
    assert required_env_names({"env_requirements": [{"name": "X"}]}) == ["X"]


def test_missing_required_env(monkeypatch):
    monkeypatch.delenv("AHREFS_API_KEY", raising=False)
    assert missing_required_env({"env_requirements": REQS}) == ["AHREFS_API_KEY"]
    monkeypatch.setenv("AHREFS_API_KEY", "value")
    assert missing_required_env({"env_requirements": REQS}) == []


def test_empty_and_absent_entries():
    assert missing_required_env({}) == []
    assert missing_required_env(None) == []
    assert declared_env_names({"env_requirements": []}) == []


def test_message_is_actionable_and_value_free():
    msg = missing_env_message("ahrefs-pack", ["AHREFS_API_KEY"])
    assert "AHREFS_API_KEY" in msg
    assert "ahrefs-pack" in msg
    assert "env_requirements" in msg


# ---------------------------------------------------------------------------
# Run gate: run_python blocks with a clear error before any dispatch
# ---------------------------------------------------------------------------


def _entry(**overrides):
    e = {
        "trust_level": "community",
        "runtime": "python",
        "version": "1.0.0",
        "env_requirements": [{"name": "NEEDED_KEY", "required": True}],
    }
    e.update(overrides)
    return e


def test_run_blocks_on_missing_required_env(monkeypatch):
    from tests.hostpolicy import run_python

    monkeypatch.delenv("NEEDED_KEY", raising=False)
    res = run_python("cred-pack", None, entry=_entry())
    assert res.success is False
    assert res.mode_used == "credentials_missing"
    assert "NEEDED_KEY" in (res.error or "")


def test_run_gate_applies_to_host_trust_too(monkeypatch):
    from tests.hostpolicy import run_python

    monkeypatch.delenv("NEEDED_KEY", raising=False)
    res = run_python("cred-pack", None, entry=_entry(trust_level="trusted"))
    assert res.success is False
    assert res.mode_used == "credentials_missing"


def test_optional_missing_does_not_block(monkeypatch):
    from tests.hostpolicy import run_python

    monkeypatch.delenv("OPT_KEY", raising=False)
    entry = _entry(env_requirements=[{"name": "OPT_KEY", "required": False}])
    res = run_python("cred-pack", None, entry=entry)
    # Passes the credentials gate; fails later at the sandbox volume gate
    # (not installed) — but NOT with credentials_missing.
    assert res.mode_used != "credentials_missing"


def test_present_required_env_passes_gate(monkeypatch):
    from tests.hostpolicy import run_python

    monkeypatch.setenv("NEEDED_KEY", "some-value")
    res = run_python("cred-pack", None, entry=_entry())
    assert res.mode_used != "credentials_missing"


# ---------------------------------------------------------------------------
# Lockfile integrity: the declaration is sealed
# ---------------------------------------------------------------------------


def test_env_requirements_is_integrity_covered():
    assert "env_requirements" in CANONICAL_FIELDS
    assert "env_requirements" in SENSITIVE_FIELDS
