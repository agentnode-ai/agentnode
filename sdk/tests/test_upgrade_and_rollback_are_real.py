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

    def test_and_when_nothing_actually_changed(self):
        assert '"$SAID_IT_IS" != "$WAS_BUILD_ID"' in UP


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
