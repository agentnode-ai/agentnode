"""UX-3A — setup wizard: optional LLM-credential screen + read-only sandbox
status. All input/getpass mocked; no real provider calls, no real keychain
(fake seam), no docker.
"""
from __future__ import annotations

import json
import types

import pytest

from agentnode_sdk import credential_store as cs
from agentnode_sdk.cli.setup_wizard import run_wizard
from agentnode_sdk.config import config_exists, load_config
from agentnode_sdk.sandbox.types import SandboxAvailability

SECRET = "sk-WIZARD-SECRET-xyz9"

# inputs consumed by the pre-credential screens:
# intro Enter, install behavior, network, filesystem, code_execution, advanced
_PRE = ["", "", "", "", "", ""]
_SAVE = [""]


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config.json"))
    # hermetic: no real env keys, no real ~/.agentnode/.env, no real keychain
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "agentnode_sdk.runtimes.agent_runner._load_agentnode_env", lambda: None)
    # wizard runs under pytest (no tty) — simulate an interactive terminal
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: True))
    # read-only sandbox check: default "not found", never a real backend
    monkeypatch.setattr(
        "agentnode_sdk.sandbox.get_default_backend",
        lambda: types.SimpleNamespace(check_available=lambda: SandboxAvailability(
            available=False, backend="none", reason="no container runtime")))
    yield


def _wire(monkeypatch, inputs, getpass_values=(), auth_test_rc=0):
    it = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
    gp = iter(getpass_values)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(gp))
    calls = []

    def fake_test(provider):
        calls.append(provider)
        return auth_test_rc

    monkeypatch.setattr("agentnode_sdk.cli.auth.cmd_auth_test", fake_test)
    return calls


def _use_fake_keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setitem(cs._keyring_state, "available", None)
    monkeypatch.setattr(cs, "_get_keyring_backend", lambda: fake)
    return fake


# --- happy paths ---------------------------------------------------------------

def test_happy_path_openai_keychain(monkeypatch, capsys):
    fake = _use_fake_keyring(monkeypatch)
    # choice 1, test? y(default ""), add another? n(default ""), save
    _wire(monkeypatch, _PRE + ["1", "", ""] + _SAVE, getpass_values=[SECRET])
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert SECRET not in out                              # never the full key
    assert "...xyz9" in out                               # masked tail
    assert "OS keychain" in out                           # honest label
    assert "encrypted" not in out.lower()
    assert fake.store[("agentnode:openai", "token")] == SECRET
    assert "openai (OS keychain)" in out                  # summary line
    assert config_exists()


def test_skip_credentials_default(monkeypatch, capsys):
    # credential choice: "" = default 4 (skip)
    _wire(monkeypatch, _PRE + [""] + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "none — add later" in out
    assert cs.list_credentials() == {}                    # nothing stored
    assert config_exists()                                # config still saved


def test_plaintext_fallback_label(monkeypatch, capsys):
    # conftest guard keeps the keychain unavailable -> file fallback
    _wire(monkeypatch, _PRE + ["1", "", ""] + _SAVE, getpass_values=[SECRET])
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "plaintext file (0600)" in out
    assert "OS keychain unavailable" in out
    assert "encrypted" not in out.lower()
    assert SECRET not in out
    entry = json.loads(cs._credentials_path().read_text(encoding="utf-8"))
    assert entry["providers"]["openai"]["storage"] == "file"


def test_env_var_detected_offers_import(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    # choice 1, import? y(default ""), test? "n", add another? "", save
    _wire(monkeypatch, _PRE + ["1", "", "n", ""] + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Found OPENAI_API_KEY" in out
    assert "overrides stored keys" in out
    assert SECRET not in out
    assert cs.get_llm_api_key("openai") == SECRET         # imported, not typed


def test_add_another_loop_two_providers(monkeypatch, capsys):
    _use_fake_keyring(monkeypatch)
    # 1+key, test n, another y, 2+key, test n, another n, save
    _wire(monkeypatch, _PRE + ["1", "n", "y", "2", "n", ""] + _SAVE,
          getpass_values=[SECRET, "sk-second-key-ab12"])
    assert run_wizard() == 0
    assert cs.get_llm_api_key("openai") == SECRET
    assert cs.get_llm_api_key("anthropic") == "sk-second-key-ab12"
    out = capsys.readouterr().out
    assert "openai (OS keychain), anthropic (OS keychain)" in out


# --- auth test is non-blocking ---------------------------------------------------

def test_key_test_offered_and_called(monkeypatch):
    _use_fake_keyring(monkeypatch)
    calls = _wire(monkeypatch, _PRE + ["1", "", ""] + _SAVE,
                  getpass_values=[SECRET], auth_test_rc=0)
    assert run_wizard() == 0
    assert calls == ["openai"]                            # default Y ran the test


def test_key_test_indeterminate_never_blocks(monkeypatch, capsys):
    _use_fake_keyring(monkeypatch)
    _wire(monkeypatch, _PRE + ["1", "", ""] + _SAVE,
          getpass_values=[SECRET], auth_test_rc=3)
    assert run_wizard() == 0                              # wizard completed
    out = capsys.readouterr().out
    assert "test later" in out


def test_key_test_rejection_never_blocks(monkeypatch):
    _use_fake_keyring(monkeypatch)
    _wire(monkeypatch, _PRE + ["1", "", ""] + _SAVE,
          getpass_values=[SECRET], auth_test_rc=1)
    assert run_wizard() == 0


def test_key_test_exception_never_blocks(monkeypatch):
    _use_fake_keyring(monkeypatch)
    _wire(monkeypatch, _PRE + ["1", "", ""] + _SAVE, getpass_values=[SECRET])
    monkeypatch.setattr("agentnode_sdk.cli.auth.cmd_auth_test",
                        lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    assert run_wizard() == 0


# --- cancel / non-TTY ------------------------------------------------------------

def test_cancel_during_key_entry_saves_no_config(monkeypatch):
    _use_fake_keyring(monkeypatch)
    _wire(monkeypatch, _PRE + ["1"])
    monkeypatch.setattr("getpass.getpass",
                        lambda prompt="": (_ for _ in ()).throw(KeyboardInterrupt()))
    assert run_wizard() == 130
    assert not config_exists()                            # nothing saved
    assert cs.list_credentials() == {}


def test_non_tty_skips_credential_screen(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: False))
    # no credential-choice input is consumed — straight to save
    _wire(monkeypatch, _PRE + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Non-interactive session" in out
    assert "agentnode auth set <provider>" in out
    assert cs.list_credentials() == {}


# --- sandbox status (read-only) ---------------------------------------------------

def test_sandbox_status_unavailable_shown(monkeypatch, capsys):
    _wire(monkeypatch, _PRE + [""] + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Sandbox runtime" in out
    assert "not found" in out
    assert "Trusted/curated" in out
    assert "agentnode sandbox doctor" in out


def test_sandbox_status_available_shown(monkeypatch, capsys):
    monkeypatch.setattr(
        "agentnode_sdk.sandbox.get_default_backend",
        lambda: types.SimpleNamespace(check_available=lambda: SandboxAvailability(
            available=True, backend="docker", reason="")))
    _wire(monkeypatch, _PRE + [""] + _SAVE)
    assert run_wizard() == 0
    assert "available (docker)" in capsys.readouterr().out


def test_wizard_never_pulls_or_enables_sandbox():
    """Structural guard: UX-3A is read-only towards docker and the flag."""
    import agentnode_sdk
    from pathlib import Path
    src = (Path(agentnode_sdk.__file__).parent / "cli" / "setup_wizard.py").read_text(encoding="utf-8")
    assert "cmd_sandbox_pull" not in src
    assert "subprocess" not in src
    assert "agent_sandbox.enabled" not in src
    assert "set_value" not in src


# --- config correctness ------------------------------------------------------------

def test_existing_screens_still_write_config(monkeypatch):
    # behavior choice 2 (review before install), rest defaults
    _wire(monkeypatch, ["", "2", "", "", "", "", ""] + _SAVE)
    assert run_wizard() == 0
    cfg = load_config()
    assert cfg["install_confirmation"] == "prompt"
    assert cfg["permissions"]["network"] == "prompt"
    assert cfg["agent_sandbox"]["enabled"] is False       # untouched by wizard
