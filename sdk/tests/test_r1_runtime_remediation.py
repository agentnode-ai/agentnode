"""EM-3B-R1: the three defects the conformance suite measured, each with a direct regression.

Run `33751686586` found them in a real container:

* a wall-clock timeout returned its documented signal while the container kept running;
* a 768 MiB allocation finished with exit code 0 under a declared 512 MiB ceiling;
* the refusal said only "no container runtime (docker or podman) found on PATH".

Every test below fails against the implementation that produced that run; the proof of that is in
`test_r1_counter_checks.py`, which reconstructs the old behaviour and shows these assertions
failing on it. A test that passes on the old code proves nothing about the fix.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import types

import pytest

from agentnode_sdk.sandbox import container_backend as cb
from agentnode_sdk.sandbox.refusal import RefusalCase, classify
from agentnode_sdk.sandbox.types import (
    ProcessSpec,
    SandboxAvailability,
    SandboxContainmentError,
)

CID = "c0ffee" * 10 + "abcd"          # 64 hex chars, like a real container id
OTHER = "dead" * 16                    # a concurrent, unrelated container


class FakeRuntime:
    """Stands in for docker/podman. Records every command, so 'exactly this container' is testable."""

    def __init__(self, *, exists=(), rm_rc=0, rm_raises=False, inspect_raises=False,
                 gone_after_rm=True, names=None):
        self.calls: list[list] = []
        self._exists = set(exists)
        # A container's name belongs to the container: removing it by id takes the name with it.
        self._names = dict(names or {"run-x": CID})
        self._rm_rc = rm_rc
        self._rm_raises = rm_raises
        self._inspect_raises = inspect_raises
        self._gone_after_rm = gone_after_rm

    def __call__(self, argv, timeout=None):
        self.calls.append(list(argv))
        verb = argv[1] if len(argv) > 1 else ""
        target = argv[-1]
        if verb == "inspect":
            if self._inspect_raises:
                return None
            found = target in self._exists
            return types.SimpleNamespace(returncode=0 if found else 1,
                                         stdout=(CID if found else ""), stderr="")
        if verb == "rm":
            if self._rm_raises:
                return None
            if self._gone_after_rm and self._rm_rc == 0:
                self._exists.discard(target)
                for name, ident in self._names.items():
                    if target in (name, ident):
                        self._exists.discard(name)
                        self._exists.discard(ident)
            return types.SimpleNamespace(returncode=self._rm_rc, stdout="",
                                         stderr="" if self._rm_rc == 0 else "cannot remove")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


class FakeProc:
    """A runtime client that has already been killed."""

    def __init__(self, alive_after_kill=False):
        self._alive = alive_after_kill
        self.killed = False

    def kill(self):
        self.killed = True

    def communicate(self, timeout=None):
        return "partial stdout", "some stderr"

    def poll(self):
        return None if self._alive else -9


def _cidfile(tmp_path, value=CID):
    path = tmp_path / "cid"
    if value is not None:
        path.write_text(value, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------- R1: the payload must end

class TestTimeoutEndsThePayload:
    def test_the_timed_out_container_is_removed_by_its_exact_id(self, tmp_path, monkeypatch):
        fake = FakeRuntime(exists={"run-x", CID})
        monkeypatch.setattr(cb, "_run_runtime", fake)
        backend = cb.ContainerBackend(runtime="docker")
        rc, out, err = backend._end_timed_out_run(FakeProc(), "docker", "run-x",
                                                  _cidfile(tmp_path), 5.0)
        assert rc == -1 and "timed out after 5.0s" in err
        removals = [c for c in fake.calls if c[1] == "rm"]
        assert removals == [["docker", "rm", "-f", CID]], (
            "the timeout must remove exactly the container this run created, by its id")

    def test_a_concurrent_unrelated_container_is_never_touched(self, tmp_path, monkeypatch):
        fake = FakeRuntime(exists={"run-x", CID, OTHER})
        monkeypatch.setattr(cb, "_run_runtime", fake)
        cb.ContainerBackend(runtime="docker")._end_timed_out_run(
            FakeProc(), "docker", "run-x", _cidfile(tmp_path), 5.0)
        assert all(OTHER not in c for c in fake.calls), "another container was addressed"

    def test_a_removal_that_fails_and_leaves_it_running_is_a_containment_error(
            self, tmp_path, monkeypatch):
        fake = FakeRuntime(exists={"run-x", CID}, rm_rc=1, gone_after_rm=False)
        monkeypatch.setattr(cb, "_run_runtime", fake)
        with pytest.raises(SandboxContainmentError, match="still there"):
            cb.ContainerBackend(runtime="docker")._end_timed_out_run(
                FakeProc(), "docker", "run-x", _cidfile(tmp_path), 5.0)

    def test_a_runtime_that_does_not_answer_the_removal_is_a_containment_error(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(cb, "_run_runtime", FakeRuntime(exists={"run-x", CID}, rm_raises=True))
        with pytest.raises(SandboxContainmentError, match="did not answer"):
            cb.ContainerBackend(runtime="docker")._end_timed_out_run(
                FakeProc(), "docker", "run-x", _cidfile(tmp_path), 5.0)

    def test_a_residue_after_a_successful_removal_is_a_containment_error(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(cb, "_REAP_TIMEOUT", 0.3)
        monkeypatch.setattr(cb, "_run_runtime",
                            FakeRuntime(exists={"run-x", CID}, rm_rc=0, gone_after_rm=False))
        with pytest.raises(SandboxContainmentError, match="still present"):
            cb.ContainerBackend(runtime="docker")._end_timed_out_run(
                FakeProc(), "docker", "run-x", _cidfile(tmp_path), 5.0)

    def test_a_client_that_will_not_die_is_a_containment_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cb, "_run_runtime", FakeRuntime(exists={"run-x", CID}))
        with pytest.raises(SandboxContainmentError, match="would not terminate"):
            cb.ContainerBackend(runtime="docker")._end_timed_out_run(
                FakeProc(alive_after_kill=True), "docker", "run-x", _cidfile(tmp_path), 5.0)

    def test_two_identities_that_disagree_stop_everything(self, tmp_path, monkeypatch):
        class Disagreeing(FakeRuntime):
            def __call__(self, argv, timeout=None):
                self.calls.append(list(argv))
                if argv[1] == "inspect":
                    return types.SimpleNamespace(returncode=0, stdout=OTHER, stderr="")
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        fake = Disagreeing()
        monkeypatch.setattr(cb, "_run_runtime", fake)
        with pytest.raises(SandboxContainmentError, match="different containers"):
            cb.ContainerBackend(runtime="docker")._end_timed_out_run(
                FakeProc(), "docker", "run-x", _cidfile(tmp_path), 5.0)
        assert not [c for c in fake.calls if c[1] == "rm"], (
            "nothing may be removed while the identity is in doubt")

    def test_a_timeout_before_the_container_existed_is_an_ordinary_timeout(
            self, tmp_path, monkeypatch):
        fake = FakeRuntime(exists=set())
        monkeypatch.setattr(cb, "_run_runtime", fake)
        rc, _out, err = cb.ContainerBackend(runtime="docker")._end_timed_out_run(
            FakeProc(), "docker", "run-x", str(tmp_path / "cid"), 5.0)
        assert rc == -1 and "timed out" in err
        assert not [c for c in fake.calls if c[1] == "rm"]

    def test_a_name_that_exists_without_a_resolvable_identity_is_a_containment_error(
            self, tmp_path, monkeypatch):
        class NameOnly(FakeRuntime):
            def __call__(self, argv, timeout=None):
                self.calls.append(list(argv))
                if argv[1] == "inspect":
                    # present, but the runtime returns no id for it
                    return types.SimpleNamespace(returncode=0, stdout="", stderr="")
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(cb, "_run_runtime", NameOnly())
        monkeypatch.setattr(cb.ContainerBackend, "_exists", lambda self, rt, ident: True)
        with pytest.raises(SandboxContainmentError, match="no identity could be resolved"):
            cb.ContainerBackend(runtime="docker")._end_timed_out_run(
                FakeProc(), "docker", "run-x", str(tmp_path / "cid"), 5.0)

    def test_a_supplied_name_is_never_the_target(self):
        """EM3B-R1-REVIEW-0001 / F3: if a container of the caller name already exists, a timeout
        firing before the cidfile is written would have resolved and removed THAT one."""
        backend = cb.ContainerBackend(runtime="docker")
        spec, name, cidfile, tmpdir = backend._with_identity(
            ProcessSpec(command=["true"], network="none", name="agentnode-mcp-weather"))
        try:
            assert name != "agentnode-mcp-weather"
            assert name.startswith("agentnode-mcp-weather-"), "a caller prefix still matches"
            second = backend._with_identity(
                ProcessSpec(command=["true"], network="none", name="agentnode-mcp-weather"))
            cb._remove_quietly(second[2], second[3])
            assert second[1] != name, "two runs never share a target"
        finally:
            cb._remove_quietly(cidfile, tmpdir)

    def test_a_pre_existing_container_of_the_same_name_is_never_removed(
            self, tmp_path, monkeypatch):
        pre_existing = "agentnode-mcp-weather"
        fake = FakeRuntime(exists={pre_existing}, names={pre_existing: "someone-elses-id"})
        monkeypatch.setattr(cb, "_run_runtime", fake)
        backend = cb.ContainerBackend(runtime="docker")
        _spec, name, cidfile, tmpdir = backend._with_identity(
            ProcessSpec(command=["true"], network="none", name=pre_existing))
        try:
            rc, _out, err = backend._end_timed_out_run(FakeProc(), "docker", name, cidfile, 5.0)
        finally:
            cb._remove_quietly(cidfile, tmpdir)
        assert rc == -1 and "timed out" in err
        assert not [c for c in fake.calls if c[1] == "rm"], (
            "a pre-existing container with the caller name was addressed")
        assert pre_existing in fake._exists, "it must still be there"

    def test_every_run_carries_an_exact_identity(self):
        backend = cb.ContainerBackend(runtime="docker")
        spec, name, cidfile, tmpdir = backend._with_identity(
            ProcessSpec(command=["true"], network="none"))
        try:
            assert spec.name == name and name.startswith("agentnode-run-")
            assert not os.path.exists(cidfile), "the runtime writes the cidfile; it must not exist"
            argv = backend._argv_with_cidfile(spec, cidfile)
            assert argv[2:4] == ["--cidfile", cidfile]
            assert argv.index("--cidfile") < argv.index(backend._image)
        finally:
            cb._remove_quietly(cidfile, tmpdir)

    def test_an_ordinary_run_is_untouched(self, monkeypatch):
        seen = {}

        class OkProc:
            def communicate(self, input=None, timeout=None):
                seen["input"] = input
                return "output", ""
            returncode = 0

        monkeypatch.setattr(cb.subprocess, "Popen", lambda *a, **k: OkProc())
        rc, out, err = cb.ContainerBackend(runtime="docker").run_process(
            ProcessSpec(command=["true"], network="none"), input_text="payload")
        assert (rc, out, err) == (0, "output", "") and seen["input"] == "payload"


# --------------------------------------------------------------------- R2: the ceiling must bind

class TestMemoryCeiling:
    def test_the_total_of_memory_and_swap_is_the_declared_limit(self):
        argv = cb.ContainerBackend(runtime="docker").wrap_command(
            ProcessSpec(command=["true"], network="none"))
        memory = argv[argv.index("--memory") + 1]
        assert "--memory-swap" in argv, (
            "without --memory-swap the runtime grants twice the memory as swap, which is how a "
            "768 MiB allocation finished under a 512 MiB limit")
        assert argv[argv.index("--memory-swap") + 1] == memory, (
            "the swap allowance must equal the memory limit, so the total IS the limit")

    def test_the_engine_capability_is_recorded_and_never_assumed(self, monkeypatch):
        def info(argv, timeout=None):
            if "{{.OSType}}|{{.MemoryLimit}}|{{.SwapLimit}}" in argv:
                return types.SimpleNamespace(returncode=0, stdout="linux|true|false", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(cb, "_run_runtime", info)
        engine_os, enforceable = cb.ContainerBackend(runtime="docker")._engine_facts("docker")
        assert engine_os == "linux"
        assert enforceable is False, "an engine without swap accounting cannot hold the ceiling"

    def test_an_engine_that_says_nothing_is_unknown_not_capable(self, monkeypatch):
        monkeypatch.setattr(cb, "_run_runtime", lambda argv, timeout=None: None)
        assert cb.ContainerBackend(runtime="docker")._engine_facts("docker") == ("", None)

    def test_podman_is_asked_in_its_own_words(self, monkeypatch):
        """Podman has no .MemoryLimit/.SwapLimit -- those are Docker fields. Asking it in
        Docker words and calling the empty answer "cannot enforce" would refuse every Podman
        host; under cgroup v2 the memory-and-swap ceiling is accounted for by default."""
        def info(argv, timeout=None):
            if "{{.Host.CgroupsVersion}}" in argv:
                return types.SimpleNamespace(returncode=0, stdout="v2", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="||", stderr="")

        monkeypatch.setattr(cb, "_run_runtime", info)
        assert cb.ContainerBackend(runtime="podman")._engine_facts("podman") == ("linux", True)

    def test_cgroup_v1_podman_cannot_hold_the_ceiling(self, monkeypatch):
        def info(argv, timeout=None):
            if "{{.Host.CgroupsVersion}}" in argv:
                return types.SimpleNamespace(returncode=0, stdout="v1", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="||", stderr="")

        monkeypatch.setattr(cb, "_run_runtime", info)
        assert cb.ContainerBackend(runtime="podman")._engine_facts("podman")[1] is False

    @pytest.mark.parametrize("state", ["false", "unknown"])
    def test_an_engine_that_cannot_hold_the_ceiling_is_not_available(self, monkeypatch, state):
        """EM3B-R1-REVIEW-0001 / F2: fail-closed BEFORE anything runs, not a note in a report."""
        def info(argv, timeout=None):
            if "{{.OSType}}|{{.MemoryLimit}}|{{.SwapLimit}}" in argv:
                return types.SimpleNamespace(
                    returncode=0,
                    stdout="linux|true|false" if state == "false" else "linux", stderr="")
            if "{{.Host.CgroupsVersion}}" in argv:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(cb.shutil, "which", lambda x: "/usr/bin/docker")
        monkeypatch.setattr(cb, "_run_runtime", info)
        av = cb.ContainerBackend(runtime="docker").check_available()
        assert av.available is False
        assert "memory ceiling" in av.reason

    @pytest.mark.skipif(os.environ.get("AGENTNODE_SANDBOX_E2E") != "1",
                        reason="set AGENTNODE_SANDBOX_E2E=1 (needs a container runtime) to run")
    def test_real_container_cannot_exceed_the_ceiling(self):
        backend = cb.ContainerBackend()
        if not backend.check_available().available:
            pytest.skip("no container runtime + pinned image available")
        code = ("held = []\n"
                "for _ in range(24):\n"
                "    held.append(bytearray(32 * 1024 * 1024))\n"
                "print('ALLOCATED')\n")
        rc, out, _err = backend.run_process(
            ProcessSpec(command=["python", "-c", code], network="none"), timeout=120)
        assert rc != 0 and "ALLOCATED" not in out, (
            f"768 MiB was allocated under a 512 MiB ceiling (rc={rc})")

    @pytest.mark.skipif(os.environ.get("AGENTNODE_SANDBOX_E2E") != "1",
                        reason="set AGENTNODE_SANDBOX_E2E=1 (needs a container runtime) to run")
    def test_real_container_below_the_ceiling_still_runs(self):
        backend = cb.ContainerBackend()
        if not backend.check_available().available:
            pytest.skip("no container runtime + pinned image available")
        rc, out, _err = backend.run_process(
            ProcessSpec(command=["python", "-c",
                                 "held = bytearray(64 * 1024 * 1024); print('FINE')"],
                        network="none"), timeout=120)
        assert rc == 0 and "FINE" in out



# --------------------------------------------------------------- the resistant payload, for real

#: Ignores every signal a process is allowed to ignore, then sleeps far past any deadline. Without
#: an active removal it keeps running: SIGTERM, SIGINT, SIGHUP and SIGQUIT are all swallowed.
#: SIGKILL cannot be ignored by anything, which is the whole point of what is being measured.
IGNORING_PAYLOAD = (
    "import signal, sys, time\n"
    "for name in ('SIGTERM', 'SIGINT', 'SIGHUP', 'SIGQUIT', 'SIGUSR1', 'SIGUSR2'):\n"
    "    s = getattr(signal, name, None)\n"
    "    if s is not None:\n"
    "        try:\n"
    "            signal.signal(s, signal.SIG_IGN)\n"
    "        except Exception:\n"
    "            pass\n"
    "print('IGNORING', flush=True)\n"
    "sys.stdout.flush()\n"
    "time.sleep(600)\n"
)


def _runtime_of(backend) -> str:
    return backend.check_available().backend


def _watch_for_container(runtime: str, prefix: str, seconds: float = 20.0):
    """Poll until a container whose name carries `prefix` is RUNNING; return (name, id).

    The run's real name is generated inside the backend with a fresh suffix, which is the point
    of the identity fix -- so the test watches for it rather than assuming it.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        listed = subprocess.run(
            [runtime, "ps", "--filter", f"name={prefix}", "--format", "{{.Names}} {{.ID}}"],
            capture_output=True, text=True)
        for line in listed.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].startswith(prefix):
                return parts[0], parts[1]
        time.sleep(0.25)
    return None, None


def _absent(runtime: str, ident: str) -> bool:
    r = subprocess.run([runtime, "inspect", "--format", "{{.Id}}", ident],
                       capture_output=True, text=True)
    return r.returncode != 0 or not r.stdout.strip()


class TestTheResistantPayloadForReal:
    """EM-3B-R1 acceptance: a payload that would keep running without an active removal.

    The ten observations the authorised run has to make are asserted here in order, each one
    named where it is made. Nothing is inferred from a duration.
    """

    @pytest.mark.skipif(os.environ.get("AGENTNODE_SANDBOX_E2E") != "1",
                        reason="set AGENTNODE_SANDBOX_E2E=1 (needs a container runtime) to run")
    def test_real_timeout_ends_a_payload_that_ignores_it(self):
        backend = cb.ContainerBackend()
        if not backend.check_available().available:
            pytest.skip("no container runtime + pinned image available")
        runtime = _runtime_of(backend)
        name = "agentnode-r1-ignoring-payload"
        bystander = "agentnode-r1-bystander"

        # (8) a concurrent unrelated container, started before the run and inspected after it.
        subprocess.run([runtime, "rm", "-f", bystander], capture_output=True)
        started = subprocess.run(
            [runtime, "run", "-d", "--name", bystander, cb._BASE_IMAGE,
             "python", "-c", "import time; time.sleep(600)"],
            capture_output=True, text=True)
        assert started.returncode == 0, f"could not start the bystander: {started.stderr[:200]}"
        bystander_id = started.stdout.strip()
        watcher: list = [None, None]

        def watch():
            watcher[0], watcher[1] = _watch_for_container(runtime, name)

        thread = threading.Thread(target=watch, daemon=True)
        try:
            thread.start()
            rc, out, err = backend.run_process(
                ProcessSpec(command=["python", "-c", IGNORING_PAYLOAD], network="none",
                            name=name), timeout=8)
            thread.join(timeout=5)
            observed_name, observed_id = watcher

            # (1) it really started, in a real container, and said so from inside.
            assert "IGNORING" in out, (
                f"the payload never reported starting; stdout={out[:200]!r} stderr={err[:200]!r}")
            # (1) and the runtime saw it running under this run's own generated name.
            assert observed_name and observed_id, (
                "the runtime never listed a running container for this run")
            assert observed_name.startswith(name) and observed_name != name, (
                f"the run must carry its own generated identity, got {observed_name!r}")

            # (3)(9) the wall clock was reached, and the timeout is what came back -- AFTER the
            # backend verified absence, which is why the checks below hold the moment it returns.
            assert rc == -1, f"expected the documented timeout code, got {rc}"
            assert "timed out" in err

            # (4)(5)(6) not merely the client: the exact container of this run is gone, by BOTH
            # its id and its name, checked immediately after the call returned.
            assert _absent(runtime, observed_id), (
                f"container {observed_id} is still present after its timeout")
            assert _absent(runtime, observed_name), (
                f"container {observed_name} is still present after its timeout")

            # (7) nothing of it is left in any state, running or exited.
            listed = subprocess.run([runtime, "ps", "-a", "--filter", f"name={name}",
                                     "--format", "{{.Names}}"], capture_output=True, text=True)
            assert name not in listed.stdout, "the payload survived its own timeout"

            # (8) the bystander was never touched.
            alive = subprocess.run(
                [runtime, "inspect", "--format", "{{.State.Running}}", bystander],
                capture_output=True, text=True)
            assert alive.returncode == 0 and alive.stdout.strip() == "true", (
                f"an unrelated container was affected: {alive.stdout.strip()!r} "
                f"{alive.stderr.strip()[:160]!r}")

            # (10) and a cleanup that does not succeed is NOT reported as an ordinary timeout.
            # The same resistant payload, with the removal made to fail: the backend must raise
            # rather than return -1. The container is cleaned up by hand afterwards.
            leaked_prefix = "agentnode-r1-containment"
            real_run_runtime = cb._run_runtime

            def refuse_removal(argv, timeout=None):
                if len(argv) > 1 and argv[1] == "rm":
                    return types.SimpleNamespace(returncode=1, stdout="",
                                                 stderr="injected: removal refused")
                return real_run_runtime(argv, timeout=timeout)

            cb._run_runtime = refuse_removal
            try:
                with pytest.raises(SandboxContainmentError):
                    backend.run_process(
                        ProcessSpec(command=["python", "-c", IGNORING_PAYLOAD], network="none",
                                    name=leaked_prefix), timeout=8)
            finally:
                cb._run_runtime = real_run_runtime
                leaked = subprocess.run(
                    [runtime, "ps", "-a", "--filter", f"name={leaked_prefix}",
                     "--format", "{{.Names}}"], capture_output=True, text=True)
                for leftover in leaked.stdout.split():
                    subprocess.run([runtime, "rm", "-f", leftover], capture_output=True)
        finally:
            subprocess.run([runtime, "rm", "-f", bystander_id or bystander], capture_output=True)
            subprocess.run([runtime, "rm", "-f", name], capture_output=True)


# --------------------------------------------------------------------- R3: a refusal with a way out

def _unavailable(**kw):
    base = {"available": False, "backend": "none", "reason": "x"}
    base.update(kw)
    return SandboxAvailability(**base)


class TestRefusalCarriesAWayOut:
    @pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
    def test_not_installed_offers_something_executable(self, platform):
        r = classify(_unavailable(), platform=platform)
        assert r.case is RefusalCase.NOT_INSTALLED
        assert r.actions and all(a.command or a.url for a in r.actions)
        assert r.recheck and "prevented" not in r.headline.lower()
        assert "did not run" in r.prevented

    def test_a_stopped_daemon_is_not_the_same_as_a_missing_one(self):
        r = classify(_unavailable(backend="docker", daemon_ok=False,
                                  probe_error="Cannot connect to the Docker daemon"),
                     platform="linux")
        assert r.case is RefusalCase.NOT_RUNNING
        assert any("systemctl start docker" in (a.command or "") for a in r.actions)

    def test_a_permission_problem_is_its_own_case(self):
        r = classify(_unavailable(backend="docker", daemon_ok=False,
                                  probe_error="Got permission denied while trying to connect to "
                                              "the Docker daemon socket"),
                     platform="linux")
        assert r.case is RefusalCase.NOT_PERMITTED
        assert any("podman" in (a.command or "").lower() for a in r.actions)
        joined = " ".join((a.text + (a.command or "")).lower() for a in r.actions)
        assert "docker group" not in joined and "usermod" not in joined, (
            "joining the docker group is administrator-equivalent and is never offered in passing")

    def test_an_engine_in_the_wrong_mode_is_its_own_case(self):
        r = classify(_unavailable(backend="docker", daemon_ok=True, engine_os="windows"),
                     platform="win32")
        assert r.case is RefusalCase.INCOMPATIBLE
        assert "linux" in r.details.lower() and r.actions

    def test_a_device_with_no_local_sandbox_still_offers_a_clean_stop_and_a_person(self):
        """EM3B-R1-REVIEW-0001 / F4: this case carried no actions at all, which broke the rule
        that every refusal carries a way out. Stopping cleanly, and being able to say you need
        this, ARE things a person can do."""
        r = classify(_unavailable(), platform="android")
        assert r.case is RefusalCase.PLATFORM_UNSUPPORTED
        assert r.actions, "every refusal carries something the person can do -- no exceptions"
        text = r.render().lower()
        assert "nothing was started" in text and "stop here safely" in text
        assert "issues" in text, "and a way to reach a person"
        for forbidden in ("run it anyway", "disable the sandbox", "remote sandbox now",
                          "run it unprotected"):
            assert forbidden not in text

    def test_a_missing_image_and_a_placeholder_build_are_different(self):
        ok_engine = dict(backend="docker", daemon_ok=True, engine_os="linux",
                         memory_limit_enforceable=True)
        missing = classify(_unavailable(**ok_engine))
        placeholder = classify(_unavailable(**ok_engine), placeholder=True)
        assert missing.case is RefusalCase.IMAGE_MISSING
        assert any("agentnode sandbox pull" == a.command for a in missing.actions)
        assert placeholder.case is RefusalCase.IMAGE_PLACEHOLDER
        assert all("pull" not in (a.command or "") for a in placeholder.actions)

    def test_an_action_that_does_not_apply_here_is_not_shown(self):
        text = classify(_unavailable(), platform="win32").render()
        assert "apt install" not in text and "systemctl" not in text

    def test_every_refusal_names_a_recheck_and_an_action(self):
        for r in (classify(_unavailable(), platform="linux"),
                  classify(_unavailable(backend="docker", daemon_ok=False), platform="linux"),
                  classify(_unavailable(backend="docker", daemon_ok=True, engine_os="linux"),
                           platform="linux"),
                  classify(_unavailable(backend="docker", daemon_ok=True, engine_os="linux",
                                        memory_limit_enforceable=True), platform="linux"),
                  classify(_unavailable(), platform="android")):
            assert r.actions, f"{r.case.value} carries no action"
            assert "agentnode sandbox doctor" in r.render()

    def test_an_engine_that_cannot_hold_the_ceiling_is_its_own_refusal(self):
        """EM3B-R1-REVIEW-0001 / F2: a ceiling that may not bind is no ceiling."""
        for state in (False, None):
            r = classify(_unavailable(backend="docker", daemon_ok=True, engine_os="linux",
                                      memory_limit_enforceable=state), platform="linux")
            assert r.case is RefusalCase.MEMORY_CEILING_UNENFORCEABLE
            assert r.actions and "cgroup" in r.render().lower()

    def test_a_refusal_without_a_way_out_cannot_be_constructed(self):
        from agentnode_sdk.sandbox.refusal import Refusal

        with pytest.raises(ValueError, match="something the person can do"):
            Refusal(RefusalCase.NOT_INSTALLED, "headline", "prevented", actions=())

    def test_an_available_sandbox_produces_no_refusal(self):
        assert classify(SandboxAvailability(available=True, backend="docker", reason="")) is None

    def test_the_sdk_renders_the_structured_refusal(self, monkeypatch):
        backend = cb.ContainerBackend(runtime="docker")
        monkeypatch.setattr(backend, "check_available", lambda force=False: _unavailable())
        text = backend.explain_unavailable()
        assert "What you can do:" in text and "agentnode sandbox doctor" in text

    def test_a_recheck_can_actually_re_ask(self, monkeypatch):
        backend = cb.ContainerBackend(runtime="docker")
        calls = []
        monkeypatch.setattr(backend, "_probe",
                            lambda: calls.append(1) or _unavailable())
        backend.check_available()
        backend.check_available()
        backend.check_available(force=True)
        assert len(calls) == 2, "a re-check has to be able to ask again, not read the old answer"
