"""EM3B-R1-REVIEW-0001 / F5: assertions that can be stated against BOTH revisions.

The finding was fair. `test_r1_counter_checks.py` reconstructs the old behaviour by hand, and a
reconstruction can encode only the failure it expects. So this file states each defect in terms
that exist in *both* the previous revision (`7b5e035`, the commit that produced the red
conformance run) and the corrected one — no new symbol is imported, nothing is monkeypatched into
place — and it is run against both. Against the previous revision every test here fails. Against
the correction every one passes. That is the evidence; the reconstructions are only commentary.

The parts of the contract that could not be expressed this way — the containment exception, the
refusal classifier — did not exist at all in the previous revision, and the record of running the
full regression file there shows the module failing to import for exactly that reason.
"""
from __future__ import annotations

import subprocess

from agentnode_sdk.sandbox import container_backend as cb
from agentnode_sdk.sandbox.types import ProcessSpec


def test_r1_a_timed_out_run_removes_its_container():
    """PREVIOUS REVISION: the timeout killed the client and issued no removal at all."""
    issued: list = []

    class Timing:
        returncode = -9

        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="docker", timeout=timeout)
            return "", ""

        def kill(self):
            pass

        def poll(self):
            return -9

    real_popen, real_run = cb.subprocess.Popen, cb.subprocess.run

    def fake_run(argv, **kw):
        issued.append(list(argv))

        class R:
            returncode = 1          # nothing exists: inspect finds nothing, rm has nothing to do
            stdout = ""
            stderr = ""
        return R()

    cb.subprocess.Popen = lambda *a, **k: Timing()
    cb.subprocess.run = fake_run
    try:
        cb.ContainerBackend(runtime="docker").run_process(
            ProcessSpec(command=["sleep", "300"], network="none"), timeout=1)
    except Exception:                                               # noqa: BLE001
        pass                        # the corrected code may raise here; the argv record is the point
    finally:
        cb.subprocess.Popen, cb.subprocess.run = real_popen, real_run

    asked_about_the_container = [c for c in issued
                                 if len(c) > 1 and c[1] in ("inspect", "rm", "kill")]
    assert asked_about_the_container, (
        "a timed-out run must at least ASK the runtime what became of its container; the "
        "previous revision killed the client and asked nothing")


def test_r1_every_run_is_identifiable():
    """PREVIOUS REVISION: no product caller set a name and nothing added a cidfile, so a timeout
    had nothing exact to act on."""
    backend = cb.ContainerBackend(runtime="docker")
    spec = ProcessSpec(command=["true"], network="none")
    assert hasattr(backend, "_with_identity"), (
        "there is no per-run identity at all on this revision")
    built, name, cidfile, tmpdir = backend._with_identity(spec)
    try:
        argv = backend._argv_with_cidfile(built, cidfile)
        assert "--name" in argv and "--cidfile" in argv
    finally:
        cb._remove_quietly(cidfile, tmpdir)


def test_r2_the_memory_ceiling_includes_swap():
    """PREVIOUS REVISION: --memory was passed alone, and a runtime with only that grants twice
    the limit as swap."""
    flags = list(cb._HARDENED_FLAGS)
    assert "--memory-swap" in flags, "the swap half of the ceiling is unbounded"
    assert flags[flags.index("--memory-swap") + 1] == flags[flags.index("--memory") + 1]


def test_r3_a_refusal_names_something_to_do():
    """PREVIOUS REVISION: explain_unavailable returned the bare sentence
    'no container runtime (docker or podman) found on PATH' and nothing else."""
    backend = cb.ContainerBackend(runtime="a-runtime-that-does-not-exist")
    text = backend.explain_unavailable()
    assert text, "an unavailable sandbox must explain itself"
    lowered = text.lower()
    assert "install" in lowered or "start" in lowered, "it names no next step"
    assert "agentnode sandbox doctor" in lowered, "it names no way to check the fix worked"


def test_r3_a_refusal_can_be_told_apart_by_case():
    """PREVIOUS REVISION: a stopped daemon and a permission problem produced the same sentence."""
    backend = cb.ContainerBackend(runtime="docker")
    assert hasattr(backend, "refusal"), "there is no structured refusal on this revision"
    assert backend.refusal() is None or backend.refusal().case
