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

import inspect
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


class TestAnUpgradedHostGetsTheFixToo:
    """The installer writing the file is not enough, and the pair proved it.

    After upgrading the worker rather than reinstalling it, the host still had no
    containers.conf -- so it would have failed at the next reboot exactly as before. An
    upgrade carries CODE; it does not re-run installer steps. A fix that only reaches new
    installations is not delivered, and upgrading is how an existing deployment gets one.
    """

    def test_the_worker_ensures_it_itself(self):
        from agentnode_sdk.sandbox import container_backend

        assert hasattr(container_backend, "ask_the_runtime_for_no_kernel_tunables")

    def test_and_serve_calls_it_before_any_container(self):
        import inspect

        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        assert "ask_the_runtime_for_no_kernel_tunables" in source
        assert source.index("ask_the_runtime_for_no_kernel_tunables") < source.index(
            "prove_its_ceilings")

    def test_it_writes_what_is_needed(self, tmp_path):
        from agentnode_sdk.sandbox import container_backend

        written = container_backend.ask_the_runtime_for_no_kernel_tunables(str(tmp_path))
        assert written
        body = (tmp_path / ".config" / "containers" / "containers.conf").read_text(
            encoding="utf-8")
        assert "[containers]" in body
        assert "default_sysctls = []" in body

    def test_it_does_not_overwrite_somebody_elses(self, tmp_path):
        from agentnode_sdk.sandbox import container_backend

        where = tmp_path / ".config" / "containers"
        where.mkdir(parents=True)
        (where / "containers.conf").write_text("# mine\n", encoding="utf-8")
        assert container_backend.ask_the_runtime_for_no_kernel_tunables(str(tmp_path)) == ""
        assert (where / "containers.conf").read_text(encoding="utf-8") == "# mine\n"

    def test_and_a_home_it_cannot_write_is_not_fatal(self, tmp_path):
        """The ceiling proof is the right place to refuse from, not this."""
        from agentnode_sdk.sandbox import container_backend

        blocked = tmp_path / "a-file-not-a-directory"
        blocked.write_text("", encoding="utf-8")
        assert container_backend.ask_the_runtime_for_no_kernel_tunables(str(blocked)) == ""


class TestTheRuntimeThatLostItsNamespace:
    """The second thing a reboot broke, once the first was fixed.

    Rootless podman keeps state referring to the user namespace of the boot it was made in.
    After a reboot the first container cannot be created at all:
    `crun: mount proc to proc: Operation not permitted`. Measured on the pair that this is
    NOT the unit's hardening -- ProtectProc, ProtectKernelTunables, ProtectSystem and
    PrivateTmp were each relaxed on the real unit, one at a time, and it failed identically
    with every one. `podman system migrate` fixed it and the worker came up.
    """

    def test_the_recovery_exists_and_is_documented_as_narrow(self):
        from agentnode_sdk.sandbox import container_backend

        source = inspect.getsource(
            container_backend.recover_a_runtime_that_lost_its_namespace)
        assert "system" in source and "migrate" in source

    def test_it_declines_for_a_runtime_that_is_not_podman(self):
        from agentnode_sdk.sandbox import container_backend

        assert container_backend.recover_a_runtime_that_lost_its_namespace("docker") is False
        assert container_backend.recover_a_runtime_that_lost_its_namespace("") is False

    # THESE FOUR MOVED ONE LEVEL DOWN, and the reason is CU1 of the eg12-repair profile rather than
    # anything about a reboot. `serve` no longer calls the recovery directly: it calls
    # `reconcile_then_rebuild`, which rebuilds ONLY after a reconciliation that came back clean. The
    # properties these tests hold are unchanged -- tried only on the "never started" fault, guarded,
    # once, and a failure still refuses -- and they are asserted against the call that now carries
    # them. The gate's own behaviour is tested in test_cleanup_is_the_products_own.py, which drives it
    # rather than reading it.

    def test_serve_tries_it_only_when_nothing_could_be_started(self):
        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        assert 'never started' in source
        assert "reconcile_then_rebuild" in source

    def test_and_a_ceiling_that_failed_to_bind_is_not_recovered_from(self):
        """The opposite fault. Recovering from it would paper over the one thing the proof
        exists to catch, so it must still refuse."""
        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        guard = source[source.index("reconcile_then_rebuild(the_worker)") - 1200:
                       source.index("reconcile_then_rebuild(the_worker)")]
        assert '"never started" in str(proof.reason' in guard

    def test_a_failed_recovery_still_refuses(self):
        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        after = source[source.index("reconcile_then_rebuild(the_worker)"):]
        # Either refusal is a refusal: a rebuild that could not happen because something of ours is
        # still there raises LeftoversRemain, and a ceiling that still does not bind afterwards
        # raises CannotHoldItsLimits. Neither path serves.
        assert "raise LeftoversRemain" in after
        assert "raise CannotHoldItsLimits" in after

    def test_and_it_is_tried_once_rather_than_in_a_loop(self):
        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        assert source.count("reconcile_then_rebuild") <= 2

    def test_the_rebuild_is_not_reachable_from_serve_without_the_gate(self):
        """CU1: the one thing this arc's repair must not allow is a migration beside the gate."""
        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        assert "recover_a_runtime_that_lost_its_namespace" not in source, (
            "serve calls the rebuild directly again; it has to go through reconcile_then_rebuild, "
            "which rebuilds only on an inventory that is empty and could be read")


class TestTheNamespaceUnitStartsAContainer:
    """Migrating is not enough, and that is the whole finding.

    Measured on the pair: `podman system migrate` repaired the stored state and the worker
    still could not start a container. What fixes it is actually STARTING one from outside
    the worker's unit, because that leaves podman's pause process alive. The worker's own
    containers then JOIN that namespace rather than creating one, and joining works from
    inside the masked mount namespace where creating does not.

    It is also why it worked before the first reboot and not after: the installer had started
    a container from outside, and the reboot took that process with it.
    """

    UNIT = (DEPLOY / "agentnode-worker-runtime.service").read_text(encoding="utf-8")

    def test_it_migrates(self):
        assert "system migrate" in self.UNIT

    def test_and_then_actually_starts_a_container(self):
        assert "podman run" in self.UNIT

    def test_in_that_order(self):
        assert self.UNIT.index("system migrate") < self.UNIT.index("podman run")

    def test_and_it_reconciles_before_it_migrates(self):
        """CU1 and CU2 in the unit. A migration replaces the user namespace a live container's init
        belongs to, so anything of ours still present has to go FIRST -- under the mapping that is
        still valid -- or it can never be removed by the account that owns it."""
        # THE COMMANDS, NOT THE PROSE. The first version of this compared `.index()` over the whole
        # file and failed: this unit's header explains `podman system migrate` at length, so the
        # comparison was a comment against a command. A unit's order is the order of its ExecStart
        # lines.
        steps = [line for line in self.UNIT.splitlines() if line.startswith("ExecStart=")]
        assert any("worker reconcile --before-migrate" in s for s in steps), steps
        first = next(i for i, s in enumerate(steps) if "worker reconcile" in s)
        migrates = next(i for i, s in enumerate(steps) if "system migrate" in s)
        assert first < migrates, steps

    def test_and_the_gate_has_no_or_true_on_it(self):
        """`|| true` on the migration is deliberate; on the gate it would make it not a gate."""
        for line in self.UNIT.splitlines():
            if line.startswith("ExecStart=") and "worker reconcile" in line:
                assert "|| true" not in line, line
                break
        else:
            raise AssertionError("the unit no longer reconciles before it migrates")

    def test_with_the_pinned_image_rather_than_a_second_copy_of_it(self):
        assert "_BASE_IMAGE" in self.UNIT

    def test_it_runs_before_the_worker(self):
        assert "Before=agentnode-worker.service" in self.UNIT

    def test_and_the_worker_waits_for_it(self):
        worker = (DEPLOY / "worker-host.service").read_text(encoding="utf-8")
        assert "After=agentnode-worker-runtime.service" in worker
        assert "Wants=agentnode-worker-runtime.service" in worker

    def test_it_carries_none_of_the_masks_that_are_the_problem(self):
        for directive in ("ProtectProc=", "ProtectKernelTunables=", "ProtectSystem="):
            assert directive not in self.UNIT, (
                "this unit exists to create a namespace, which is exactly what those stop")

    def test_but_it_is_still_the_unprivileged_account(self):
        assert "User=agentnode-worker" in self.UNIT

    def test_and_it_cannot_stop_the_worker_starting(self):
        """If the runtime is genuinely unusable the ceiling proof refuses and says why. That
        is the right place for it; a failure here would only hide it."""
        assert self.UNIT.count("|| true") >= 2

    def test_what_it_runs_is_bounded_and_says_so(self):
        """A new trust boundary has to be described accurately. An earlier version of this
        unit's comment said it 'starts no container', which was simply untrue -- starting one
        is the entire point. What is bounded is WHAT it starts."""
        body = self.UNIT
        assert "--network none" in body, "the helper's container must have no network"
        assert "--user 1000:1000" in body, "and must not run as root inside"
        assert "--rm" in body, "and must leave nothing behind but the namespace"
        assert "/bin/true" in body, "and must not run anything of anybody's"
        assert "_BASE_IMAGE" in body, "and must use the image this build pins, not any other"

    def test_and_the_comment_does_not_claim_otherwise(self):
        assert "starts no container and opens no port" not in self.UNIT, (
            "the unit used to claim it starts no container while its ExecStart runs one")


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
