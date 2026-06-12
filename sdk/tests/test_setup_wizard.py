"""UX-3A/3B — setup wizard: optional LLM-credential screen + local-sandbox
screen. All input/getpass mocked; no real provider calls, no real keychain
(fake seam), no real docker (doctor checks + pull are mocked seams).
"""
from __future__ import annotations

import json
import types

import pytest

from agentnode_sdk import credential_store as cs
from agentnode_sdk.cli.setup_wizard import run_wizard
from agentnode_sdk.config import config_exists, load_config

SECRET = "sk-WIZARD-SECRET-xyz9"

# inputs consumed by the pre-credential screens:
# intro Enter, install behavior, network, filesystem, code_execution, advanced
_PRE = ["", "", "", "", "", ""]
_SAVE = [""]

# doctor-check shapes for the mocked _build_env_checks seam
CHECKS_NO_RUNTIME = ([{"check": "runtime", "ok": False,
                       "detail": "no Docker or Podman found",
                       "fix": "Install Docker or Podman"}], False, False)
CHECKS_IMAGE_MISSING = ([{"check": "runtime", "ok": True, "detail": "docker"},
                         {"check": "daemon", "ok": True, "detail": "reachable"},
                         {"check": "image", "ok": False,
                          "detail": "pinned sandbox image is not present locally",
                          "fix": "agentnode sandbox pull"}], False, True)
CHECKS_READY = ([{"check": "runtime", "ok": True, "detail": "docker"},
                 {"check": "daemon", "ok": True, "detail": "reachable"},
                 {"check": "image", "ok": True, "detail": "pinned image present"}],
                True, False)


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
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "DASHSCOPE_API_KEY",
                "GEMINI_API_KEY", "OLLAMA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "agentnode_sdk.runtimes.agent_runner._load_agentnode_env", lambda: None)
    # wizard runs under pytest (no tty) — simulate an interactive terminal
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: True))
    # sandbox diagnosis: default "no runtime" (prompt-free path); NEVER real docker
    monkeypatch.setattr(
        "agentnode_sdk.cli.sandbox_commands._build_env_checks",
        lambda: CHECKS_NO_RUNTIME)
    # any pull without an explicit test override = consent violation
    monkeypatch.setattr(
        "agentnode_sdk.cli.sandbox_commands.cmd_sandbox_pull",
        lambda: pytest.fail("cmd_sandbox_pull called without explicit consent/override"))
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


# --- Endpoint-B: registry provider list + keyless ollama ---------------------------

def test_wizard_deepseek_happy_path(monkeypatch, capsys):
    fake = _use_fake_keyring(monkeypatch)
    # choice 4 = deepseek, test "n", add another "" (default n), save ""
    _wire(monkeypatch, _PRE + ["4", "n", ""] + _SAVE, getpass_values=[SECRET])
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    assert fake.store[("agentnode:deepseek", "token")] == SECRET
    assert "deepseek (OS keychain)" in out


def test_wizard_deepseek_env_import(monkeypatch, capsys):
    _use_fake_keyring(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    # choice 4, import "" (default y), test "n", another "", save ""
    _wire(monkeypatch, _PRE + ["4", "", "n", ""] + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Found DEEPSEEK_API_KEY" in out
    assert SECRET not in out
    assert cs.get_llm_api_key("deepseek") == SECRET


def test_wizard_ollama_keyless_no_getpass(monkeypatch, capsys):
    # choice 8 = ollama, use? "" (default y), another "", save "" — NO getpass
    # value provided: any getpass call would raise StopIteration and fail.
    _wire(monkeypatch, _PRE + ["8", "", ""] + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "no API key" in out
    assert "Enter API key" not in out                  # never prompted for a key
    assert "ollama (local, keyless)" in out            # summary line
    cfg = load_config()
    assert cfg["llm"]["default_provider"] == "ollama"  # persisted via Save
    assert cs.list_credentials() == {}                 # no credential stored


def test_wizard_ollama_cancel_persists_nothing(monkeypatch):
    # choice 8, use "" (yes), another "", save "n" -> nothing persisted
    _wire(monkeypatch, _PRE + ["8", "", "", "n"])
    assert run_wizard() == 1
    assert not config_exists()


def test_wizard_skip_default_is_nine(monkeypatch, capsys):
    # explicit "9" skips, same as the "" default
    _wire(monkeypatch, _PRE + ["9"] + _SAVE)
    assert run_wizard() == 0
    assert "none — add later" in capsys.readouterr().out


# --- sandbox screen (UX-3B) --------------------------------------------------------

def test_sandbox_no_runtime_friendly_no_prompts(monkeypatch, capsys):
    # default fixture = no runtime: the screen consumes NO input and never pulls
    _wire(monkeypatch, _PRE + [""] + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "no Docker or Podman found" in out
    assert "Install Docker or Podman" in out
    assert "Trusted/curated" in out
    assert "agentnode sandbox doctor" in out
    assert "not ready" in out
    assert load_config()["agent_sandbox"]["enabled"] is False


def test_sandbox_ready_enable_default_no(monkeypatch, capsys):
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_READY)
    # cred skip "", enable prompt "" (default No), save ""
    _wire(monkeypatch, _PRE + ["", ""] + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Sandbox ready" in out
    assert "agent_sandbox.enabled true" in out          # later-command hint
    cfg = load_config()
    assert cfg["agent_sandbox"]["enabled"] is False     # default stays off
    assert "Sandboxed community agents: " in out.replace("  ", " ") or "disabled" in out


def test_sandbox_ready_enable_yes_persists_real_bool(monkeypatch, capsys):
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_READY)
    _wire(monkeypatch, _PRE + ["", "y"] + _SAVE)
    assert run_wizard() == 0
    cfg = load_config()
    assert cfg["agent_sandbox"]["enabled"] is True
    assert not isinstance(cfg["agent_sandbox"]["enabled"], str)
    assert "enabled" in capsys.readouterr().out


def test_image_missing_user_declines_pull(monkeypatch, capsys):
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_IMAGE_MISSING)
    pulls = []
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands.cmd_sandbox_pull",
                        lambda: pulls.append(1) or 0)
    # cred skip "", pull prompt "" (default No), save ""
    _wire(monkeypatch, _PRE + ["", ""] + _SAVE)
    assert run_wizard() == 0
    assert pulls == []                                   # consent gate held
    out = capsys.readouterr().out
    assert "Skipped" in out
    assert "Enable sandboxed community agents" not in out  # not ready -> not offered
    assert load_config()["agent_sandbox"]["enabled"] is False


def test_image_missing_user_accepts_pull_success(monkeypatch, capsys):
    states = [CHECKS_IMAGE_MISSING, CHECKS_READY]        # before pull, after pull
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: states.pop(0))
    pulls = []
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands.cmd_sandbox_pull",
                        lambda: pulls.append(1) or 0)
    # cred skip "", pull "y", enable "" (default No), save ""
    _wire(monkeypatch, _PRE + ["", "y", ""] + _SAVE)
    assert run_wizard() == 0
    assert pulls == [1]                                  # exactly one explicit pull
    out = capsys.readouterr().out
    assert "Sandbox ready" in out                        # post-pull re-check
    assert load_config()["agent_sandbox"]["enabled"] is False


def test_pull_failure_is_non_blocking(monkeypatch, capsys):
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_IMAGE_MISSING)
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands.cmd_sandbox_pull",
                        lambda: 1)
    _wire(monkeypatch, _PRE + ["", "y"] + _SAVE)
    assert run_wizard() == 0                             # wizard still completes
    out = capsys.readouterr().out
    assert "the wizard continues" in out
    assert "Enable sandboxed community agents" not in out  # still not ready
    assert load_config()["agent_sandbox"]["enabled"] is False


def test_non_tty_no_pull_no_enable_prompt(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_IMAGE_MISSING)
    # non-TTY: credential screen self-skips AND sandbox screen consumes no input
    _wire(monkeypatch, _PRE + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "agentnode sandbox doctor" in out             # guidance instead of prompt
    assert "Pull the pinned" not in out                  # no pull offer without tty


def test_enable_not_persisted_on_save_cancel(monkeypatch):
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_READY)
    # cred skip "", enable "y", save "n" -> nothing persisted
    _wire(monkeypatch, _PRE + ["", "y", "n"])
    assert run_wizard() == 1
    assert not config_exists()


def test_wizard_structural_guards():
    """The wizard itself contains no docker calls and no direct config set_value;
    the only pull path is the imported, fully guarded cmd_sandbox_pull."""
    import agentnode_sdk
    from pathlib import Path
    src = (Path(agentnode_sdk.__file__).parent / "cli" / "setup_wizard.py").read_text(encoding="utf-8")
    assert "subprocess" not in src
    assert "set_value" not in src
    assert "cmd_sandbox_pull" in src                     # reuse, not reimplementation


# --- config correctness ------------------------------------------------------------

def test_existing_screens_still_write_config(monkeypatch):
    # behavior choice 2 (review before install), rest defaults
    _wire(monkeypatch, ["", "2", "", "", "", "", ""] + _SAVE)
    assert run_wizard() == 0
    cfg = load_config()
    assert cfg["install_confirmation"] == "prompt"
    assert cfg["permissions"]["network"] == "prompt"
    assert cfg["agent_sandbox"]["enabled"] is False       # untouched by wizard
