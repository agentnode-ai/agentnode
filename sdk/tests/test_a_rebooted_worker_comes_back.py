"""The worker did not survive a reboot, and the reason was its own hardening.

Podman puts `net.ipv4.ping_group_range` on every rootless container by default, and crun
writes it inside the container's network namespace. The unit runs with
`ProtectKernelTunables=yes`, which mounts /proc/sys read-only for the service and everything
it spawns. So the write fails with EROFS and no container starts -- including the one the
worker runs before it serves, to prove its memory ceiling binds. The worker then refuses,
correctly, and the host never comes back.

Measured on the machine, not inferred: with that unit property the pinned image fails to
start with exactly that error; with the property removed it does not.

The cheap fix was `ProtectKernelTunables=no`. That would make every kernel tunable writable
by the one account on the machine that runs foreign code, to save a sysctl a sandbox with no
network does not need. So the write is removed instead, and these tests exist mostly to stop
the cheap fix being made later.

A second, unrelated cause of the same symptom: the installer created the home directories
podman needs only via `useradd --create-home`, which runs only when the account is absent.

NOT A HOST-ISOLATION TEST, and not the reboot itself: these are the file-level guards. The
reboot is measured on the two machines.
"""
from __future__ import annotations

from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "separate-worker-host"
INSTALLER = (DEPLOY / "install-worker-host.sh").read_text(encoding="utf-8")
UNIT = (DEPLOY / "worker-host.service").read_text(encoding="utf-8")


def code_of(text: str) -> str:
    """The script without its comments, so a test cannot be satisfied by prose about it.

    Backslash continuations are joined: a shell command spread over four lines is one
    command, and a test that reads it as four would be reading something the shell never
    sees.
    """
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if out and out[-1].endswith("\\"):
            out[-1] = out[-1][:-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
    return "\n".join(out)


class TestTheHardeningWasNotTradedAway:
    """If a later change makes the runtime work by loosening the unit, this is what should
    go red."""

    def test_the_unit_still_protects_kernel_tunables(self):
        assert "ProtectKernelTunables=yes" in code_of(UNIT)

    def test_and_still_protects_the_rest(self):
        for directive in ("ProtectSystem=strict", "ProtectKernelModules=yes",
                          "ProtectClock=yes", "ProtectProc=invisible",
                          "RestrictSUIDSGID=yes", "RestrictRealtime=yes",
                          "LockPersonality=yes", "SystemCallArchitectures=native"):
            assert directive in code_of(UNIT), directive

    def test_nothing_punches_a_hole_in_proc_sys(self):
        """`ReadWritePaths=/proc/sys/...` would be the other way to make crun's write
        succeed, and it would give the account back the tunables wholesale."""
        assert "/proc/sys" not in code_of(UNIT)


class TestTheWriteIsRemovedInstead:

    def test_the_installer_writes_a_containers_conf(self):
        assert "containers.conf" in code_of(INSTALLER)

    def test_that_empties_podmans_default_sysctls(self):
        assert "default_sysctls = []" in INSTALLER

    def test_it_goes_where_rootless_podman_will_read_it(self):
        assert '"$HOME_DIR/.config/containers/containers.conf"' in code_of(INSTALLER)

    def test_and_belongs_to_the_account_that_runs_the_containers(self):
        code = code_of(INSTALLER)
        assert 'chown "$WORKER_USER:$WORKER_USER" "$HOME_DIR/.config/containers/containers.conf"' in code
        assert 'chmod 0600 "$HOME_DIR/.config/containers/containers.conf"' in code

    def test_it_is_written_before_the_image_is_pulled(self):
        """A config the pull cannot see is a config that does not apply to the pull."""
        code = code_of(INSTALLER)
        assert code.index("containers.conf") < code.index("podman pull")


class TestTheHomeIsMadeWhateverTheAccountsHistory:
    """`useradd --create-home` runs only when the account does not exist, so a host whose
    account survived a wipe but whose home did not failed at the image pull."""

    def test_the_directories_podman_needs_are_created_explicitly(self):
        code = code_of(INSTALLER)
        for needed in ('"$HOME_DIR/.config"', '"$HOME_DIR/.config/containers"',
                       '"$HOME_DIR/.local/share"', '"$HOME_DIR/.local/share/containers"'):
            assert needed in code, needed

    def test_with_install_d_which_is_idempotent(self):
        code = code_of(INSTALLER)
        line = [ln for ln in code.splitlines() if '"$HOME_DIR/.config"' in ln]
        assert line and line[0].lstrip().startswith("install -d"), (
            "created with something that would fail or re-own on a second run")

    def test_and_owned_by_the_worker_not_by_root(self):
        code = code_of(INSTALLER)
        block = code[code.index('"$HOME_DIR/.config"') - 200:code.index('"$HOME_DIR/.config"')]
        assert '-o "$WORKER_USER"' in block

    def test_they_are_made_before_the_pull_needs_them(self):
        code = code_of(INSTALLER)
        assert code.index('"$HOME_DIR/.config"') < code.index("podman pull")


class TestTheCeilingProofIsUntouched:
    """The worker must still refuse when it cannot prove its ceiling binds. A repair that
    made the container start by making the proof optional would pass every test above."""

    def test_the_proof_still_runs_before_the_door_opens(self):
        import inspect

        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        assert "prove_its_ceilings()" in source
        assert "CannotHoldItsLimits" in source

    def test_a_container_that_cannot_start_is_still_not_a_pass(self):
        import inspect

        from agentnode_sdk.worker import local

        source = inspect.getsource(local.LocalWorker.prove_its_ceilings)
        assert "held=False" in source
        assert "never started" in source

    def test_the_ceiling_itself_did_not_move(self):
        from agentnode_sdk.sandbox import container_backend

        flags = container_backend._HARDENED_FLAGS
        assert "--memory" in flags and "512m" in flags
        assert "--memory-swap" in flags
        assert flags[flags.index("--memory-swap") + 1] == "512m", (
            "memory without memory-swap is a ceiling that swap walks through")

    def test_and_the_probe_still_asks_for_more_than_the_ceiling(self):
        from agentnode_sdk.worker import local

        assert local.LocalWorker.PROOF_MEGABYTES > 512
