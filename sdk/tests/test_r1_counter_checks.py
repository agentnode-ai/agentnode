"""EM-3B-R1: the counter-checks. Every regression test must FAIL against the old implementation.

A test that passes on the code that produced run `33751686586` proves nothing about the fix. So
each of the three defects is reconstructed here exactly as it was, and the assertion that now
guards it is run against that reconstruction and required to fail.

The old behaviour is rebuilt from the code that was replaced, quoted in each test, rather than
described. If one of these ever stops failing, the reconstruction has drifted from what was there
and this file is the thing to fix -- not the assertion.
"""
from __future__ import annotations

import subprocess

import pytest

from agentnode_sdk.sandbox import container_backend as cb
from agentnode_sdk.sandbox.types import ProcessSpec, SandboxAvailability

CID = "c0ffee" * 10 + "abcd"


def _fails(assertion) -> bool:
    """Run an assertion against the old behaviour. True when it fails, which is the point."""
    try:
        assertion()
    except AssertionError:
        return True
    except Exception as exc:                                        # noqa: BLE001
        raise AssertionError(
            f"the counter-check itself broke ({type(exc).__name__}: {exc}); it proves nothing"
        ) from exc
    return False


# --------------------------------------------------------------------------------- R1

class OldTimeoutBackend(cb.ContainerBackend):
    """The timeout path exactly as it was:

        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return -1, out or "", (err or "") + f"[sandbox timed out after {timeout}s]"

    It kills the runtime CLIENT. The container is owned by the daemon and keeps running.
    """

    def run_process(self, spec, input_text=None, timeout=120.0):
        argv = self.wrap_command(spec)
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            out, err = proc.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return -1, out or "", (err or "") + f"\n[sandbox timed out after {timeout}s]"
        return proc.returncode, out or "", err or ""


class TestTheOldTimeoutFailsTheNewTests:
    def test_the_old_path_never_removed_the_container(self, monkeypatch):
        """The new test asserts the run's container is removed by its exact id. The old path
        issued no removal at all, so that assertion fails on it."""
        issued: list = []
        monkeypatch.setattr(cb, "_run_runtime",
                            lambda argv, timeout=None: issued.append(list(argv)))

        class Timing:
            returncode = -9

            def communicate(self, input=None, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired(cmd="docker", timeout=timeout)
                return "", ""

            def kill(self):
                pass

        monkeypatch.setattr(cb.subprocess, "Popen", lambda *a, **k: Timing())
        rc, _out, err = OldTimeoutBackend(runtime="docker").run_process(
            ProcessSpec(command=["sleep", "300"], network="none"), timeout=1)

        assert rc == -1 and "timed out" in err, "the old path did report the timeout"
        assert not [c for c in issued if len(c) > 1 and c[1] == "rm"], (
            "the reconstruction issued a removal; then it is not the old behaviour")
        assert _fails(lambda: _assert_removed(issued)), (
            "the old implementation must fail the 'container removed by its exact id' assertion")

    def test_the_old_path_had_no_identity_to_remove_it_by(self):
        """Even if the old path had tried, there was nothing exact to target: no product caller
        set spec.name, and there was no cidfile."""
        spec = ProcessSpec(command=["true"], network="none")
        assert spec.name is None
        argv = cb.ContainerBackend(runtime="docker").wrap_command(spec)
        assert "--cidfile" not in argv, (
            "wrap_command stays pure; the identity is added by run_process, and the old path "
            "added neither")
        assert _fails(lambda: _assert_has_identity(argv)), (
            "the old argv must fail the 'every run carries an exact identity' assertion")


def _assert_removed(issued):
    removals = [c for c in issued if len(c) > 1 and c[1] == "rm"]
    assert removals == [["docker", "rm", "-f", CID]]


def _assert_has_identity(argv):
    assert "--cidfile" in argv, "the run carries no cidfile"
    assert "--name" in argv, "the run carries no name"


# --------------------------------------------------------------------------------- R2

OLD_HARDENED_FLAGS = [
    "--rm", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
    "--user", "1000:1000", "--pids-limit", "256", "--memory", "512m", "--cpus", "1",
]


class TestTheOldMemoryFlagsFailTheNewTest:
    def test_the_old_flags_bound_memory_but_not_the_total(self):
        """`--memory` alone leaves the runtime's default swap allowance of twice the limit, which
        is how a 768 MiB allocation finished under a 512 MiB ceiling."""
        def new_assertion():
            assert "--memory-swap" in OLD_HARDENED_FLAGS
            i = OLD_HARDENED_FLAGS.index("--memory-swap")
            assert OLD_HARDENED_FLAGS[i + 1] == "512m"

        assert _fails(new_assertion), (
            "the old flag list must fail the 'total of memory and swap is the limit' assertion")

    def test_the_current_flags_pass_the_same_assertion(self):
        flags = list(cb._HARDENED_FLAGS)
        assert "--memory-swap" in flags
        assert flags[flags.index("--memory-swap") + 1] == flags[flags.index("--memory") + 1]

    def test_the_old_probe_recorded_no_engine_capability(self):
        """The old availability had no field for it, so nothing could report that an engine
        cannot hold the ceiling."""
        old_fields = {"available", "backend", "reason", "executable_path", "daemon_ok",
                      "image_available", "image_digest"}
        assert "memory_limit_enforceable" not in old_fields
        assert _fails(lambda: _assert_capability_recorded(old_fields))


def _assert_capability_recorded(fields):
    assert "memory_limit_enforceable" in fields


# --------------------------------------------------------------------------------- R3

def _old_reason(availability) -> str:
    """`explain_unavailable` as it was: the bare sentence, and nothing else.

        return "" if a.available else (a.reason or "no container runtime available")
    """
    return "" if availability.available else (availability.reason or "no container runtime available")


class TestTheOldRefusalFailsTheNewTests:
    OLD = SandboxAvailability(available=False, backend="none",
                              reason="no container runtime (docker or podman) found on PATH")

    def test_the_old_refusal_named_no_next_step(self):
        text = _old_reason(self.OLD)
        assert text == "no container runtime (docker or podman) found on PATH"
        assert _fails(lambda: _assert_actionable(text)), (
            "the old refusal must fail the 'names something executable' assertion")

    def test_the_old_refusal_named_no_recheck(self):
        assert _fails(lambda: _assert_has_recheck(_old_reason(self.OLD)))

    def test_the_old_refusal_could_not_tell_the_cases_apart(self):
        """A stopped daemon and a permission problem produced the same sentence, so no test could
        distinguish them."""
        stopped = SandboxAvailability(available=False, backend="docker", daemon_ok=False,
                                      reason="docker found but its daemon is not reachable")
        denied = SandboxAvailability(available=False, backend="docker", daemon_ok=False,
                                     reason="docker found but its daemon is not reachable")
        assert _old_reason(stopped) == _old_reason(denied)
        assert _fails(lambda: _assert_distinguishes(_old_reason(stopped), _old_reason(denied)))

    def test_the_current_refusal_passes_all_three(self):
        from agentnode_sdk.sandbox.refusal import classify

        text = classify(self.OLD, platform="linux").render()
        _assert_actionable(text)
        _assert_has_recheck(text)
        stopped = classify(SandboxAvailability(available=False, backend="docker", daemon_ok=False,
                                               reason="x", probe_error="Cannot connect"),
                           platform="linux")
        denied = classify(SandboxAvailability(available=False, backend="docker", daemon_ok=False,
                                              reason="x",
                                              probe_error="Got permission denied while trying to "
                                                          "connect to the Docker daemon socket"),
                          platform="linux")
        _assert_distinguishes(stopped.case.value, denied.case.value)


def _assert_actionable(text):
    assert "What you can do:" in text and ("install" in text.lower() or "start" in text.lower())


def _assert_has_recheck(text):
    assert "agentnode sandbox doctor" in text


def _assert_distinguishes(a, b):
    assert a != b, "these two situations produce the same answer"


# --------------------------------------------------------------------------------- the guard

def test_every_counter_check_actually_exercised_the_old_behaviour():
    """A counter-check that silently stopped running would quietly remove the proof.

    The number is written out rather than counted from the class, so deleting one fails here.
    """
    expected = 9
    found = sum(1 for name, obj in globals().items()
                if name.startswith("Test")
                for attr in dir(obj) if attr.startswith("test_"))
    assert found == expected, (
        f"{found} counter-checks are defined, {expected} are required; one was removed or added "
        "without updating this guard")


def test_the_helper_reports_a_broken_counter_check_rather_than_a_pass():
    """`_fails` must not turn its own bug into evidence."""
    with pytest.raises(AssertionError, match="proves nothing"):
        _fails(lambda: (_ for _ in ()).throw(TypeError("broken")))
    assert _fails(lambda: (_ for _ in ()).throw(AssertionError("expected")))
    assert not _fails(lambda: None)
