"""An upgrade that could not be followed by a start, and a rollback that proved nothing.

Both scripts shipped inside both artefacts and neither had ever been run across two machines.
Reading them afterwards shows why that mattered.

`upgrade-one-host.sh` reinstalled the wheel with pip. `AGENTNODE_ARTEFACT` lives INSIDE the
dist-info pip replaces, so it went; `runtime-pin.json` was left naming the wheel that is no
longer installed. The pin check then refuses at the next start -- the pin names one artefact
and the installation records none -- so the upgrade left a host that will not come up.

And it proved "this is the new code" by comparing the service's start time with the mtime of
the installed package. That distinguishes two orders of events, not two builds.

`rollback-one-host.sh` restored the code but not the pin, so the same disagreement appeared
in the other direction; and its only check printed the running version string beside the kept
one without comparing them. A version string is explicitly not an identity here -- a
development wheel keeps its number while its contents change, which is exactly why the
upgrade force-reinstalls.

The product already prints what it is: `Running as managed-<commit>+<artefact>`, derived from
the pin and the installed distribution. That is what both scripts compare now.

THESE ARE FILE-LEVEL GUARDS. Whether an upgrade and a rollback actually work is measured on
the two machines; this is what stops the mechanism regressing.
"""
from __future__ import annotations

from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "separate-worker-host"
UPGRADE = (DEPLOY / "upgrade-one-host.sh").read_text(encoding="utf-8")
ROLLBACK = (DEPLOY / "rollback-one-host.sh").read_text(encoding="utf-8")


def code_of(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if out and out[-1].endswith("\\"):
            out[-1] = out[-1][:-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
    return "\n".join(out)


UP = code_of(UPGRADE)
BACK = code_of(ROLLBACK)


class TestAnUpgradeLeavesAHostThatCanStart:

    def test_it_records_the_new_artefact_digest(self):
        assert "AGENTNODE_ARTEFACT" in UP
        assert "sha256sum \"$WHEEL\"" in UP

    def test_into_the_dist_info_pip_just_wrote(self):
        """The old one is gone with the directory it lived in, so it must be re-read."""
        assert 'NEW_DIST="$(cd "$SITE" && ls -d agentnode_sdk-*.dist-info' in UP

    def test_and_rewrites_the_pin(self):
        assert "runtime_pin.write_pin" in UPGRADE
        assert "/etc/agentnode/runtime-pin.json" in UP

    def test_the_commit_comes_from_the_artefact_rather_than_being_invented(self):
        assert "BUILD.json" in UP

    def test_a_wheel_with_no_artefact_beside_it_is_refused(self):
        assert "did not come out of an artefact" in UPGRADE

    def test_and_a_build_json_describing_a_different_wheel_is_refused(self):
        assert "describes a different wheel" in UPGRADE

    def test_all_of_that_happens_before_the_restart(self):
        assert UP.index("write_pin") < UP.index("systemctl restart")


class TestAnUpgradeProvesWhichCodeCameUp:

    def test_it_no_longer_compares_a_timestamp(self):
        assert "stat -c %Y" not in UP
        assert "ActiveEnterTimestamp" not in UP

    def test_it_reads_what_the_process_said_it_is(self):
        assert "managed-" in UP
        assert "journalctl" in UP

    def test_from_the_restart_and_not_from_an_earlier_one(self):
        """A build id printed by the previous start would answer about the wrong process."""
        assert "RESTARTED_AT" in UP
        assert UP.index("RESTARTED_AT=") < UP.index("systemctl restart")

    def test_and_refuses_when_nothing_said_anything(self):
        assert "never said which build it is" in UPGRADE

    def test_it_fails_when_the_build_is_not_the_one_installed(self):
        assert '"$SAID_IT_IS" = "$WILL_BE_BUILD_ID"' in UP

    def test_and_when_nothing_actually_changed_it_says_so_and_does_not_fail(self):
        """CORRECTED, and the correction cost an upgrade rather than being an opinion.

        This used to assert the line `"$SAID_IT_IS" != "$WAS_BUILD_ID"`, which made the upgrade fail
        when the running build equalled the one that was running before. Measured on the two machines:
        the first upgrade of the control plane wrote the new wheel and the new pin and then stopped at
        the measurement gate, because its worker was parked and unreachable -- the product being right.
        The second attempt ran on a host that therefore already had the new code, found before and
        after equal, and refused an upgrade that had in fact succeeded.

        The property worth protecting is that the restart brought up the build THIS RUN INSTALLED, and
        the two assertions above are exactly that. "It is the same as before" adds nothing to them and
        is true of every resumed upgrade and of installing one build twice. So the comparison stays and
        is REPORTED; what is gone is treating it as a failure.
        """
        assert '"$SAID_IT_IS" = "$WAS_BUILD_ID"' in UP, (
            "the upgrade no longer compares the build with what was running before, so it cannot say "
            "whether anything moved")
        after = UP.split('"$SAID_IT_IS" = "$WAS_BUILD_ID"', 1)[1][:800]
        assert "the build did not change" in after, after[:200]
        assert "That is not a failure" in after, after[:200]
        assert "died" not in after.split("fi", 1)[0], (
            "the equal-build branch still ends the run, which is what made a resumed upgrade "
            "impossible")


class TestTheGatewayGateIsPassable:
    """A gate that can never pass is not a gate.

    A gateway's measurement is bound to the build that took it -- artefact digest, commit and
    build id are all in it -- so after any code change the stored one describes something
    else and the PLAIN doctor refuses. Correctly, and every time. `install.sh --verify`
    learned this in the previous repair; this script had not, and no upgrade had ever been
    run across two machines to find out. Measured on the pair: the plain doctor reported
    "the stored measurement describes something else" and the upgrade stopped dead.
    """

    def test_the_gateway_gate_re_measures(self):
        gate = UP[UP.index("would it still start?"):UP.index("5. restart")]
        assert "gateway doctor --measure" in gate

    def test_and_the_worker_gate_is_still_its_own_preflight(self):
        """Different question, different check: the worker's preflight opens nothing."""
        gate = UP[UP.index("would it still start?"):UP.index("5. restart")]
        assert "worker preflight" in gate


class TestAFailedUpgradeSaysWhatItAlreadyChanged:
    """It used to say "Nothing was restarted", which is true and incomplete: by the time the
    gate runs, the code on disk and the runtime pin are already the new ones. A host left in
    that state comes up on the new build at the next restart for any other reason."""

    def test_the_gateway_failure_says_the_pin_is_already_new(self):
        assert "the runtime pin are ALREADY the new ones" in ROLLBACK or \
               "runtime pin are ALREADY the new" in UPGRADE

    def test_both_failure_paths_name_the_rollback(self):
        after_install = UPGRADE[UPGRADE.index("3. the new code"):]
        for chunk in after_install.split("died ")[1:]:
            if "NOT restarted" in chunk or "not restarted" in chunk:
                assert "rollback-one-host.sh" in chunk


class TestTheProofIsWaitedForNotSleptThrough:
    """Measured on the pair: 71 seconds between systemd starting the gateway and the gateway
    printing which build it is, because it settles its state and reaches its worker first. A
    fixed three-second sleep read the journal before the line existed and reported "it never
    said which build it is" about a service that was perfectly fine -- a false failure on the
    one check that is supposed to BE the proof."""

    def test_the_upgrade_polls_for_it(self):
        assert "wait_for_the_build_id" in UP
        assert "BUILD_ID_PATIENCE" in UP

    def test_and_so_does_the_rollback(self):
        assert "wait_for_the_build_id" in BACK
        assert "BUILD_ID_PATIENCE" in BACK

    def test_the_patience_is_well_past_what_was_measured(self):
        for text in (UPGRADE, ROLLBACK):
            line = [ln for ln in text.splitlines() if ln.startswith("BUILD_ID_PATIENCE=")][0]
            assert int(line.split("=")[1]) >= 120

    def test_neither_confirms_on_a_bare_sleep_any_more(self):
        for text in (UP, BACK):
            after = text[text.index("wait_for_the_build_id"):]
            assert "sleep 3\n" not in after.split("SAID_IT_IS")[-1]

    def test_and_giving_up_is_still_a_failure_rather_than_a_pass(self):
        assert "never said which build it is" in UPGRADE
        assert "never said which build it is" in ROLLBACK


class TestARollbackToABuildThatCannotSpeak:
    """"Could not be established" is a different answer from "established false".

    A build from before the identity line was flushed cannot announce itself while it runs --
    under systemd stdout is a pipe and the line reaches the journal only when the process
    exits. Measured on the pair: every `Running as ...` arrived in the same second as the
    following "Stopped". A rollback to such a build is therefore not confirmable by anybody,
    and calling that a failed rollback would be wrong: the code went back; the proof is what
    is missing, and it is missing because of the build that was restored.
    """

    def test_it_distinguishes_the_two_by_looking_at_the_restored_code(self):
        assert "flush=True" in BACK
        assert "runtime_pin.py" in BACK

    def test_a_build_that_cannot_announce_itself_is_not_called_a_failure(self):
        assert "ROLLED BACK, NOT CONFIRMED" in ROLLBACK

    def test_but_it_is_not_called_a_success_either(self):
        """A distinct exit status, so a script driving this cannot read it as done."""
        assert "exit 3" in BACK

    def test_and_it_says_how_to_see_the_id_anyway(self):
        assert "systemctl stop" in ROLLBACK and "journalctl -u" in ROLLBACK

    def test_a_build_that_can_announce_itself_and_did_not_is_still_a_failure(self):
        assert "this is a real failure and not a build" in ROLLBACK


class TestTheKeepHasWhatARollbackNeeds:

    def test_the_pin_is_kept(self):
        assert 'cp /etc/agentnode/runtime-pin.json "$KEEP/"' in UP

    def test_and_the_build_id_it_names(self):
        assert 'build-id.txt' in UP

    def test_a_host_with_no_pin_is_refused_rather_than_upgraded(self):
        assert "there is no /etc/agentnode/runtime-pin.json to keep" in UPGRADE


class TestARollbackPutsTheCodeBackIntoService:

    def test_it_restores_the_pin_with_the_code(self):
        assert 'install -m 0644 "$KEEP/runtime-pin.json" /etc/agentnode/runtime-pin.json' in BACK

    def test_an_old_keep_without_one_is_refused(self):
        assert "holds no runtime-pin.json" in ROLLBACK

    def test_it_compares_rather_than_printing_two_strings(self):
        assert '"$SAID_IT_IS" = "$WAS_BUILD_ID"' in BACK

    def test_and_no_longer_just_prints_them(self):
        assert "the keep says it was" not in BACK

    def test_it_still_stops_rather_than_restarting_into_the_swap(self):
        """Replacing code under a running process leaves it serving what it loaded."""
        assert 'systemctl stop "$UNIT".service' in BACK
        assert BACK.index('systemctl stop') < BACK.index('tar -C "$SITE" -xf')

    def test_and_says_so_when_the_process_is_not_what_was_kept(self):
        assert "put files back and the running process is not them" in ROLLBACK


class TestTheOneRollbackThatMustBeRefused:
    """A hazard this repair itself created, and the reason the guard is not optional.

    The lease counter moved out of the journal directory. A build from before that move looks
    for it inside the journal, does not find it, reads a missing file as zero, and hands out
    epoch 1 again -- so every instruction a retired control plane still holds becomes valid.
    Putting the file back is not a way out: the old journal enumerates that directory and the
    crash-loop returns. The two layouts are incompatible in both directions.
    """

    def test_the_rollback_checks_the_kept_build_for_the_new_layout(self):
        assert "legacy=legacy" in BACK
        assert "tar -xOf" in BACK

    def test_before_anything_is_stopped_or_unpacked(self):
        """A refusal after the service is down has already broken the thing it protects."""
        assert BACK.index("legacy=legacy") < BACK.index('systemctl stop')
        assert BACK.index("legacy=legacy") < BACK.index('tar -C "$SITE" -xf')

    def test_only_when_there_is_actually_a_number_to_lose(self):
        """A worker that never issued a lease has nothing at stake and is let through."""
        assert '[ -f /var/lib/agentnode-worker/lease-epoch.json ]' in BACK

    def test_and_only_on_the_worker(self):
        """The gateway keeps no epoch counter, so the hazard cannot arise there."""
        guard = BACK[BACK.index("legacy=legacy") - 400:BACK.index("legacy=legacy")]
        assert '"$UNIT" = "agentnode-worker"' in guard

    def test_the_refusal_says_what_would_go_wrong(self):
        assert "epoch" in ROLLBACK and "retired control plane" in ROLLBACK

    def test_and_names_a_way_forward_rather_than_only_refusing(self):
        assert "pki revoke" in ROLLBACK
        assert "a NEW instance" in ROLLBACK


class TestTheOtherRollbackThatMustBeRefused:
    """Found by doing it, not by reading the code.

    Six refusal codes were missing from ERRORS, so `refusal()` rewrote them to `internal`,
    `NO_LEASE` among them. A worker from before that fix cannot say "no lease" on the wire.
    The re-acquisition this repair added keys on the CAUSE of the refusal, so against such a
    worker the gateway never re-takes the lease and the pair stops recovering. Measured on the
    pair: four jobs, spaced, nothing restarted, every one refused, until the worker was rolled
    forward again. Raw: crosshost-repair-2/raw/C-13a.

    The other direction was measured too and is NOT refused: a gateway rolled back across the
    same change ran three jobs out of three, because the rewriting happens on the worker's
    side and an old gateway still receives correctly named codes.
    """

    def test_the_rollback_reads_the_kept_builds_ERRORS_TUPLE(self):
        """NOT the file. `NO_LEASE = "no-lease"` has been defined in every build ever shipped;
        the defect was that it was missing from ERRORS, which is what refusal() consults.
        Grepping the file for the string matches everything and refuses nothing -- the first
        version of this guard did that, and its keep inventory called a pre-fix build fine."""
        assert "agentnode_sdk/worker/protocol.py" in BACK
        assert "^ERRORS" in BACK and "NO_LEASE" in BACK
        assert '"no-lease"' not in BACK, "reading the constant's value instead of the tuple"

    def test_before_anything_is_stopped_or_unpacked(self):
        """A refusal after the service is down has already broken the thing it protects."""
        assert BACK.index("KEPT_ERRORS") < BACK.index("systemctl stop")
        assert BACK.index("KEPT_ERRORS") < BACK.index('tar -C "$SITE" -xf')

    def test_and_only_on_the_worker(self):
        """A gateway rolled back across the same change was measured and it works."""
        guard = BACK[BACK.index("KEPT_ERRORS") - 600 : BACK.index("KEPT_ERRORS")]
        assert '"$UNIT" = "agentnode-worker"' in guard

    def test_the_refusal_says_what_would_go_wrong(self):
        assert "arrives at the control plane as" in ROLLBACK
        assert "stops recovering" in ROLLBACK

    def test_and_names_a_way_forward_rather_than_only_refusing(self):
        assert "Roll forward" in ROLLBACK
        assert "CONTROL" in ROLLBACK

    def test_and_says_the_other_direction_is_allowed(self):
        """Refusing both directions would be the easy answer and a false one."""
        assert "is safe and is not refused" in ROLLBACK


class TestTheGuardDoesNotForbidItsOwnAdvice:
    """A fault in the first version of the guard above, found by trying to follow it.

    Its refusal says: roll the control plane back to the same generation, then roll this host
    back. But the check keyed only on the keep, so it refused the worker half of that remedy
    too -- the advice could not be taken. A guard that makes its own recommendation impossible
    is one that will be worked around instead of followed.

    The waiver is an ASSERTION by the operator, not a verification: this script cannot see the
    other machine. So it is named for what it asserts, it is recorded in the output, and it
    says plainly what happens if the assertion is false.
    """

    def test_the_flag_exists_and_is_named_for_what_it_asserts(self):
        assert "--control-plane-already-rolled-back" in BACK

    def test_the_refusal_tells_you_about_it(self):
        assert "--control-plane-already-rolled-back" in ROLLBACK
        assert "how you say you have done it" in ROLLBACK

    def test_it_admits_it_cannot_check(self):
        assert "cannot see the other" in ROLLBACK

    def test_it_waives_the_refusal_code_check_and_only_that_one(self):
        assert '[ -z "$PAIRED" ]' in BACK
        assert BACK.index("lease-epoch.json") < BACK.index("KEPT_ERRORS"), \
            "the counter check must run first"
        # The waiver must not appear on the COUNTER guard's own condition line. That check is
        # unsafe in both directions whatever the other host runs, so nothing may excuse it.
        conditions = [ln for ln in BACK.splitlines()
                      if "-f /var/lib/agentnode-worker/lease-epoch.json" in ln]
        assert conditions, "the lease-counter guard's condition was not found"
        for line in conditions:
            assert "PAIRED" not in line, "the lease-counter check must not be waivable"

    def test_and_says_so_when_it_is_used(self):
        assert "was waived by" in ROLLBACK
        assert "is NOT waived" in ROLLBACK

    def test_the_header_does_not_contradict_the_remedy(self):
        """Review round 6, F-ROUND6-002. The header prescribes worker-first, and the waiver
        can only be used truthfully gateway-first. Both are right for their own case; a
        header that states only one of them misleads whoever follows it."""
        assert "REVERSES STEPS 2 AND 3" in ROLLBACK
        assert "roll the CONTROL PLANE back first" in ROLLBACK

    def test_and_warns_about_the_re_measurement_window(self):
        """F-ROUND6-003. Five minutes of `Not protecting` is operationally material and must
        not be normalised as an instantaneous rollback."""
        assert "Not protecting" in ROLLBACK
        assert "300 seconds" in ROLLBACK


class TestNeitherScriptTouchesState:
    """An upgrade that rewrote the journal, the floor, the counter or the certificates would
    be a migration. Both say they do not; this is the assertion."""

    def test_the_upgrade_leaves_the_durable_things_alone(self):
        for path in ("/var/lib/agentnode-worker/journal", "/var/lib/agentnode-floor",
                     "pair-keys.json", "/var/lib/agentnode-worker/tls"):
            assert ("rm " + path) not in UP
            assert ("rm -rf " + path) not in UP

    def test_and_so_does_the_rollback(self):
        for path in ("/var/lib/agentnode-worker/journal", "/var/lib/agentnode-floor",
                     "pair-keys.json", "/var/lib/agentnode-worker/tls"):
            assert ("rm " + path) not in BACK
            assert ("rm -rf " + path) not in BACK

    def test_the_rollback_removes_only_the_package(self):
        removals = [ln for ln in BACK.splitlines() if ln.strip().startswith("rm -rf")]
        assert removals == ['rm -rf "$SITE/agentnode_sdk" "$SITE"/agentnode_sdk-*.dist-info']
