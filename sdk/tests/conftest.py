"""Shared fixtures and utilities for E2E runtime tests.

Provides ToolUsageScore, ToolCallTracker, and score-writing infrastructure
for structured provider comparison and regression tracking.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest import mock

import pytest


# ------------------------------------------------------------------ the browser, once


@pytest.fixture(scope="session")
def browser():
    """ONE Playwright, for the whole session, shared by every module that drives a browser.

    It is here rather than in the module that first needed it because `sync_playwright()` cannot
    be entered twice in one thread: a second module declaring its own session-scoped `browser`
    gets a second instance, and it fails with "you are using Playwright Sync API inside the
    asyncio loop" -- which names the symptom rather than the cause.

    A missing browser is a SKIP by default and a FAILURE under `AGENTNODE_BROWSER_TESTS=required`,
    which is how the managed-access lane runs it. A skip that reads as a pass is how a suite comes
    to report a flow that nothing exercised.
    """
    required = os.environ.get("AGENTNODE_BROWSER_TESTS", "").lower() == "required"

    def no_browser(why):
        if required:
            pytest.fail("the browser tests were required and could not run: %s" % why,
                        pytrace=False)
        pytest.skip("%s -- set AGENTNODE_BROWSER_TESTS=required to make this a failure" % why)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:                                # noqa: BLE001
        no_browser("playwright is not installed (%s)" % exc)

    try:
        with sync_playwright() as play:
            try:
                engine = play.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:                          # noqa: BLE001
                no_browser("chromium would not start (%s)" % exc)
            yield engine
            engine.close()
    except Exception as exc:                                  # noqa: BLE001
        no_browser("playwright would not start (%s)" % exc)




@pytest.fixture(autouse=True)
def _the_pin_is_not_the_one_on_this_machine(tmp_path_factory, monkeypatch):
    """Tests must NEVER read the runtime pin belonging to the machine they run on.

    The pin moved out of the state directory and into `/etc/agentnode`, because a restore drill
    destroys the state and rebuilt a pin describing the previous build. That was the right move
    for the service, and it made the DEFAULT a machine-global path -- so on any machine that has
    actually been deployed, `cmd_start` read the HOST's pin, compared it against a source tree
    that records no artefact digest, and refused. Four tests failed on the DevelopServer and
    passed everywhere else, which is the signature of host state leaking into a suite.

    Each test therefore gets an EMPTY pin directory of its own. The refusals themselves are
    neither weakened nor skipped: `test_runtime_pin.py::TestStartingRefuses` points the same
    lookup at a directory it writes real pins into, and drives the CLI entry points against them.
    """
    monkeypatch.setenv("AGENTNODE_PIN_DIR",
                       str(tmp_path_factory.mktemp("pin-that-is-not-this-machines")))


@pytest.fixture(autouse=True)
def _no_real_os_keychain():
    """Tests must NEVER touch the real OS keychain (UX-2 vault).

    Default: keychain reported unavailable → credential_store deterministically
    uses file storage (the pre-vault behavior all legacy tests assume). Vault
    tests opt back in by resetting ``_keyring_state["available"]`` to None AND
    monkeypatching ``_get_keyring_backend`` to a fake — the probe then runs
    against the fake, still never the real keychain.
    """
    from agentnode_sdk import credential_store as _cs

    prev = _cs._keyring_state["available"]
    _cs._keyring_state["available"] = False
    try:
        yield
    finally:
        _cs._keyring_state["available"] = prev


@pytest.fixture(autouse=True)
def _bootstrap_registry_keys():
    """Run all tests in bootstrap mode (empty REGISTRY_KEYS).

    REGISTRY_KEYS are pinned in source (v0.11.0+). Tests that don't
    exercise TG-4 enforcement need bootstrap mode to avoid spurious
    REGISTRY_SIGNATURE_MISSING errors on mocked HTTP responses.
    TG-4 tests override this with their own _patch_registry_keys().
    """
    with mock.patch(
        "agentnode_sdk.registry_trust.REGISTRY_KEYS",
        MappingProxyType({}),
    ):
        yield


@pytest.fixture(autouse=True)
def _default_sandbox_available():
    """Run tests as if a container runtime is available.

    P0.1 added a LIVE fail-closed sandbox gate to runner.run_tool that blocks
    non-trusted execution when no container runtime is present. Pre-existing tests
    are not about the sandbox and would otherwise be blocked. The gate stays live;
    its REAL block behaviour is covered by test_sandbox_gate.py, which overrides
    this fixture in-body. (Parallels _bootstrap_registry_keys above.)
    """
    from agentnode_sdk.sandbox import set_default_backend
    from agentnode_sdk.sandbox.backend import SandboxBackend
    from agentnode_sdk.sandbox.types import SandboxAvailability

    class _Available(SandboxBackend):
        def check_available(self):
            return SandboxAvailability(available=True, backend="docker", reason="",
                                       daemon_ok=True, image_available=True)

        def wrap_command(self, spec):
            return ["docker", "run", *spec.command]

    set_default_backend(_Available())
    yield
    set_default_backend(None)


@pytest.fixture
def legacy_default_policy(monkeypatch):
    """Pin ``sandbox.host_trust_policy`` to the pre-EM-1 value ``default``.

    **Not autouse.** Nothing is overridden unless a test explicitly requests it with
    ``@pytest.mark.usefixtures("legacy_default_policy")``, so an unannotated test observes
    the SHIPPED default, ``curated_only`` (EM-1 / EXEC-MODEL-SCOPE-0001, option 1B).

    R3 Route A (EM2-AC-REMEDIATION-DECISION-0001) removed the earlier suite-wide autouse
    mask precisely because a global override let coverage drift away from shipped
    behaviour silently. Each remaining override is applied at its own test or class and is
    justified there.

    Who legitimately needs it: tests whose subject is the HOST install or dispatch
    mechanics of a ``trusted`` package — install transactions, environment write-locks,
    lockfile integrity, interpreter resolution, subprocess/direct dispatch. Under the
    shipped default those packages route to the sandbox, so the host path under test is
    never reached. The routing itself is covered by ``test_shipped_default_routing.py``.

    In-process only: a spawned subprocess reads the config file, so a test that spawns one
    must also seed the policy into that config (see ``_seed_host_policy`` in
    ``test_layer3_installer_concurrency.py``).
    """
    monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: "default")
    monkeypatch.setattr(
        "agentnode_sdk.config.read_host_trust_policy_snapshot", lambda: "default"
    )


@pytest.fixture(autouse=True)
def _non_sensitive_test_env(monkeypatch):
    """H7a-v2: make the default test environment non-sensitive.

    policy._detect_environment() treats any env var matching _SECRET_PREFIXES as
    has_secrets=True and escalates low-trust/network or undeclared-permission runs to
    policy_prompt. GitHub Actions may provide such variables ambiently, so tests must
    not depend on the runner's secret-shaped environment.

    Tests that intentionally exercise has_secrets set their own secret env vars inside
    the test body after this fixture has run.
    """
    from agentnode_sdk.policy import _SECRET_PREFIXES

    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in _SECRET_PREFIXES):
            monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Policy bypass fixture — explicit opt-in via @pytest.mark.bypass_policy.
#
# WHY THIS EXISTS: Phase A added policy checks (check_run/check_install) to
# runner.py, runtime.py, and client.py. Pre-existing tests were written
# before policy enforcement and don't set up trust/permission entries that
# satisfy the new checks. Rather than modifying 100+ existing lockfile
# fixtures, tests that don't exercise policy can opt in to bypass it.
#
# WHEN TO USE: Only on pre-existing tests that test non-policy behavior
# (subprocess isolation, tool dispatch, client API calls, etc.).
# New tests MUST NOT use this unless they genuinely don't test policy.
#
# WHEN NOT TO USE: test_policy.py and test_policy_integration.py provide
# their own explicit config mocks and must never use this fixture.
# ---------------------------------------------------------------------------

_ALLOW_RESULT = None  # Lazy-initialized to avoid import ordering issues


def _get_allow_result():
    global _ALLOW_RESULT
    if _ALLOW_RESULT is None:
        from agentnode_sdk.policy import PolicyResult
        _ALLOW_RESULT = PolicyResult(action="allow", reason="test bypass", source="default")
    return _ALLOW_RESULT


@pytest.fixture
def bypass_policy():
    """Bypass all policy checks. Opt-in only — use @pytest.mark.bypass_policy
    or request this fixture explicitly in tests that don't exercise policy.

    Mocks check_run, check_install, and audit_decision at all import sites
    (policy.py, runner.py, runtime.py) so tool dispatch proceeds without
    trust/permission enforcement.
    """
    allow = _get_allow_result()
    from agentnode_sdk.guard import GuardDecision
    guard_allow = GuardDecision(action="allow", reason="test bypass", source="guard.default")
    patches = [
        mock.patch("agentnode_sdk.policy.check_run", return_value=allow),
        mock.patch("agentnode_sdk.policy.check_install", return_value=allow),
        mock.patch("agentnode_sdk.policy.audit_decision"),
        # runner.py imports
        mock.patch("agentnode_sdk.runner.check_run", return_value=allow),
        mock.patch("agentnode_sdk.runner.audit_decision"),
        # guard bypass
        mock.patch("agentnode_sdk.guard.check_action", return_value=guard_allow),
        mock.patch("agentnode_sdk.guard.check_rate_limit", return_value=guard_allow),
        # runtime.py imports (aliased)
        mock.patch("agentnode_sdk.runtime._policy_check_run", return_value=allow),
        mock.patch("agentnode_sdk.runtime._policy_audit"),
    ]
    for p in patches:
        p.start()
    yield
    for p in reversed(patches):
        p.stop()


# ---------------------------------------------------------------------------
# ToolUsageScore — structured logging for provider comparison
# ---------------------------------------------------------------------------

@dataclass
class ToolUsageScore:
    """Score object for tracking tool usage across providers and tests.

    Written as JSON to sdk/.artifacts/tool_usage_scores/ after each test.
    Enables: provider comparison, prompt version A/B testing, regression detection.
    """

    test_name: str
    provider: str
    capability_class: str
    model: str = ""
    prompt_version: str = "v1_basic"
    tool_calls: list[str] = field(default_factory=list)
    correct_sequence: bool = False
    expected_tool_path: bool = False
    hallucination: bool = False
    final_answer_present: bool = False
    success: bool = False
    duration_ms: int = 0

    @property
    def verdict(self) -> str:
        """PASS / WARN / FAIL based on fixed schema.

        PASS = success + expected_tool_path + no hallucination
        WARN = success but tool path inefficient
        FAIL = wrong sequence, hallucination, or incorrect result
        """
        if self.success and self.expected_tool_path and not self.hallucination:
            return "PASS"
        if self.success and not self.expected_tool_path:
            return "WARN"
        return "FAIL"


# ---------------------------------------------------------------------------
# ToolCallTracker — wraps runtime.handle() to record tool calls
# ---------------------------------------------------------------------------

class ToolCallTracker:
    """Monkey-patches runtime.handle() to record all tool calls.

    Works with both OpenAI and Anthropic loops since handle() is the
    single dispatch point for all tool execution.
    """

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.calls: list[str] = []
        self.call_details: list[dict] = []
        self._original_handle = runtime.handle
        self._start_time = time.monotonic()
        runtime.handle = self._tracking_handle

    def _tracking_handle(self, tool_name: str, arguments: dict | None = None) -> dict:
        self.calls.append(tool_name)
        result = self._original_handle(tool_name, arguments)
        self.call_details.append({
            "tool": tool_name,
            "arguments": arguments,
            "success": result.get("success"),
            "elapsed_ms": int((time.monotonic() - self._start_time) * 1000),
        })
        return result

    def restore(self) -> None:
        """Restore original handle() method."""
        self.runtime.handle = self._original_handle

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start_time) * 1000)


# ---------------------------------------------------------------------------
# Score writer — JSON to .artifacts/tool_usage_scores/
# ---------------------------------------------------------------------------

_SCORES_DIR = Path(__file__).resolve().parent.parent / ".artifacts" / "tool_usage_scores"


def write_score(score: ToolUsageScore) -> Path:
    """Write a ToolUsageScore as JSON. Returns the file path."""
    _SCORES_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    model_slug = score.model.replace("/", "-").replace(":", "-") if score.model else "default"
    filename = f"{score.provider}_{model_slug}_{score.test_name}_{ts}.json"
    path = _SCORES_DIR / filename
    data = asdict(score)
    data["verdict"] = score.verdict
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Helper: extract text from provider response
# ---------------------------------------------------------------------------

def extract_response_text(result: Any, provider: str) -> str:
    """Extract text content from an OpenAI, Anthropic, or Gemini response.

    Accepts both plain provider names ("openai", "anthropic", "gemini") and
    prefixed IDs ("openai_gpt4o_mini", "anthropic_sonnet", "gemini_flash").
    """
    if isinstance(result, dict):
        # Error dict from runtime.run()
        return result.get("error", {}).get("message", "")

    # Normalize provider IDs like "anthropic_haiku" → "anthropic"
    family = provider.split("_")[0] if "_" in provider else provider
    # Compat/third-party providers all use OpenAI response format
    if family in ("compat", "nvidia", "openrouter"):
        family = "openai"

    if family == "openai":
        if hasattr(result, "content") and result.content:
            return result.content
        return ""

    if family == "anthropic":
        if hasattr(result, "content"):
            return " ".join(
                b.text for b in result.content if hasattr(b, "text")
            )
        return ""

    if family == "gemini":
        # Gemini GenerateContentResponse: .text or .candidates[0].content.parts
        if hasattr(result, "text") and result.text:
            return result.text
        if hasattr(result, "candidates") and result.candidates:
            parts = result.candidates[0].content.parts or []
            texts = [p.text for p in parts if hasattr(p, "text") and p.text]
            return " ".join(texts)
        return ""

    return ""


# ---------------------------------------------------------------------------
# Helper: check tool call sequence
# ---------------------------------------------------------------------------

def check_sequence(calls: list[str], expected_order: list[str]) -> bool:
    """Check that expected tools appear in the correct order within calls.

    Only checks relative order of tools that are present.
    Returns False if any expected tool is missing.
    """
    indices = []
    for tool in expected_order:
        if tool not in calls:
            return False
        indices.append(calls.index(tool))
    return indices == sorted(indices)


# ---------------------------------------------------------------------------
# Helper: build score from tracker + result
# ---------------------------------------------------------------------------

def build_score(
    *,
    test_name: str,
    provider: str,
    capability_class: str,
    tracker: ToolCallTracker,
    result: Any,
    expected_tools: list[str],
    expected_sequence: list[str] | None = None,
    prompt_version: str = "v1_basic",
    model: str = "",
) -> ToolUsageScore:
    """Build a ToolUsageScore from test execution data and write to disk."""
    response_text = extract_response_text(result, provider)

    # Check sequence
    correct_sequence = True
    if expected_sequence:
        correct_sequence = check_sequence(tracker.calls, expected_sequence)

    # Check expected tool path
    expected_tool_path = all(t in tracker.calls for t in expected_tools)

    # Check hallucination: expected tool calls but none were made
    hallucination = len(expected_tools) > 0 and len(tracker.calls) == 0

    # Check final answer
    final_answer_present = len(response_text.strip()) > 0

    # Overall success
    success = (
        expected_tool_path
        and correct_sequence
        and final_answer_present
        and not hallucination
    )

    score = ToolUsageScore(
        test_name=test_name,
        provider=provider,
        capability_class=capability_class,
        model=model,
        prompt_version=prompt_version,
        tool_calls=list(tracker.calls),
        correct_sequence=correct_sequence,
        expected_tool_path=expected_tool_path,
        hallucination=hallucination,
        final_answer_present=final_answer_present,
        success=success,
        duration_ms=tracker.elapsed_ms,
    )
    write_score(score)
    return score


# ---------------------------------------------------------------- reliability trail
#
# A fault that accumulates over a run -- a leaked thread, a leaked descriptor -- is invisible in
# the test that finally trips over it and obvious in a line going up. One has already been found
# this way and fixed. Off unless AGENTNODE_DIAGNOSE is set, so a developer running one test pays
# nothing for it.


@pytest.fixture(autouse=True)
def _reliability_trail(request):
    from tests import reliability

    if not reliability.enabled():
        yield
        return
    trail = os.environ.get("AGENTNODE_DIAGNOSE_TRAIL") or reliability.TRAIL_NAME
    name = request.node.nodeid
    reliability.record(trail, name, "before")
    try:
        yield
    finally:
        reliability.record(trail, name, "after")


# ---------------------------------------------------------------- who owns a started server
#
# The ownership boundary for anything a test starts. Servers registered through
# `tests.serving.owned()` are stopped here whether the test passed, failed, raised, or was
# interrupted half way through setting something up -- which is the case that used to leave a
# listening socket and a thread behind with nobody able to reach either.


@pytest.fixture(autouse=True)
def _servers_have_an_owner():
    import contextlib as _contextlib

    from tests import serving

    with _contextlib.ExitStack() as stack:
        previous, serving._owner = serving._owner, stack
        try:
            yield stack
        finally:
            serving._owner = previous


# ---------------------------------------------------------------- the lifecycle gate
#
# Threads were counted per test before, which cannot tell a leak from a session fixture that is
# legitimately still serving -- and that ambiguity is why the previous round could not conclude.
# The count is taken here instead, after the whole session and every fixture it owned has been
# torn down. At this point nothing owns anything, so any serving or handler thread still alive is
# unowned by definition, and each one is reported with where it was started rather than as a total.
#
# The process ending is NOT the proof. This runs while the process is still alive and asks what
# it is still holding.


def pytest_configure(config):
    from tests import serving

    serving.remember_births()


def _write_the_lifecycle_report(text: str) -> str:
    where = os.environ.get("AGENTNODE_LIFECYCLE_REPORT") or "lifecycle-at-the-end.txt"
    try:
        with open(where, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:                                           # pragma: no cover
        pass
    return where


def pytest_sessionfinish(session, exitstatus):
    """Late enough that session and class fixtures have already been finalised."""
    import time as _time

    from tests import serving

    # Bounded, never assumed: a thread told to stop gets a moment to actually stop.
    deadline = _time.monotonic() + 10.0
    left = serving.outstanding()
    while left and _time.monotonic() < deadline:
        _time.sleep(0.2)
        left = serving.outstanding()

    if not left:
        return
    report = ("%d serving or handler thread(s) outlived the session and every fixture that could "
              "have owned one.\n%s" % (len(left), serving.describe(left)))
    where = _write_the_lifecycle_report(report)
    print("\n" + report[:4000])
    print("\n  full report: %s" % where)
    if os.environ.get("AGENTNODE_LIFECYCLE_GATE") and exitstatus == 0:
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def _somewhere_to_keep_a_credential(monkeypatch):
    """This suite has no keyring, and says so rather than hoping.

    Saying it once here rather than in every test that saves a connection: the refusal when
    there is nowhere safe is a real behaviour with its own tests in `test_credentials.py`, and
    every other test is about something else.

    The `_keyring` patch is not belt-and-braces. `credentials.keep` asks the keyring FIRST and
    only falls back to the file, so the environment variable below decides nothing on a
    machine that HAS one -- and on such a machine this suite was writing real tokens into the
    real OS credential store, which is precisely what the fixture above exists to prevent by
    the other route. It also made three `test_em3c_cli` tests fail there and nowhere else:
    they read the token out of `gateways.json`, which is deliberately EMPTY when the
    credential went to a keyring.

    `test_credentials.py` sets `_keyring` itself, to a fake, in the tests that are about
    having one. A fixture applied here does not stop that: theirs runs later and wins.
    """
    from agentnode_sdk.access import credentials

    monkeypatch.setattr(credentials, "_keyring", lambda: None)
    monkeypatch.setenv(credentials.SAY_SO, "file")
