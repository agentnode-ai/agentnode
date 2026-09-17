"""A restore is checked against WHAT WAS TAKEN, not against whether the copy looks plausible.

The drill used to ask five questions of the restored state: does the metering chain verify, does
it know its own identity, are there some devices, is the policy key here, are the permissions
700. Every one of those passes on a restore that came back with half the accounts, no sessions,
an empty audit and no tombstones -- because a copy on its own has nothing to disagree with.

So a backup writes down what it contained, class by class, and a restore recomputes it and
compares. What is asserted here is not that the comparison is implemented: it is that REMOVING
each class in turn is NOTICED and NAMED. A drill that reports "does not match" for everything
sends somebody through a tarball; a drill that reports it for nothing is worse.

The list of classes is not written here either. It comes from `retention.CLASSES` plus
`backup.BESIDES`, which between them are every file a gateway keeps -- so the day somebody adds
a store, this file fails until the drill knows about it.
"""
from __future__ import annotations

import json
import shutil

import pytest

from agentnode_sdk.gateway import backup
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_retention_every_class import a_gateway_that_has_done_everything


@pytest.fixture()
def a_busy_gateway(tmp_path):
    """One where EVERY file in the table exists, so every per-class check has something to lose.

    A drill run against a gateway that has only ever paired one device would skip most of
    itself, and a suite that skips most of itself while reporting green is the thing this whole
    file exists to stop. `test_the_fixture_wrote_every_one_of_them` turns a class this does not
    produce into a failure rather than a skip.
    """
    from agentnode_sdk.access import dispatch
    from agentnode_sdk.gateway import certificate, retention

    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    who = a_gateway_that_has_done_everything(service)

    # The rest of the table, produced the way the product produces it rather than written by
    # hand: a file this test planted is a file whose real shape nothing here has seen.
    state.start_pairing()                                  # pairing.json
    retention.write_retention(state.root, retention.Retention())          # retention.json
    retention.sweep_if_due(state.root)                     # retention-last-swept.json
    service.stopping.ask(next(iter(service.runs), "a-run"), by=who.device_id)  # stopping.json
    certificate.make(state.root, "127.0.0.1")              # tls-cert.pem and tls-key.pem
    (state.root / "config.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 8099}), encoding="utf-8")

    # The key directory beside it, which a backup of the state alone leaves behind.
    secret = tmp_path / ("state" + backup.SECRET_SUFFIX)
    secret.mkdir(exist_ok=True)
    (secret / "policy.key").write_bytes(b"not a real key, but a real file\n")
    service.whoever = who
    try:
        yield service
    finally:
        state.close()


def _taken(service):
    return backup.what_is_in_it(service.state.root)


def _restored_into(service, where):
    """A copy, the way a tar-and-extract produces one."""
    shutil.copytree(str(service.state.root), str(where / "state"))
    shutil.copytree(str(service.state.root) + backup.SECRET_SUFFIX,
                    str(where / ("state" + backup.SECRET_SUFFIX)))
    return where / "state"


def _present(manifest) -> list:
    return sorted(name for name, what in manifest["kept"].items() if what.get("present"))


# ------------------------------------------------------------------ the table


class TestTheDrillKnowsEveryStore:

    def test_it_covers_the_aged_classes_and_the_ageless_ones(self):
        from agentnode_sdk.gateway import retention

        known = backup.everything_a_gateway_keeps()
        for name, what in retention.CLASSES.items():
            if name in backup.NOT_IN_A_BACKUP:
                continue
            assert what["file"] in known, (
                "%s is swept and would not be missed by a restore" % name)
        assert set(backup.BESIDES) <= set(known)
        assert set(backup.HOW_THE_AGED_ONES_LOOK) == set(retention.CLASSES), (
            "a retention class with no shape here is one the manifest cannot summarise: %s"
            % (set(backup.HOW_THE_AGED_ONES_LOOK) ^ set(retention.CLASSES)))

    def test_and_the_one_class_that_is_not_in_a_backup_is_named_rather_than_missing(self):
        """The exclusion is a list, not a silence.

        `backups` is a period over the sealed archives themselves, so putting it in the manifest
        would ask a backup to contain the backups. That is the ONLY reason a class may be absent
        from the manifest, and the reason has to be written down: a skip nobody declared is
        indistinguishable from a store somebody forgot, and a restore that quietly omits a store
        is the failure this whole class exists to catch.
        """
        from agentnode_sdk.gateway import retention

        assert backup.NOT_IN_A_BACKUP == {"backups"}
        assert backup.NOT_IN_A_BACKUP <= set(retention.CLASSES), (
            "something is excluded from the manifest that is not even a retention class")
        known = backup.everything_a_gateway_keeps()
        assert retention.CLASSES["backups"]["file"] not in known

    def test_and_every_entry_says_what_it_is(self):
        for name, (how, why) in sorted(backup.everything_a_gateway_keeps().items()):
            assert how in (backup.LINES, backup.KEYS, backup.NESTED, backup.PRESENT,
                           backup.WHOLE), (name, how)
            assert why and len(why) > 10, (
                "%s is in the table without saying what it is, which is how a table absorbs a "
                "file somebody added to stop a test failing" % name)

    def test_and_a_real_gateway_writes_nothing_it_does_not_know_about(self, a_busy_gateway):
        strangers = backup.unaccounted_files(a_busy_gateway.state.root)
        assert not strangers, (
            "this gateway stores %s, and losing it in a restore would be reported by nothing. "
            "Add it to retention.CLASSES with a period, or to backup.BESIDES with why it has "
            "no age." % strangers)

    def test_and_a_file_nobody_declared_is_reported(self, a_busy_gateway):
        """The counter-check for the check above: it has to be able to say no."""
        (a_busy_gateway.state.root / "somebody-added-this.json").write_text("{}",
                                                                           encoding="utf-8")
        assert backup.unaccounted_files(a_busy_gateway.state.root) == [
            "somebody-added-this.json"]


# ------------------------------------------------------------------ the round trip


class TestARestoreThatIsWholeIsAccepted:

    def test_a_copy_of_everything_matches(self, a_busy_gateway, tmp_path):
        taken = _taken(a_busy_gateway)
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        assert backup.differences(taken, backup.what_is_in_it(back)) == []

    def test_and_the_manifest_is_not_empty_of_the_things_it_claims_to_check(self,
                                                                           a_busy_gateway):
        """A manifest of nothing would match a restore of nothing."""
        taken = _taken(a_busy_gateway)
        for must_be_there in ("identity.json", "tokens.json", "accounts.json", "audit.jsonl",
                              "use-log.jsonl", "sessions.json", "joining.json", "ledger.json",
                              "exports.jsonl", "events.jsonl", ".secret"):
            assert taken["kept"][must_be_there]["present"], (
                "%s was not written by a gateway that did everything, so the per-class checks "
                "below would be testing an absent file" % must_be_there)


# ------------------------------------------------------------------ losing one class


class TestLosingAnyOneStoreIsNoticedAndNamed:
    """Parametrised over the table, so a class added later is drilled the day it is added."""

    def _restored_without(self, service, where, name):
        back = _restored_into(service, where)
        (back / name).unlink()
        return back

    def test_the_fixture_wrote_every_one_of_them(self, a_busy_gateway):
        """So a class added to the table that nothing produces FAILS rather than skipping.

        A skip reads as a pass in every summary anybody looks at. If this is red, the fixture
        above needs to make the named file the way the product makes it -- not the parametrised
        check below relaxed.
        """
        taken = _taken(a_busy_gateway)
        absent = sorted(name for name in backup.everything_a_gateway_keeps()
                        if not taken["kept"][name]["present"])
        assert not absent, (
            "nothing in this fixture produces %s, so losing it in a restore is checked by "
            "nothing" % absent)

    # EVERY store the manifest knows, EXCEPT the ones that deliberately do not come back.
    # Derived from `backup.NOT_RESTORED` rather than written as a list here: a name written in
    # two places drifts, and this is the place where the drift would read as "the restore is
    # fine" rather than as an error.
    @pytest.mark.parametrize("name", sorted(set(backup.everything_a_gateway_keeps())
                                            - backup.NOT_RESTORED))
    def test_a_class_that_did_not_come_back_is_reported_by_name(self, a_busy_gateway, tmp_path,
                                                                name):
        taken = _taken(a_busy_gateway)
        assert taken["kept"][name]["present"], (
            "%s was not written; see test_the_fixture_wrote_every_one_of_them" % name)

        back = self._restored_without(a_busy_gateway, tmp_path / "restored", name)
        said = backup.differences(taken, backup.what_is_in_it(back))
        assert said, "%s vanished in the restore and nothing said so" % name
        assert any(line.startswith(name + ":") for line in said), (
            "%s vanished and the drill reported %s instead" % (name, said))

    def test_and_the_key_directory_beside_it_counts_as_one(self, a_busy_gateway, tmp_path):
        """The one a backup of the state alone leaves behind, which a drill once missed."""
        taken = _taken(a_busy_gateway)
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        shutil.rmtree(str(back) + backup.SECRET_SUFFIX)
        said = backup.differences(taken, backup.what_is_in_it(back))
        assert any(line.startswith(".secret:") for line in said), said

    def test_a_class_that_came_back_SHORTER_is_reported_with_both_figures(self, a_busy_gateway,
                                                                         tmp_path):
        """Losing every record is loud. Losing some of them is the one worth catching."""
        taken = _taken(a_busy_gateway)
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        lines = (back / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) > 2, "this gateway did too little for a partial loss to be partial"
        (back / "audit.jsonl").write_text("\n".join(lines[:1]) + "\n", encoding="utf-8")

        said = backup.differences(taken, backup.what_is_in_it(back))
        named = [line for line in said if line.startswith("audit.jsonl:")]
        assert named, said
        assert "1 after" in named[0], named

    def test_a_customer_that_came_back_missing_is_reported(self, a_busy_gateway, tmp_path):
        taken = _taken(a_busy_gateway)
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        (back / "accounts.json").write_text("{}", encoding="utf-8")

        said = backup.differences(taken, backup.what_is_in_it(back))
        assert any(line.startswith("accounts.json:") for line in said), said

    def test_a_record_REPLACED_by_another_is_reported_even_at_the_same_count(self,
                                                                            a_busy_gateway,
                                                                            tmp_path):
        """A count alone would match. What identifies the records has to be in the manifest."""
        taken = _taken(a_busy_gateway)
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        held = json.loads((back / "sessions.json").read_text(encoding="utf-8"))
        assert held, "no sessions to swap"
        was = sorted(held)[0]
        held["a-session-that-was-never-opened"] = held.pop(was)
        (back / "sessions.json").write_text(json.dumps(held), encoding="utf-8")

        said = backup.differences(taken, backup.what_is_in_it(back))
        assert any(line.startswith("sessions.json:") for line in said), said

    def test_and_a_file_that_came_back_UNREADABLE_is_not_mistaken_for_an_empty_one(
            self, a_busy_gateway, tmp_path):
        taken = _taken(a_busy_gateway)
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        (back / "sessions.json").write_text("{ this is not json", encoding="utf-8")

        said = backup.differences(taken, backup.what_is_in_it(back))
        assert any("unreadable" in line for line in said), said


# ------------------------------------------------------------------ what it must not carry


class TestTheManifestTravelsWithTheArchiveAndIsNotAWayIn:

    def test_it_carries_no_key_material_and_no_digest_of_any(self, a_busy_gateway, tmp_path):
        """A manifest ends up in places an archive does not. It says a key is THERE and how long
        it is, never anything derived from its content: 'we only stored a hash of it' is the
        sentence that comes just before that being a problem."""
        secret = (a_busy_gateway.state.root / "meter-key.pem")
        if not secret.is_file():
            pytest.skip("this gateway has no signing key on disk")
        said = json.dumps(_taken(a_busy_gateway))

        raw = secret.read_bytes()
        assert raw.decode("utf-8", "ignore").strip() not in said
        import hashlib

        for made in (hashlib.sha256(raw).hexdigest(), hashlib.sha1(raw).hexdigest(),
                     hashlib.md5(raw).hexdigest()):
            assert made[:16] not in said, (
                "the manifest carries a digest of the signing key, which makes the manifest "
                "worth stealing")

    def test_and_a_missing_key_is_still_reported_as_missing(self, a_busy_gateway, tmp_path):
        """Not carrying its content must not mean not noticing it went."""
        taken = _taken(a_busy_gateway)
        if not taken["kept"]["meter-key.pem"]["present"]:
            pytest.skip("this gateway has no signing key on disk")
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        (back / "meter-key.pem").unlink()

        said = backup.differences(taken, backup.what_is_in_it(back))
        assert any(line.startswith("meter-key.pem:") for line in said), said

    def test_and_a_key_that_came_back_a_different_length_is_reported(self, a_busy_gateway,
                                                                     tmp_path):
        taken = _taken(a_busy_gateway)
        if not taken["kept"]["meter-key.pem"]["present"]:
            pytest.skip("this gateway has no signing key on disk")
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        (back / "meter-key.pem").write_bytes(b"truncated")

        said = backup.differences(taken, backup.what_is_in_it(back))
        assert any(line.startswith("meter-key.pem:") for line in said), said


# ------------------------------------------------------------------ the tombstones


class TestErasureSurvivesTheRoundTrip:
    """A deleted customer must not come back, and the proof of the deletion must not go.

    `meter.erase` replaces a customer's metered lines with SIGNED TOMBSTONES rather than removing
    them, because a chain with a hole in it verifies as tampered. A restore that dropped the
    tombstones would leave a chain that does not verify; one that brought back the original lines
    would un-delete a customer who asked to be deleted. Both are checked.
    """

    def test_a_restore_brings_back_the_tombstones_and_the_chain_still_verifies(
            self, a_busy_gateway, tmp_path):
        from agentnode_sdk.gateway import meter

        lines = (a_busy_gateway.state.root / "use-log.jsonl").read_text(
            encoding="utf-8").splitlines()
        assert lines, "nothing was metered, so there is nothing to erase"
        whose = json.loads(lines[0]).get("account_id") or ""
        assert whose, lines[0]

        gone = meter.erase(a_busy_gateway.state.root, "the customer asked to be deleted",
                           lambda line: line.get("account_id") == whose)
        assert gone, "nothing was erased, so this test proves nothing"
        taken = _taken(a_busy_gateway)

        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        assert backup.differences(taken, backup.what_is_in_it(back)) == []

        held = meter.verify(back)
        assert held.get("ok"), held
        assert held.get("erased"), "the tombstones did not come back"
        assert whose not in (back / "use-log.jsonl").read_text(encoding="utf-8"), (
            "a restore brought back the metered lines of a customer who was erased")

    def test_and_losing_them_is_reported_rather_than_verifying_anyway(self, a_busy_gateway,
                                                                     tmp_path):
        from agentnode_sdk.gateway import meter

        lines = (a_busy_gateway.state.root / "use-log.jsonl").read_text(
            encoding="utf-8").splitlines()
        whose = json.loads(lines[0]).get("account_id") or ""
        meter.erase(a_busy_gateway.state.root, "the customer asked to be deleted",
                    lambda line: line.get("account_id") == whose)
        taken = _taken(a_busy_gateway)

        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        kept = (back / "use-log.jsonl").read_text(encoding="utf-8").splitlines()
        (back / "use-log.jsonl").write_text("\n".join(kept[1:]) + "\n", encoding="utf-8")

        said = backup.differences(taken, backup.what_is_in_it(back))
        assert any(line.startswith("use-log.jsonl:") for line in said), said


# ------------------------------------------------------------------ the operator's own command


class TestTheCommandTheScriptCalls:
    """`python -m agentnode_sdk.gateway.backup ...` -- what `backup-and-restore.sh` runs.

    Written in Python rather than in the shell because counting records in a JSON-lines file with
    `wc` and hoping is how a drill comes to pass on a file that was truncated to exactly the
    right number of bytes.
    """

    def test_write_then_compare_against_an_intact_copy_says_nothing_is_wrong(self,
                                                                            a_busy_gateway,
                                                                            tmp_path, capsys):
        where = str(tmp_path / backup.MANIFEST_NAME)
        assert backup.main(["write", str(a_busy_gateway.state.root), where]) == 0
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        assert backup.main(["compare", str(back), where]) == 0
        assert "came back exactly as they went in" in capsys.readouterr().out

    def test_and_names_the_class_that_is_wrong_with_a_non_zero_exit(self, a_busy_gateway,
                                                                    tmp_path, capsys):
        where = str(tmp_path / backup.MANIFEST_NAME)
        backup.main(["write", str(a_busy_gateway.state.root), where])
        back = _restored_into(a_busy_gateway, tmp_path / "restored")
        (back / "accounts.json").unlink()

        assert backup.main(["compare", str(back), where]) == 1
        said = capsys.readouterr().out
        assert "accounts.json" in said and "PROBLEM" in said, said

    def test_and_no_manifest_at_all_is_a_failure_rather_than_a_pass(self, a_busy_gateway,
                                                                    tmp_path, capsys):
        """A comparison with nothing to compare against is not a comparison."""
        assert backup.main(["compare", str(a_busy_gateway.state.root),
                            str(tmp_path / "never-written.json")]) == 1
        assert "no manifest" in capsys.readouterr().out

    def test_and_an_undeclared_store_is_a_failure(self, a_busy_gateway, capsys):
        (a_busy_gateway.state.root / "something-new.jsonl").write_text("{}\n", encoding="utf-8")
        assert backup.main(["unaccounted", str(a_busy_gateway.state.root)]) == 1
        assert "something-new.jsonl" in capsys.readouterr().out


# ------------------------------------------------------------------ the operator's script


class TestTheScriptActuallyUsesIt:
    """`deploy/backup-and-restore.sh` is the operator path, and the wiring is what rots.

    Everything above tests the drill. None of it would notice the script that runs on the real
    machine quietly not calling it -- which is the state this whole change started from: a
    thorough check that nothing invoked on the thing it was meant to check.
    """

    def _script(self) -> str:
        import pathlib

        import agentnode_sdk

        where = (pathlib.Path(agentnode_sdk.__file__).parent.parent / "deploy"
                 / "backup-and-restore.sh")
        assert where.is_file(), where
        return where.read_text(encoding="utf-8")

    def test_a_backup_writes_the_manifest_and_digests_it_with_the_archive(self):
        said = self._script()
        assert "agentnode_sdk.gateway.backup write" in said, (
            "a backup that records nothing leaves a restore with nothing to be checked against")
        assert backup.MANIFEST_NAME in said
        assert "sha256sum state.tar.sealed secret.tar.sealed %s" % backup.MANIFEST_NAME in said, (
            "the manifest is not digested with the SEALED archive, so one edited afterwards to "
            "match a damaged restore would pass")

    def test_a_check_and_a_restore_both_compare_against_it(self):
        said = self._script()
        assert said.count("agentnode_sdk.gateway.backup compare") >= 1
        assert said.count('check_state "') >= 2, "check and restore must both ask"
        for call in ('check_state "$INNER" "$FROM/%s"' % backup.MANIFEST_NAME,
                     'check_state "$STATE_DIR" "$FROM/%s"' % backup.MANIFEST_NAME):
            assert call in said, (
                "one of the two paths does not pass the manifest, so it checks the old five "
                "questions only: %s" % call)

    def test_and_a_backup_with_no_manifest_is_reported_rather_than_passed_over(self):
        """An archive from before this existed is unverifiable, and that is what it is told."""
        said = self._script()
        assert "nothing to compare the restored state against" in said

    def test_and_an_undeclared_store_stops_the_backup_being_called_checkable(self):
        said = self._script()
        assert "agentnode_sdk.gateway.backup unaccounted" in said
        assert "The archive was written. It is NOT fully checkable" in said, (
            "a store the drill does not know about has to be said out loud; refusing to WRITE "
            "the archive would be the wrong failure, and saying nothing is the other one")


class TestTheScriptSealsWhatItWrites:
    """`P1` failed on an unencrypted archive. The wiring is what makes the module matter."""

    def _script(self) -> str:
        import pathlib

        import agentnode_sdk

        return (pathlib.Path(agentnode_sdk.__file__).parent.parent / "deploy"
                / "backup-and-restore.sh").read_text(encoding="utf-8")

    def test_a_backup_seals_both_tars_and_removes_the_plaintext(self):
        said = self._script()
        assert "agentnode_sdk.gateway.archive seal" in said
        assert "shred -u" in said or "rm -f \"$WHERE/$what.tar\"" in said, (
            "the plaintext tar is left beside the sealed one, which makes sealing decorative")

    def test_and_binds_the_gateway_and_the_manifest_into_the_header(self):
        said = self._script()
        assert "--gateway" in said and "--manifest-sha256" in said, (
            "nothing binds this archive to the gateway it came from or to what it contained, so "
            "a valid archive of something else would restore")

    def test_check_and_restore_both_open_it_before_touching_anything(self):
        said = self._script()
        assert said.count("open_the_archive") >= 4, (
            "one of the two paths still extracts a tar directly")
        assert "Nothing has been restored and the existing state is" in said

    def test_and_the_key_is_refused_if_it_sits_with_the_archive_or_in_the_state(self):
        said = self._script()
        assert "key_is_somewhere_else" in said
        assert "unencrypted archive with extra steps" in said
        assert said.count("key_is_somewhere_else") >= 3, (
            "the check exists and is not called on every path that uses the key")

    def test_and_there_is_a_way_to_make_a_key_that_says_what_losing_it_means(self):
        said = self._script()
        assert "newkey)" in said
        assert "A backup whose key is" in said


class TestTheCategoryForFilesThatMustNotComeBack:
    """`NOT_RESTORED` is EMPTY, and that is the finished state rather than an oversight.

    It was created for one file, the runtime pin, which has since moved out of the state
    directory -- where it should never have been, as the module that writes it had already said.
    Moving it removed the whole class: no exclusion pattern in the restore, no manifest entry, no
    comparison exemption, nothing to drift.

    The machinery stays because it is right, and because the next file that genuinely is "in a
    backup and must not come back" should land here rather than being handled where somebody
    happens to notice it.
    """

    def test_it_is_empty_and_the_comparison_still_honours_it(self):
        assert backup.NOT_RESTORED == set()
        before = {"kept": {"audit.jsonl": {"present": True, "lines": 3}}}
        after = {"kept": {"audit.jsonl": {"present": False}}}
        assert backup.differences(before, after) != [], "a real loss must still be a loss"

    def test_and_the_pin_is_not_in_the_manifest_because_it_is_not_in_the_state(self):
        assert "runtime-pin.json" not in backup.everything_a_gateway_keeps()
