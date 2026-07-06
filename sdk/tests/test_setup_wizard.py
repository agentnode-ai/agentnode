"""Setup wizard — full first-class config coverage. Multiple-choice screens with a
recommended default; all input/getpass mocked; no real provider calls, no real
keychain (fake seam), no real docker (doctor checks + pull are mocked seams).
"""
from __future__ import annotations

import json
import types

import pytest

from agentnode_sdk import credential_store as cs
from agentnode_sdk.cli.setup_wizard import run_wizard
from agentnode_sdk.config import config_exists, default_config, load_config
from agentnode_sdk.guard import _DEFAULT_GUARD_POLICY, _STRICT_GUARD_POLICY

SECRET = "sk-WIZARD-SECRET-xyz9"

# Inputs consumed by the pre-credential screens (all "(recommended)" defaults):
# intro Enter, install behavior, trust level, network, filesystem, code_execution,
# guard posture.  (On a TTY every _pick reads one line; on non-TTY _pick reads none.)
_PRE = ["", "", "", "", "", "", ""]
_ADV = ["n"]      # skip the optional Advanced gate (TTY only)
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
    _wire(monkeypatch, _PRE + ["1", "", ""] + [""] + _ADV + _SAVE, getpass_values=[SECRET])
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "...xyz9" in out
    assert "OS keychain" in out
    assert "encrypted" not in out.lower()
    assert fake.store[("agentnode:openai", "token")] == SECRET
    assert "openai (OS keychain)" in out
    assert config_exists()


def test_skip_credentials_default(monkeypatch, capsys):
    _wire(monkeypatch, _PRE + [""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "none — add later" in out
    assert cs.list_credentials() == {}
    assert config_exists()


def test_plaintext_fallback_label(monkeypatch, capsys):
    _wire(monkeypatch, _PRE + ["1", "", ""] + [""] + _ADV + _SAVE, getpass_values=[SECRET])
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
    # choice 1, import "" (default y), test "n", add another "", sandbox, adv, save
    _wire(monkeypatch, _PRE + ["1", "", "n", ""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Found OPENAI_API_KEY" in out
    assert "overrides stored keys" in out
    assert SECRET not in out
    assert cs.get_llm_api_key("openai") == SECRET


def test_add_another_loop_two_providers(monkeypatch, capsys):
    _use_fake_keyring(monkeypatch)
    # openai (no default-offer: openai IS the default), another y, anthropic
    # (no default-offer: openai still in the stored set), another "", sandbox, adv, save
    _wire(monkeypatch, _PRE + ["1", "n", "y", "2", "n", ""] + [""] + _ADV + _SAVE,
          getpass_values=[SECRET, "sk-second-key-ab12"])
    assert run_wizard() == 0
    assert cs.get_llm_api_key("openai") == SECRET
    assert cs.get_llm_api_key("anthropic") == "sk-second-key-ab12"
    out = capsys.readouterr().out
    assert "openai (OS keychain), anthropic (OS keychain)" in out


# --- auth test is non-blocking ---------------------------------------------------

def test_key_test_offered_and_called(monkeypatch):
    _use_fake_keyring(monkeypatch)
    calls = _wire(monkeypatch, _PRE + ["1", "", ""] + [""] + _ADV + _SAVE,
                  getpass_values=[SECRET], auth_test_rc=0)
    assert run_wizard() == 0
    assert calls == ["openai"]


def test_key_test_indeterminate_never_blocks(monkeypatch, capsys):
    _use_fake_keyring(monkeypatch)
    _wire(monkeypatch, _PRE + ["1", "", ""] + [""] + _ADV + _SAVE,
          getpass_values=[SECRET], auth_test_rc=3)
    assert run_wizard() == 0
    assert "test later" in capsys.readouterr().out


def test_key_test_rejection_never_blocks(monkeypatch):
    _use_fake_keyring(monkeypatch)
    _wire(monkeypatch, _PRE + ["1", "", ""] + [""] + _ADV + _SAVE,
          getpass_values=[SECRET], auth_test_rc=1)
    assert run_wizard() == 0


def test_key_test_exception_never_blocks(monkeypatch):
    _use_fake_keyring(monkeypatch)
    _wire(monkeypatch, _PRE + ["1", "", ""] + [""] + _ADV + _SAVE, getpass_values=[SECRET])
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
    assert not config_exists()
    assert cs.list_credentials() == {}


def test_non_tty_skips_credential_screen(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: False))
    # non-TTY: every _pick self-defaults (reads NO line); only intro + save read input
    _wire(monkeypatch, ["", ""])
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Non-interactive session" in out
    assert "agentnode auth set <provider>" in out
    assert cs.list_credentials() == {}


# --- registry provider list + keyless ollama ---------------------------------------

def test_wizard_deepseek_happy_path(monkeypatch, capsys):
    fake = _use_fake_keyring(monkeypatch)
    # choice 4, test "n", default-provider offer "n" (deepseek not the current default),
    # add another "", sandbox, adv, save
    _wire(monkeypatch, _PRE + ["4", "n", "n", ""] + [""] + _ADV + _SAVE, getpass_values=[SECRET])
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    assert fake.store[("agentnode:deepseek", "token")] == SECRET
    assert "deepseek (OS keychain)" in out


def test_wizard_deepseek_env_import(monkeypatch, capsys):
    _use_fake_keyring(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    # choice 4, import "" (y), test "n", default-offer "n", add another ""
    _wire(monkeypatch, _PRE + ["4", "", "n", "n", ""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Found DEEPSEEK_API_KEY" in out
    assert SECRET not in out
    assert cs.get_llm_api_key("deepseek") == SECRET


def test_default_provider_offer_sets_default(monkeypatch):
    _use_fake_keyring(monkeypatch)
    # choice 4, test "n", add another "" (=No, break), then ACCEPT "use as default?" "y"
    _wire(monkeypatch, _PRE + ["4", "n", "", "y"] + [""] + _ADV + _SAVE, getpass_values=[SECRET])
    assert run_wizard() == 0
    assert load_config()["llm"]["default_provider"] == "deepseek"


def test_wizard_ollama_keyless_no_getpass(monkeypatch, capsys):
    _wire(monkeypatch, _PRE + ["8", "", ""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "no API key" in out
    assert "Enter API key" not in out
    assert "ollama (local, keyless)" in out
    cfg = load_config()
    assert cfg["llm"]["default_provider"] == "ollama"
    assert cs.list_credentials() == {}


def test_wizard_ollama_cancel_persists_nothing(monkeypatch):
    _wire(monkeypatch, _PRE + ["8", "", ""] + [""] + _ADV + ["n"])
    assert run_wizard() == 1
    assert not config_exists()


def test_wizard_skip_default_is_nine(monkeypatch, capsys):
    _wire(monkeypatch, _PRE + ["9"] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    assert "none — add later" in capsys.readouterr().out


# --- sandbox screen + host-trust policy --------------------------------------------

def test_sandbox_no_runtime_friendly_no_prompts(monkeypatch, capsys):
    # NO_RUNTIME: the sandbox screen consumes only the host_trust_policy pick, no pull
    _wire(monkeypatch, _PRE + [""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "no Docker or Podman found" in out
    assert "Install Docker or Podman" in out
    assert "Trusted/curated" in out
    assert "agentnode sandbox doctor" in out
    assert "not ready" in out
    assert load_config()["agent_sandbox"]["enabled"] is False


def test_host_trust_policy_choice_persists(monkeypatch):
    # sandbox host_trust_policy pick = "2" (curated_only)
    _wire(monkeypatch, _PRE + [""] + ["2"] + _ADV + _SAVE)
    assert run_wizard() == 0
    assert load_config()["sandbox"]["host_trust_policy"] == "curated_only"


def test_sandbox_ready_enable_default_no(monkeypatch, capsys):
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_READY)
    # cred skip, htp "", enable "" (default No), adv, save
    _wire(monkeypatch, _PRE + [""] + ["", ""] + _ADV + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Sandbox ready" in out
    assert "agent_sandbox.enabled true" in out
    cfg = load_config()
    assert cfg["agent_sandbox"]["enabled"] is False
    assert "disabled" in out


def test_sandbox_ready_enable_yes_persists_real_bool(monkeypatch, capsys):
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_READY)
    _wire(monkeypatch, _PRE + [""] + ["", "y"] + _ADV + _SAVE)
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
    # cred skip, htp "", pull "" (No), adv, save
    _wire(monkeypatch, _PRE + [""] + ["", ""] + _ADV + _SAVE)
    assert run_wizard() == 0
    assert pulls == []
    out = capsys.readouterr().out
    assert "Skipped" in out
    assert "Enable sandboxed community agents" not in out
    assert load_config()["agent_sandbox"]["enabled"] is False


def test_image_missing_user_accepts_pull_success(monkeypatch, capsys):
    states = [CHECKS_IMAGE_MISSING, CHECKS_READY]
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: states.pop(0))
    pulls = []
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands.cmd_sandbox_pull",
                        lambda: pulls.append(1) or 0)
    # cred skip, htp "", pull "y", enable "" (No), adv, save
    _wire(monkeypatch, _PRE + [""] + ["", "y", ""] + _ADV + _SAVE)
    assert run_wizard() == 0
    assert pulls == [1]
    assert "Sandbox ready" in capsys.readouterr().out
    assert load_config()["agent_sandbox"]["enabled"] is False


def test_pull_failure_is_non_blocking(monkeypatch, capsys):
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_IMAGE_MISSING)
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands.cmd_sandbox_pull",
                        lambda: 1)
    _wire(monkeypatch, _PRE + [""] + ["", "y"] + _ADV + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "the wizard continues" in out
    assert "Enable sandboxed community agents" not in out
    assert load_config()["agent_sandbox"]["enabled"] is False


def test_non_tty_no_pull_no_enable_prompt(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_IMAGE_MISSING)
    _wire(monkeypatch, ["", ""])
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "agentnode sandbox doctor" in out
    assert "Pull the pinned" not in out


def test_enable_not_persisted_on_save_cancel(monkeypatch):
    monkeypatch.setattr("agentnode_sdk.cli.sandbox_commands._build_env_checks",
                        lambda: CHECKS_READY)
    # cred skip, htp "", enable "y", adv "n", save "n" -> nothing persisted
    _wire(monkeypatch, _PRE + [""] + ["", "y"] + _ADV + ["n"])
    assert run_wizard() == 1
    assert not config_exists()


# --- guard posture -----------------------------------------------------------------

def test_guard_posture_balanced_is_default(monkeypatch):
    # all recommendations (guard pick "") -> balanced == the shipped default
    _wire(monkeypatch, _PRE + [""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    assert load_config()["guard"] == _DEFAULT_GUARD_POLICY


def test_guard_posture_strict(monkeypatch):
    # guard pick "2" = strict
    _wire(monkeypatch, ["", "", "", "", "", "", "2"] + [""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    assert load_config()["guard"] == _STRICT_GUARD_POLICY


def test_guard_posture_permissive(monkeypatch):
    # guard pick "3" = permissive (all allow except unknown prompt)
    _wire(monkeypatch, ["", "", "", "", "", "", "3"] + [""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    g = load_config()["guard"]
    assert g["delete"] == "allow" and g["execute"] == "allow" and g["unknown"] == "prompt"


def test_guard_posture_customize_writes_each(monkeypatch):
    # guard pick "4" = customize; delete -> "3" (deny), the other 8 -> "" (balanced)
    guard = ["4", "3", "", "", "", "", "", "", "", ""]
    _wire(monkeypatch, ["", "", "", "", "", ""] + guard + [""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    g = load_config()["guard"]
    assert g["delete"] == "deny"           # overridden
    assert g["execute"] == "prompt"        # balanced default kept
    assert g["read"] == "allow"


# --- advanced gate -----------------------------------------------------------------

def test_advanced_gate_sets_niche_keys(monkeypatch):
    # advanced "y"; require-before-auto-install pick "2" (No -> real False);
    # external-write pick "4" (deny)
    _wire(monkeypatch, _PRE + [""] + [""] + ["y", "2", "4"] + _SAVE)
    assert run_wizard() == 0
    cfg = load_config()
    assert cfg["credentials"]["require_before_auto_install"] is False
    assert cfg["risk_policies"]["external_write_capable"] == "deny"


# --- invalid input re-prompts (never a silent fallback) ----------------------------

def test_pick_invalid_input_reprompts(monkeypatch, capsys):
    # install screen: "x" (invalid) -> re-prompt -> "2" (review). A typo must not
    # silently set a security/behaviour choice. (intro, install x+2, trust, net, fs,
    # code, guard = 8 pre-credential inputs)
    _wire(monkeypatch, ["", "x", "2", "", "", "", "", ""] + [""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    out = capsys.readouterr().out
    assert "Please choose 1–3" in out
    assert load_config()["install_confirmation"] == "prompt"   # used the re-entered "2"


# --- non-breaking guarantee + structural guards ------------------------------------

def test_all_recommendations_equal_default_config(monkeypatch):
    # accepting every recommendation reproduces default_config() exactly
    _wire(monkeypatch, _PRE + [""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    saved, defaults = load_config(), default_config()
    for k in ("created_at", "updated_at"):
        saved.pop(k, None)
        defaults.pop(k, None)
    assert saved == defaults


def test_non_tty_writes_defaults(monkeypatch):
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: False))
    _wire(monkeypatch, ["", ""])                     # intro + save only
    assert run_wizard() == 0
    cfg = load_config()
    assert cfg["guard"] == _DEFAULT_GUARD_POLICY
    assert cfg["sandbox"]["host_trust_policy"] == "default"
    assert cfg["permissions"]["code_execution"] == "sandboxed"
    assert cfg["trust"]["minimum_trust_level"] == "verified"


def test_wizard_structural_guards():
    """No docker calls, no direct config set_value; the only pull path is the
    imported, fully guarded cmd_sandbox_pull."""
    import agentnode_sdk
    from pathlib import Path
    src = (Path(agentnode_sdk.__file__).parent / "cli" / "setup_wizard.py").read_text(encoding="utf-8")
    assert "subprocess" not in src
    assert "set_value" not in src
    assert "cmd_sandbox_pull" in src


def test_existing_screens_still_write_config(monkeypatch):
    # install choice "2" (review before install), everything else recommended
    _wire(monkeypatch, ["", "2", "", "", "", "", ""] + [""] + [""] + _ADV + _SAVE)
    assert run_wizard() == 0
    cfg = load_config()
    assert cfg["install_confirmation"] == "prompt"
    assert cfg["permissions"]["network"] == "prompt"
    assert cfg["agent_sandbox"]["enabled"] is False
