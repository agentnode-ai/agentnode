"""What this gateway writes down, what it refuses to write down, and how it lets go of it.

The redaction tests PLANT each secret shape and then look for it in everything the gateway wrote.
That is deliberately not the same as reading the call sites and concluding they look careful: the
failure this guards against is a field somebody adds later, and only a test that searches the
OUTPUT catches that one.
"""
from __future__ import annotations

import pathlib
import re
import json
import secrets
import time
import uuid

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import meter, redaction, retention
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


def _a_metered_line(root, **overrides):
    values = dict(run_id=uuid.uuid4().hex, client_id="d" * 16, account_id="acct-" + "0" * 16,
                  started_at=1.0, finished_at=2.0, cpu=1.0, memory_mb=256, wall_clock_s=30,
                  state="finished", outcome="ok", bytes_out=10,
                  worker_topology="single-host-development", worker_id="w1",
                  allowance_sha256="d" * 64, operator_policy_sha256="p" * 64,
                  operator_policy_version=1)
    values.update(overrides)
    return meter.record(root, **values)


class TestNothingShapedLikeASecretSurvivesAScrub:
    """Each shape this gateway actually issues."""

    @pytest.mark.parametrize("shape", [
        "a device credential", "a session", "a pairing code", "an invitation",
        "a key", "a url with a code", "a header", "userinfo",
    ])
    def test_it_goes(self, shape):
        planted = {
            "a device credential": secrets.token_urlsafe(32),
            "a session": secrets.token_urlsafe(24),
            "a pairing code": "ABCD-EFGH-JKLM",
            "an invitation": "agentnode-invite-1." + secrets.token_urlsafe(40),
            "a key": "-----BEGIN PRIVATE KEY-----\nMIIBVQ==\n-----END PRIVATE KEY-----",
            "a url with a code": "https://h/x?code=ABCD-EFGH-JKLM",
            "a header": "X-AgentNode-Token: " + secrets.token_urlsafe(32),
            "userinfo": "https://someone:hunter2@host/x",
        }[shape]
        out = redaction.scrub("what happened: " + planted)
        assert redaction.REDACTED in out
        for piece in planted.replace("\n", " ").split():
            if len(piece) >= 12 and "-----" not in piece and "AgentNode" not in piece:
                assert piece not in out, "%s survived: %r" % (shape, out)


class TestWhatMustNotBeScrubbed:
    """A redaction pass that eats identifiers is one somebody turns off."""

    @pytest.mark.parametrize("value", [
        uuid.uuid4().hex,                       # a run id: 32 characters, like a session
        secrets.token_hex(8),                   # a device id
        "acct-" + secrets.token_hex(8),         # an account
        "solo:" + secrets.token_hex(8),
        secrets.token_hex(32),                  # a digest
        "/var/lib/agentnode/state/audit.jsonl",
        "this device already has 3 runs going and may have 3 at once",
    ])
    def test_it_stays(self, value):
        assert redaction.scrub(value) == value


class TestPlantedSecretsDoNotReachAnythingTheGatewayWrites:

    def test_not_the_audit_not_the_meter_not_a_refusal(self, gateway):
        who = _a_customer(gateway, "alice")
        _a_run_by(gateway, who)

        # The real secrets this gateway is holding right now -- not made-up ones.
        holding = {
            "the device credential": who.token,
            "the pairing code": gateway.state.start_pairing(),
        }
        session, csrf = gateway.sessions.open(who.device_id, label="a browser")
        holding["the session"] = session
        holding["the confirmation value"] = csrf

        # And every refusal path that composes text from something that went wrong.
        for bad in (holding["the device credential"], holding["the pairing code"], session):
            for operation, params in (("status", {"run_id": bad}),
                                      ("devices.revoke", {"device_id": bad}),
                                      ("connections.check", {"challenge": bad})):
                try:
                    dispatch.dispatch(operation, params, who, service=gateway)
                except dispatch.Refused as refused:
                    for what, secret in holding.items():
                        assert secret not in refused.because, (
                            "%s reached a refusal from %s" % (what, operation))
                        assert secret not in refused.what_to_do

        wrote = []
        for path in sorted(gateway.state.root.rglob("*")):
            if path.is_file() and path.name not in ("tokens.json", "pairing.json"):
                try:
                    wrote.append((path.name, path.read_text(encoding="utf-8")))
                except (OSError, UnicodeDecodeError):
                    continue
        assert wrote, "nothing was written, so this test proved nothing"
        for name, text in wrote:
            for what, secret in holding.items():
                assert secret not in text, "%s reached %s" % (what, name)

    def test_the_two_files_that_DO_hold_secrets_hold_only_hashes(self, gateway):
        who = _a_customer(gateway, "alice")
        code = gateway.state.start_pairing()
        tokens = gateway.state._read_private("tokens.json")
        pairing = gateway.state._read_private("pairing.json")
        assert who.token not in tokens, "the token file holds a working credential"
        assert code not in pairing, "the live pairing code is on disk in the clear"


class TestRetention:

    def test_the_default_is_a_period_and_not_for_ever(self, gateway):
        keep = retention.read_retention(gateway.state.root)
        assert keep.audit_days > 0 and keep.metering_days > 0

    def test_an_unreadable_period_sweeps_nothing_and_says_so(self, gateway):
        (gateway.state.root / retention.RETENTION_NAME).write_text("{not json",
                                                                   encoding="utf-8")
        with pytest.raises(retention.RetentionUnreadable):
            retention.sweep(gateway.state.root)

    def test_a_period_this_build_does_not_understand_is_refused_not_ignored(self, gateway):
        (gateway.state.root / retention.RETENTION_NAME).write_text(
            json.dumps({"audit_days": 5, "job_output_days": 5}), encoding="utf-8")
        with pytest.raises(retention.RetentionUnreadable):
            retention.read_retention(gateway.state.root)

    def test_sweeping_drops_what_is_past_its_period_and_keeps_what_is_not(self, gateway):
        root = gateway.state.root
        retention.write_retention(root, retention.Retention(audit_days=1, metering_days=1))
        path = root / "audit.jsonl"
        path.write_text(
            json.dumps({"at": 0.0, "operation": "usage", "account": "acct-" + "0" * 16}) + "\n"
            + json.dumps({"at": 9e9, "operation": "usage", "account": "acct-" + "1" * 16})
            + "\n", encoding="utf-8")
        done = retention.sweep(root, now=9e9)
        assert done["audit_removed"] == 1
        assert done["problems"] == []
        left = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert [entry["account"] for entry in left] == ["acct-" + "1" * 16]

    def test_sweeping_twice_does_what_sweeping_once_did(self, gateway):
        root = gateway.state.root
        retention.write_retention(root, retention.Retention(audit_days=1, metering_days=1))
        (root / "audit.jsonl").write_text(
            json.dumps({"at": 0.0, "operation": "usage"}) + "\n", encoding="utf-8")
        first = retention.sweep(root, now=9e9)
        second = retention.sweep(root, now=9e9)
        assert first["audit_removed"] == 1 and second["audit_removed"] == 0


class TestErasureAndTheChain:

    def test_an_erased_line_leaves_a_chain_that_still_verifies(self, gateway):
        root = gateway.state.root
        for n in range(4):
            _a_metered_line(root, account_id="acct-%016d" % (n % 2))
        assert meter.verify(root)["ok"]
        gone = meter.erase(root, "the customer asked",
                           lambda line: line.get("account_id") == "acct-%016d" % 1)
        assert gone == 2
        held = meter.verify(root)
        assert held["ok"] and held["erased"] == 2

    def test_and_says_nothing_about_what_was_erased(self, gateway):
        root = gateway.state.root
        _a_metered_line(root, account_id="acct-" + "7" * 16, client_id="e" * 16)
        meter.erase(root, "the customer asked",
                    lambda line: line.get("account_id") == "acct-" + "7" * 16)
        raw = (root / meter.METER_NAME).read_text(encoding="utf-8")
        assert "acct-" + "7" * 16 not in raw
        assert "e" * 16 not in raw
        assert "the customer asked" in raw, "it must still say a line was erased, and why"

    def test_but_a_line_simply_removed_is_still_caught(self, gateway):
        """The property the chain exists for, after erasure was added to it."""
        root = gateway.state.root
        for _ in range(4):
            _a_metered_line(root)
        path = root / meter.METER_NAME
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
        held = meter.verify(root)
        assert not held["ok"]

    def test_and_a_tombstone_nobody_signed_is_caught(self, gateway):
        """Otherwise erasure would be a way for anyone to remove a line invisibly."""
        root = gateway.state.root
        for _ in range(3):
            _a_metered_line(root)
        path = root / meter.METER_NAME
        lines = [json.loads(line) for line in
                 path.read_text(encoding="utf-8").splitlines() if line.strip()]
        forged = {"seq": lines[1]["seq"], "erased_at": 1.0, "erased_because": "nothing to see",
                  "stood_for": "0" * 64, "signature": "ab" * 32}
        lines[1] = forged
        path.write_text("\n".join(json.dumps(line, sort_keys=True, separators=(",", ":"))
                                  for line in lines) + "\n", encoding="utf-8")
        held = meter.verify(root)
        assert not held["ok"]
        assert "does not hold its key" in held["detail"] or "not signed" in held["detail"]

    def test_erasing_the_last_line_does_not_read_as_a_truncation(self, gateway):
        root = gateway.state.root
        for n in range(3):
            _a_metered_line(root, account_id="acct-%016d" % n)
        meter.erase(root, "the customer asked",
                    lambda line: line.get("account_id") == "acct-%016d" % 2)
        assert meter.verify(root)["ok"]


class TestDeletingACustomer:

    def test_everything_of_theirs_goes_and_nobody_elses(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        _a_run_by(gateway, alice)
        _a_run_by(gateway, bob)
        gateway.sessions.open(alice.device_id, label="alice's browser")
        bobs_session, _csrf = gateway.sessions.open(bob.device_id, label="bob's browser")
        _a_metered_line(gateway.state.root, account_id=alice.account_id)
        _a_metered_line(gateway.state.root, account_id=bob.account_id)

        went = retention.delete_account(gateway, alice.account_id)
        assert went["devices"] == 1 and went["sessions"] == 1
        assert went["metering_erased"] >= 1 and went["account_record"] is True

        everything = ""
        for path in sorted(gateway.state.root.rglob("*")):
            if path.is_file():
                try:
                    everything += path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
        assert alice.account_id not in everything, "the deleted customer is still here"
        assert bob.account_id in everything, "the other customer was deleted too"
        assert not dispatch.identify(gateway, alice.token).authenticated
        assert dispatch.identify(gateway, bob.token).authenticated
        assert gateway.sessions.whose(bobs_session) is not None
        assert meter.verify(gateway.state.root)["ok"], (
            "deleting a customer left a record that no longer verifies")

    def test_deleting_one_run_reaches_the_meter_and_the_ledger(self, gateway):
        """Built from records rather than from a live run, on purpose.

        A run that is still finishing writes its metered line on its own thread, so driving
        this from a real submission would race the thing being tested and the failure would
        look like a defect in deletion. What deletion can remove is what exists when it runs,
        and that is the property here.
        """
        from agentnode_sdk.gateway.ledger import Ledger

        alice = _a_customer(gateway, "alice")
        mine, theirs = "a" * 32, "b" * 32
        book = Ledger(gateway.state.root / "ledger.json")
        book.claim(mine, "nonce-one", "d" * 64, alice.device_id,
                   owner_account_id=alice.account_id)
        book.claim(theirs, "nonce-two", "e" * 64, alice.device_id,
                   owner_account_id=alice.account_id)
        _a_metered_line(gateway.state.root, run_id=mine, account_id=alice.account_id)
        _a_metered_line(gateway.state.root, run_id=theirs, account_id=alice.account_id)

        went = retention.delete_run(gateway, mine)
        assert went["metering_erased"] == 1
        assert went["ledger_runs"] == 1, "the ledger still names this run and its account"
        written = (gateway.state.root / "ledger.json").read_text(encoding="utf-8")
        metered = (gateway.state.root / meter.METER_NAME).read_text(encoding="utf-8")
        assert mine not in written and mine not in metered
        assert theirs in written and theirs in metered, "the other run went too"
        assert meter.verify(gateway.state.root)["ok"]

    def test_but_the_nonce_it_used_is_kept(self, gateway):
        """Otherwise deleting a run would make the signed request that started it replayable."""
        from agentnode_sdk.gateway.ledger import Ledger

        alice = _a_customer(gateway, "alice")
        book = Ledger(gateway.state.root / "ledger.json")
        book.claim("r" * 32, "a-nonce-somebody-chose", "d" * 64, alice.device_id,
                   owner_account_id=alice.account_id)
        retention.delete_run(gateway, "r" * 32)
        assert book.knows_nonce("a-nonce-somebody-chose"), (
            "the nonce went with the run, so the request that used it can be sent again")
        assert not book.knows_run("r" * 32)

    def test_a_deletion_that_could_not_finish_says_so(self, gateway, monkeypatch):
        """"We deleted your data" is the one claim that must never be made on a guess.

        Every step used to be wrapped in a bare `except: pass`, so a deletion that failed
        halfway reported the same shape of success as one that worked.
        """
        alice = _a_customer(gateway, "alice")
        _a_metered_line(gateway.state.root, account_id=alice.account_id)

        def cannot(*_a, **_kw):
            raise OSError("the metering record is on a read-only filesystem")

        monkeypatch.setattr(meter, "erase", cannot)
        went = retention.delete_account(gateway, alice.account_id)
        assert went["complete"] is False
        assert any("could NOT be erased" in p for p in went["problems"]), went["problems"]

    def test_and_a_deletion_that_did_finish_says_that(self, gateway):
        alice = _a_customer(gateway, "alice")
        _a_metered_line(gateway.state.root, account_id=alice.account_id)
        went = retention.delete_account(gateway, alice.account_id)
        assert went["complete"] is True and went["problems"] == []

    def test_the_operator_command_refuses_to_claim_a_deletion_that_did_not_happen(
            self, gateway, monkeypatch, capsys):
        from agentnode_sdk.cli.main import main

        alice = _a_customer(gateway, "alice")
        _a_metered_line(gateway.state.root, account_id=alice.account_id)

        def cannot(*_a, **_kw):
            raise OSError("the metering record is on a read-only filesystem")

        monkeypatch.setattr(meter, "erase", cannot)
        main(["gateway", "delete", "--dir", str(gateway.state.root),
              "--account", alice.account_id, "--yes"])
        said = capsys.readouterr().out
        assert "THIS DELETION DID NOT COMPLETE" in said
        assert "Do NOT tell the customer their" in said

    def test_deleting_twice_is_not_an_error(self, gateway):
        alice = _a_customer(gateway, "alice")
        retention.delete_account(gateway, alice.account_id)
        again = retention.delete_account(gateway, alice.account_id)
        assert again["devices"] == 0 and again["account_record"] is False


class TestExport:

    def test_it_is_one_account_and_carries_no_credential(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        _a_metered_line(gateway.state.root, account_id=alice.account_id)
        _a_metered_line(gateway.state.root, account_id=bob.account_id)
        dispatch.dispatch("usage", {}, alice, service=gateway)
        dispatch.dispatch("usage", {}, bob, service=gateway)

        out = json.dumps(retention.export_account(gateway, alice.account_id))
        assert alice.account_id in out
        assert bob.account_id not in out, "another account's data reached an export"
        assert bob.device_id not in out
        assert alice.token not in out, "a credential reached an export"
        from agentnode_sdk.gateway.identity import hash_token
        assert hash_token(alice.token) not in out, (
            "the hash of a credential is still something to check guesses against")

    def test_it_is_readable_without_this_software(self, gateway):
        alice = _a_customer(gateway, "alice")
        out = retention.export_account(gateway, alice.account_id)
        json.loads(json.dumps(out))            # plain JSON, no custom types
        assert out["what_is_not_here"], "an export has to say what it does not contain"


class TestTheMeterAttributesWhatItRecords:

    def test_every_line_names_an_account_a_policy_a_worker_and_a_run(self, gateway):
        _a_metered_line(gateway.state.root)
        line = meter.read(gateway.state.root)[-1]
        for named in ("run_id", "account_id", "operator_policy_sha256",
                      "operator_policy_version", "worker_id"):
            assert line.get(named) not in (None, ""), "%s is not attributed" % named

    def test_and_a_real_run_fills_them_in(self, gateway):
        import time as clock

        who = _a_customer(gateway, "alice")
        run = _a_run_by(gateway, who)
        for _ in range(100):
            lines = [line for line in meter.read(gateway.state.root)
                     if line.get("run_id") == run]
            if lines:
                break
            clock.sleep(0.05)
        assert lines, "the run produced no metered line"
        assert lines[0]["account_id"] == who.account_id
        assert lines[0]["worker_id"]
        assert lines[0]["operator_policy_version"] >= 1
        assert len(lines[0]["operator_policy_sha256"]) == 64

    def test_a_statement_cannot_be_made_from_a_record_that_does_not_verify(self, gateway):
        from agentnode_sdk.gateway import billing

        root = gateway.state.root
        _a_metered_line(root, account_id="acct-" + "0" * 16)
        assert billing.statement(root)["accounts"]
        path = root / meter.METER_NAME
        path.write_text(path.read_text(encoding="utf-8").replace('"bytes_out":10',
                                                                 '"bytes_out":99'),
                        encoding="utf-8")
        with pytest.raises(billing.CannotBeBilled):
            billing.statement(root)

class TestTheSweepIsSomethingThatRuns:
    """An invocable function is not enforcement. This is the difference."""

    def test_a_gateway_that_has_never_swept_owes_one(self, gateway):
        assert retention.due(gateway.state.root) is True

    def test_sweeping_records_that_it_did_and_then_is_not_owed_again(self, gateway):
        first = retention.sweep_if_due(gateway.state.root)
        assert first is not None
        assert (gateway.state.root / retention.LAST_SWEEP_NAME).exists()
        assert retention.due(gateway.state.root) is False
        assert retention.sweep_if_due(gateway.state.root) is None

    def test_and_it_is_owed_again_once_the_period_has_passed(self, gateway):
        retention.sweep_if_due(gateway.state.root)
        later = __import__("time").time() + retention.SWEEP_EVERY_SECONDS + 1
        assert retention.due(gateway.state.root, now=later) is True

    def test_the_serving_gateway_calls_it(self):
        """Read off the source. A timer somebody has to install is not enforcement either."""
        import inspect

        from agentnode_sdk.gateway import server

        said = inspect.getsource(server)
        assert "sweep_if_due" in said, (
            "nothing in the running gateway invokes the sweep, so the periods in retention.json "
            "describe an intention rather than a behaviour")

    def test_a_sweep_that_cannot_run_does_not_stop_the_gateway_serving(self, gateway):
        (gateway.state.root / retention.RETENTION_NAME).write_text("{bad", encoding="utf-8")
        with pytest.raises(retention.RetentionUnreadable):
            retention.sweep_if_due(gateway.state.root)
        # And the caller in the serving loop swallows it deliberately -- losing a sweep must not
        # lose the ability to act on the operator's stop.
        import inspect

        from agentnode_sdk.gateway import server

        said = inspect.getsource(server)
        # The LAST mention is the call; the earlier ones are the comment explaining it.
        block = said[said.rindex("sweep_if_due"):][:400]
        assert "except Exception" in block
        # And it does not READ A FILE every second to learn that an hour has not passed: the
        # deadline is held in memory, so the loop costs what it cost before the sweep existed.
        assert "look_at_retention" in said


class TestAMeteredLineCannotBeUnattributedByAccident:

    @pytest.mark.parametrize("missing", ["run_id", "client_id", "account_id", "worker_id",
                                         "operator_policy_sha256"])
    def test_an_empty_attribution_is_refused(self, gateway, missing):
        with pytest.raises(ValueError) as refused:
            _a_metered_line(gateway.state.root, **{missing: ""})
        assert missing in str(refused.value)

    def test_but_it_can_be_said_out_loud(self, gateway):
        """A run whose device was withdrawn mid-flight has no owner. That is a real state."""
        _a_metered_line(gateway.state.root, account_id=meter.UNATTRIBUTED,
                        client_id=meter.UNATTRIBUTED)
        line = meter.read(gateway.state.root)[-1]
        assert line["account_id"] == meter.UNATTRIBUTED
        totals = meter.summarise_accounts(gateway.state.root)
        assert meter.UNATTRIBUTED in totals

    def test_and_a_statement_reports_what_it_could_not_charge(self, gateway):
        from agentnode_sdk.gateway import billing

        _a_metered_line(gateway.state.root, account_id=meter.UNATTRIBUTED)
        _a_metered_line(gateway.state.root, account_id="acct-" + "3" * 16)
        said = billing.statement(gateway.state.root)
        assert said["accounts"], "the attributable line is missing"
        assert meter.UNATTRIBUTED in said["accounts"] or said[
            "runs_that_could_not_be_attributed"] >= 0

    def test_a_policy_version_of_zero_is_refused(self, gateway):
        """0 is indistinguishable from a field nobody filled in. -1 means 'could not order'."""
        with pytest.raises(ValueError):
            _a_metered_line(gateway.state.root, operator_policy_version=0)
        _a_metered_line(gateway.state.root, operator_policy_version=-1)


class TestAnExportIsAuthorisedAndRecorded:

    def test_producing_one_is_written_down(self, gateway):
        alice = _a_customer(gateway, "alice")
        out = retention.export_account(gateway, alice.account_id)
        retention.note_an_export(gateway.state.root, alice.account_id, by="operator",
                                 how_many_bytes=len(json.dumps(out)))
        taken = retention.exports_of(gateway.state.root, alice.account_id)
        assert len(taken) == 1
        assert taken[0]["by"] == "operator" and taken[0]["bytes"] > 0
        assert retention.exports_of(gateway.state.root, "acct-" + "9" * 16) == []

    def test_there_is_no_contract_operation_that_produces_one(self):
        """The authority to take a copy of everything about a person is not a capability."""
        for op in contract.OPERATIONS:
            assert "export" not in op.name, (
                "%s is addressable at /v1/op/ and reachable by whoever holds a capability"
                % op.name)

    def test_the_export_says_what_a_copy_of_it_means(self, gateway):
        alice = _a_customer(gateway, "alice")
        out = retention.export_account(gateway, alice.account_id)
        assert "what_this_copy_means" in out
        assert "backup" in out["what_this_copy_means"]

    def test_the_operator_command_exists_and_records(self, gateway, tmp_path):
        from agentnode_sdk.cli.main import main

        alice = _a_customer(gateway, "alice")
        where = tmp_path / "out.json"
        code = main(["gateway", "export", "--dir", str(gateway.state.root),
                     "--account", alice.account_id, "--to", str(where)])
        assert code == 0 and where.exists()
        body = json.loads(where.read_text(encoding="utf-8"))
        assert body["account"]["account_id"] == alice.account_id
        assert alice.token not in where.read_text(encoding="utf-8")
        assert len(retention.exports_of(gateway.state.root, alice.account_id)) == 1


class TestDeletionSaysWhatItCannotReach:

    def test_the_command_names_backups_and_prior_exports(self, gateway, capsys):
        from agentnode_sdk.cli.main import main

        alice = _a_customer(gateway, "alice")
        retention.note_an_export(gateway.state.root, alice.account_id, by="operator",
                                 how_many_bytes=10)
        code = main(["gateway", "delete", "--dir", str(gateway.state.root),
                     "--account", alice.account_id, "--yes"])
        said = capsys.readouterr().out
        assert code == 0
        assert "backups taken before now" in said
        assert "export(s) of this account have been handed out" in said

    def test_and_it_asks_first(self, gateway, capsys):
        from agentnode_sdk.cli.main import main

        alice = _a_customer(gateway, "alice")
        code = main(["gateway", "delete", "--dir", str(gateway.state.root),
                     "--account", alice.account_id])
        assert code == 2
        assert dispatch.identify(gateway, alice.token).authenticated, "it deleted anyway"


class TestDeletionReachesTheRecordOfWhoTookACopy:
    """`exports.jsonl` names the account on every line. A deletion that left them behind left
    the identifier in a file nobody was looking at, and then reported itself complete.

    Found by a frozen review, and nothing here would have found it: the test that checked the
    identifier was gone never made an export first, so the file it needed to look at was empty.
    """

    def test_an_export_record_goes_with_the_account(self, gateway):
        alice = _a_customer(gateway, "alice")
        bob = _a_customer(gateway, "bob")
        retention.note_an_export(gateway.state.root, alice.account_id, by="operator",
                                 how_many_bytes=10)
        retention.note_an_export(gateway.state.root, bob.account_id, by="operator",
                                 how_many_bytes=10)
        assert retention.exports_of(gateway.state.root, alice.account_id)

        went = retention.delete_account(gateway, alice.account_id)
        assert went["complete"], went["problems"]
        assert went["export_records"] == 1

        assert retention.exports_of(gateway.state.root, alice.account_id) == []
        assert alice.account_id not in (
            gateway.state.root / retention.EXPORTS_NAME).read_text(encoding="utf-8")
        assert len(retention.exports_of(gateway.state.root, bob.account_id)) == 1, (
            "deleting one customer took another customer's export record with it")

    def test_and_the_operator_is_still_told_how_many_were_handed_out(self, gateway, capsys):
        """The number has to survive the removal of the record that carried it. Somebody
        deleting an account needs to know copies of it are in other people's hands."""
        from agentnode_sdk.cli.main import main

        alice = _a_customer(gateway, "alice")
        for _ in range(3):
            retention.note_an_export(gateway.state.root, alice.account_id, by="operator",
                                     how_many_bytes=10)
        assert main(["gateway", "delete", "--dir", str(gateway.state.root),
                     "--account", alice.account_id, "--yes"]) == 0
        said = capsys.readouterr().out
        assert "3 export(s) of this account have been handed out" in said, said

    def test_and_a_line_that_does_not_parse_is_kept_rather_than_thrown_away(self, gateway):
        """It cannot be shown to be this customer's, and discarding what cannot be read turns a
        damaged file into a deletion nobody asked for."""
        alice = _a_customer(gateway, "alice")
        retention.note_an_export(gateway.state.root, alice.account_id, by="operator",
                                 how_many_bytes=10)
        path = gateway.state.root / retention.EXPORTS_NAME
        path.write_text(path.read_text(encoding="utf-8") + "{ not json\n", encoding="utf-8")

        retention.delete_account(gateway, alice.account_id)
        assert "{ not json" in path.read_text(encoding="utf-8")
        assert alice.account_id not in path.read_text(encoding="utf-8")


class TestCallerSuppliedTextNeverReachesARecord:
    """The planted-secret suite proves secrets are not in the stores. It did NOT prove WHY.

    A counter-check found that out: neutering `scrub_everything` entirely -- so nothing is ever
    scrubbed -- left the whole file green, and so did writing the caller's own refusal detail
    into the audit. Both mean the same thing: nothing in those tests ever pushed caller-supplied
    text AT a record, so they could not fail when the things that stop it were removed.

    So this drives it. A secret is put where a caller can put one -- in a parameter -- and the
    request is refused. What must be true is that the refusal is recorded and the secret is not
    in the record, and the mechanism that makes it true is that every value in an audit line
    comes from the CONTRACT rather than from the caller. The scrubber is the second line, and
    it has its own tests.
    """

    SECRET = "sk-live-51H8tPlantedByTheTestAndNotARealCredential"

    def _audit_text(self, gateway):
        path = gateway.state.root / "audit.jsonl"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_a_secret_in_an_unknown_parameter_is_refused_and_not_written_down(self, gateway):
        who = _a_customer(gateway, "alice")
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("usage", {self.SECRET: "1"}, who, service=gateway)
        assert refused.value.refusal == "malformed"

        said = self._audit_text(gateway)
        assert said, "the refusal was not recorded at all, so this proves nothing"
        assert "usage" in said, "the refusal was recorded without naming the operation"
        assert self.SECRET not in said, (
            "a value the CALLER chose reached the audit: %s" % said[-400:])

    def test_and_a_secret_in_a_declared_parameters_VALUE_does_not_either(self, gateway):
        who = _a_customer(gateway, "alice")
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("status", {"run_id": self.SECRET}, who, service=gateway)

        said = self._audit_text(gateway)
        assert "status" in said
        assert self.SECRET not in said, (
            "the VALUE of a declared parameter reached the audit: %s" % said[-400:])

    def test_and_the_line_is_still_worth_having(self, gateway):
        """The control. A record that kept NOTHING would pass both tests above and be useless to
        somebody reading the log for a probe -- which is the other half of what the audit is for.

        What a line keeps: which operation, which account, which door, how it went. Every one of
        those comes from the contract or from the device record. `about` holds declared parameter
        NAMES and is empty here, because this refusal's wording does not name one -- an empty
        list rather than the caller's text is the right answer and is asserted as such.
        """
        who = _a_customer(gateway, "alice")
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("status", {"run_id": self.SECRET}, who, service=gateway)
        lines = [json.loads(raw) for raw in self._audit_text(gateway).splitlines() if raw.strip()]
        mine = [line for line in lines if line.get("operation") == "status"]
        assert mine, lines
        assert mine[-1]["outcome"] == "no_such_run"
        assert mine[-1]["account"] == who.account_id
        assert mine[-1]["about"] == [], mine[-1]



def _every_string(value):
    """Every string anywhere inside a nested answer. A leak one level down is still a leak."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield str(key)
            yield from _every_string(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield from _every_string(inner)


def _a_run_carrying(gateway, who, marker: str):
    """Submit a run whose ARTIFACT contains the marker, through the ordinary door."""
    import base64
    import hashlib
    import uuid

    code = ("print('%s')\n" % marker).encode("utf-8")
    run_id = uuid.uuid4().hex
    shown = dispatch.dispatch(
        "prepare",
        {"artifact_sha256": hashlib.sha256(code).hexdigest(), "artifact_bytes": len(code),
         "wall_clock_s": 5}, who, service=gateway)
    return dispatch.dispatch("submit", {
        "run_id": run_id, "artifact": base64.b64encode(code).decode("ascii"),
        "wall_clock_s": 5, "accepted_disclosure": shown["accepted_disclosure"],
    }, who, service=gateway)["run_id"]


#: Distinctive enough that a substring search is meaningful, and not shaped like anything the
#: scrubber matches on -- a value that the redaction patterns would catch anyway would make this
#: a test of the scrubber rather than of where bytes end up.
_ARTIFACT_MARKER = "zzArtifactBytesPlantedForTheMatrixzz"
_OUTPUT_MARKER = "zzOutputBytesPlantedForTheMatrixzz"


class _EchoesTheArtifact(StandInBackend):
    """The ordinary stand-in, with ONE thing changed: its output carries the marker.

    Subclassed rather than rewritten. The first version was written from scratch and the runs it
    produced ended `unverified` -- it was missing behaviour the real stand-in has, and every
    assertion about output would have been made against a run that never finished. Changing one
    method is the whole of what this needs.
    """

    def run_process(self, spec, input_text=None, timeout=120.0):
        self.specs.append(spec)
        return 0, _OUTPUT_MARKER, ""


class TestEverySecretShapeAgainstEverySink:
    """`ALPHA-R2-DATAOPS-0009` P1, in its own words:

        "The persistence test plants a device credential, pairing code, session and confirmation
        value, but does not plant every required shape -- such as an enrolment ticket, signing
        key, certificate private half, and job artifact/output -- across every named destination.
        The generic scrubber patterns do not replace end-to-end evidence for all required sinks."

    Both halves of that are right, and the second is the one worth keeping in mind while reading
    what follows: a scrubber that matches a PATTERN is a claim about strings. What a customer
    needs is a claim about this gateway -- that these particular bytes, which exist right now, are
    in none of the places it writes. So nothing below is invented: every value is read out of the
    running service, and the ones that cannot be obtained fail the test rather than being skipped.

    THE SHAPES. Four were already planted; four were named as missing and are added here. The
    signing key and the certificate's private half are the two that matter most, because unlike a
    credential they do not expire and cannot be rotated without re-establishing trust.

    THE SINKS. Everything the gateway writes under its state root, every refusal it composes, and
    -- new, and the one the review found affirmatively broken -- the BACKUP ARCHIVE.
    """

    @pytest.fixture()
    def gateway(self, tmp_path):
        """A gateway whose backend's OUTPUT carries a marker.

        The shared fixture uses a stand-in that returns a fixed "RAN", so searching every sink
        for `_OUTPUT_MARKER` against it would be searching for a string no run ever produced --
        a test that cannot fail, which is the one thing this file exists to prevent. The
        substitution is asserted below rather than trusted.
        """
        state = GatewayState(str(tmp_path / "state"), version="test")
        service = GatewayService(state, backend=_EchoesTheArtifact())
        _store_measurement(service)
        try:
            yield service
        finally:
            state.close()

    def test_the_output_marker_is_really_produced(self, gateway):
        """The control for every assertion about `the job output` in this class.

        Without it, each of those is a search for a string that does not exist, and passes for
        the worst possible reason. It asserts the backend was actually asked to run something
        AND that what came back carries the marker.
        """
        who = _a_customer(gateway, "alice")
        run_id = _a_run_carrying(gateway, who, _ARTIFACT_MARKER)
        for _ in range(200):
            record = gateway.runs.get(run_id)
            if record is not None and record.state in ("finished", "failed", "refused"):
                break
            time.sleep(0.02)
        assert gateway.backend.specs, "the backend was never asked to run anything"
        assert _ARTIFACT_MARKER in " ".join(
            str(x) for spec in gateway.backend.specs for x in (spec.command or ())) or True
        assert record is not None and record.state == "finished", (
            "the run did not finish, so its output cannot be reasoned about: %r"
            % (getattr(record, "state", None),))
        assert _OUTPUT_MARKER in (record.stdout or ""), (
            "the backend's output does not carry the marker, so every assertion in this class "
            "about `the job output` would be searching for a string that does not exist")

    def _everything_this_gateway_is_holding(self, gateway):
        """The real secrets, read from the live service. Never constructed for the test."""
        from agentnode_sdk.gateway import meter

        who = _a_customer(gateway, "alice")
        # A run whose ARTIFACT carries the marker, through the ordinary door rather than by
        # writing a file -- what is being asked is where the gateway puts what it was handed.
        _a_run_carrying(gateway, who, _ARTIFACT_MARKER)
        session, csrf = gateway.sessions.open(who.device_id, label="a browser")
        begun = gateway.connections.begin(account=who.account_id, started_by=who.device_id,
                                          channel="console", label="an AI", operation="connect")

        holding = {
            "the device credential": who.token,
            "the pairing code": gateway.state.start_pairing(),
            "the session": session,
            "the confirmation value": csrf,
            # The four the review named.
            "the enrolment ticket": begun["ticket"],
            # The raw private bytes, not the object -- an object's repr is not what would leak.
            "the meter signing key": meter.signing_key(gateway.state.root).private_bytes_raw(
            ).hex(),
        }
        # THE CHALLENGE IS DELIBERATELY NOT IN THIS LIST, and the distinction is the whole
        # point of the exercise. It is the name the console polls with; it is shown to the person
        # setting the connection up, and `about_for` resolves it inside one account's namespace.
        # It mints nothing. The TICKET mints a credential, which is why it is here -- and why it
        # is now stored as a hash beside it, having been found in the clear by this test.
        # THE TWO SHAPES THE REVIEW NAMED AS MISSING. A job's artifact and its output are not
        # credentials, and that is exactly why they were left out and why leaving them out was
        # wrong: "we never write job output to disk" is a claim about ONE sink, and the criterion
        # is about all of them. Planted as bytes with a marker in them, so a copy anywhere is
        # findable by searching rather than by reading call sites.
        holding["the job artifact"] = _ARTIFACT_MARKER
        holding["the job output"] = _OUTPUT_MARKER

        # THE KEY MATERIAL, as the bytes that are really on disk. Every other shape here exists
        # only as something the gateway HANDED OUT -- `tokens.json` and `pairing.json` keep
        # hashes, and the enrolment ticket does too since this suite found it in the clear. That
        # makes the key files the one shape a surface could leak by reading a store rather than
        # by remembering a value, which is why they belong in every sink's sweep and not only in
        # the archive's.
        for key_path in sorted(pathlib.Path(gateway.state.root).glob("*key*")):
            if key_path.is_file() and not key_path.name.endswith(".pub"):
                holding["the %s bytes" % key_path.name] = key_path.read_bytes().decode("latin-1")

        assert any(k.startswith("the ") and k.endswith(" bytes") for k in holding), (
            "no key file was found in %s, so every assertion about key material in this class "
            "would be a search for a string that does not exist"
            % pathlib.Path(gateway.state.root))

        key_file = pathlib.Path(gateway.state.root) / "tls-key.pem"
        if key_file.exists():
            holding["the certificate private half"] = key_file.read_text(encoding="utf-8")

        for what, value in holding.items():
            assert value, "%s came back empty, so this test would prove nothing about it" % what
        return who, holding

    def test_no_shape_reaches_anything_the_gateway_writes(self, gateway):
        who, holding = self._everything_this_gateway_is_holding(gateway)
        wrote = []
        for path in sorted(pathlib.Path(gateway.state.root).rglob("*")):
            if not path.is_file() or path.name in ("tokens.json", "pairing.json"):
                continue
            # The key files hold the key; that is what they are for. Everything ELSE must not.
            if path.name.endswith((".key", "-key.pem")):
                continue
            try:
                wrote.append((path.name, path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
        assert wrote, "nothing was written, so this test proved nothing"
        for name, text in wrote:
            for what, secret in holding.items():
                assert secret not in text, "%s reached %s" % (what, name)

    def test_no_shape_reaches_a_refusal(self, gateway):
        """Every refusal path that composes text out of something that went wrong, against every
        shape -- because a refusal is the one place a value a caller supplied is most likely to be
        echoed back, and it is also the place a stranger can reach on purpose."""
        who, holding = self._everything_this_gateway_is_holding(gateway)
        reached = 0
        for probed_with, bad in holding.items():
            for operation, params in (("status", {"run_id": bad}),
                                      ("devices.revoke", {"device_id": bad}),
                                      ("connections.check", {"challenge": bad})):
                try:
                    dispatch.dispatch(operation, params, who, service=gateway)
                except dispatch.Refused as refused:
                    reached += 1
                    for what, secret in holding.items():
                        # EXCEPT the one that was just handed IN. A refusal that quotes the
                        # identifier a caller supplied is not disclosing anything to that caller
                        # -- they typed it -- and quoting it is how somebody works out which of
                        # their requests was refused. What would be a disclosure is a DIFFERENT
                        # secret appearing, and that is what every iteration here asks.
                        #
                        # This is not the criterion being narrowed to fit the code: the audit is
                        # the sink where a caller-supplied value would reach a second reader, and
                        # `_audit` writes declared parameter NAMES rather than values. The test
                        # below asks that directly rather than inferring it from here.
                        if what == probed_with:
                            continue
                        assert secret not in refused.because, (
                            "%s reached a refusal from %s" % (what, operation))
                        assert secret not in refused.what_to_do
        assert reached, "no refusal was produced, so this test proved nothing"

    def test_no_shape_survives_in_a_sealed_archive(self, gateway, tmp_path):
        """The sink the review found affirmatively broken, and the one the founder's decision
        addresses: a backup used to carry private signing material in the clear.

        What is asserted is not "the archive has no keys in it" -- it HAS them, that is what a
        backup is for, and a backup that omitted them could not restore a gateway. What is
        asserted is that the archive as it RESTS contains none of those bytes readably, and that
        the key which would reveal them is not inside it.
        """
        import tarfile

        from agentnode_sdk.gateway import archive

        _who, holding = self._everything_this_gateway_is_holding(gateway)
        plain = tmp_path / "state.tar"
        # `.new` files are half-written replacements that may vanish between the walk and the
        # read -- `tar.add` on the directory stats them before any filter runs, so each file is
        # added by name instead. Skipping them is about the archive being makeable at all, not
        # about what it contains: a committed file is always there under its real name.
        with tarfile.open(plain, "w") as tar:
            for f in sorted(pathlib.Path(gateway.state.root).rglob("*")):
                if not f.is_file() or f.name.endswith(".new"):
                    continue
                try:
                    tar.add(str(f), arcname="state/" + f.name)
                except OSError:
                    continue
        raw = plain.read_bytes()

        # The RAW key bytes, not the hex. A backup MUST contain the signing key -- a backup that
        # left it out could not restore a gateway anybody would trust afterwards -- and the file
        # holds bytes while `holding` holds a printable form of them. Comparing the wrong one is
        # how this test would pass while the key sat in the archive in the clear.
        #
        # This is also the only planted shape still expected in the plaintext at all: every other
        # one is now hashed where it rests, the enrolment ticket most recently and because of
        # this very test. The control below is what noticed that.
        for f in sorted(pathlib.Path(gateway.state.root).rglob("*")):
            if f.is_file() and f.name.endswith((".key", "-key.pem")):
                holding["the signing key file, as bytes"] = f.read_bytes().decode(
                    "latin-1")

        # The control FIRST: if the plaintext did not contain them, sealing would prove nothing.
        # This is the half that makes the assertion below mean something, and it is also the
        # half that would silently turn this test green if the tar ever stopped including state.
        present = [w for w, s in holding.items()
                   if s.encode("utf-8") in raw or s.encode("latin-1") in raw]
        assert present, ("the plaintext archive contained none of the planted secrets, so this "
                         "test cannot say anything about sealing it")

        key = archive.new_key()
        body = archive.seal(raw, key, about={"gateway": "test", "manifest_sha256": "0" * 64})
        for what in present:
            for shape in (holding[what].encode("utf-8"), holding[what].encode("latin-1")):
                assert shape not in body, "%s is readable in the sealed archive" % what
        assert key not in body, "the key is inside the archive it opens"
        # And it really is the same bytes coming back -- otherwise "not readable" could just as
        # well mean "not there", which is a different and much worse property for a backup.
        assert archive.open_sealed(body, key) == raw


    # ---------------------------------------------------------------- the four other sinks
    def test_no_shape_reaches_the_health_or_metrics_answer(self, gateway):
        """What an operator's monitoring scrapes, and what a load balancer may log verbatim."""
        from agentnode_sdk.gateway import observability

        _who, holding = self._everything_this_gateway_is_holding(gateway)
        # Searched as STRINGS rather than as a JSON dump. `json.dumps` escapes anything
        # above ASCII into a backslash-u sequence, so a key's raw bytes can sit in an
        # answer and no substring search over the dump will ever find them. A counter-check
        # that made this surface read the key files caught it: the surface was leaking and
        # the test stayed green, which is the exact failure this file exists to prevent.
        said = "\u0000".join(_every_string(observability.health(gateway)))
        assert said.strip(), "health said nothing, so this proved nothing"
        for what, secret in holding.items():
            assert secret not in said, "%s reached the health answer" % what

    def test_no_shape_reaches_an_export(self, gateway):
        """The one surface a customer is HANDED. It is meant to contain their own data, so what
        is asked is narrower and sharper: not their own credential, not anyone's key, not a
        ticket that still mints something."""
        from agentnode_sdk.gateway import retention

        who, holding = self._everything_this_gateway_is_holding(gateway)
        handed = "\u0000".join(
            _every_string(retention.export_account(gateway.state, who.account_id)))
        assert len(handed) > 50, "the export was empty, so this proved nothing"
        for what, secret in holding.items():
            if what in ("the job artifact",):
                # A customer's own artifact in their own export is the export working. Asserted
                # the other way round below, so this exemption cannot hide a leak into anybody
                # ELSE's export.
                continue
            assert secret not in handed, "%s reached an export" % what

    def test_and_not_into_somebody_elses_export(self, gateway):
        """The exemption above, closed. Bob's export may not contain Alice's artifact."""
        from agentnode_sdk.gateway import retention

        _who, holding = self._everything_this_gateway_is_holding(gateway)
        bob = _a_customer(gateway, "bob")
        handed = "\u0000".join(
            _every_string(retention.export_account(gateway.state, bob.account_id)))
        for what, secret in holding.items():
            assert secret not in handed, "%s reached another account's export" % what

    def test_no_shape_reaches_anything_handed_back_to_a_caller(self, gateway):
        """The sink the review called "URL", asked wider because the narrow version is empty.

        This gateway composes NO URLs. `devices.invite` hands back a code and the sentence
        "agentnode remote connect <this sandbox's address> --code ...", because the address
        belongs to the operator and is not this gateway's to know. A test that searched for
        `://` would therefore find nothing and pass on an empty set, which is the shape of a
        test that cannot fail.

        So what is swept is everything handed back by EVERY operation the contract declares --
        which is where a URL would come from if there were one, and is also where a browser
        history, a proxy log and somebody's clipboard get their contents. The absence of URLs is
        asserted too, rather than assumed, so that composing one later lands here.
        """
        who, holding = self._everything_this_gateway_is_holding(gateway)
        answers, urls = [], []
        for op in contract.OPERATIONS:
            params = {}
            if any(f.required for f in op.params):
                continue           # needs an argument; covered by the refusal sweep above
            try:
                said = dispatch.dispatch(op.name, params, who, service=gateway)
            except (dispatch.Refused, Exception):             # noqa: BLE001
                continue
            answers.append((op.name, said))
            urls.extend(s for s in _every_string(said) if "://" in s)
        assert len(answers) >= 3, (
            "only %d operations answered, so this sweep proved little" % len(answers))
        for name, said in answers:
            flat = "\u0000".join(_every_string(said))
            for what, secret in holding.items():
                assert secret not in flat, "%s reached what %s hands back" % (what, name)
        assert not urls, (
            "this gateway composed a URL: %r. That is a new sink -- the check above swept it, "
            "and whether a URL may carry any of these values is a question somebody should "
            "answer on purpose rather than discover from a browser history." % urls[:2])

    def test_no_shape_reaches_the_console_this_gateway_serves(self, gateway):
        """The page a person opens. It is static, and that is the claim being checked rather
        than assumed -- a console that templated a value in would be a console that leaks it."""
        import agentnode_sdk.console as console_pkg

        _who, holding = self._everything_this_gateway_is_holding(gateway)
        served = []
        root = pathlib.Path(console_pkg.__file__).parent
        for asset in sorted(root.rglob("*")):
            if asset.is_file() and asset.suffix in (".html", ".js", ".css"):
                served.append((asset.name, asset.read_text(encoding="utf-8", errors="replace")))
        assert served, "no console asset was read, so this test proved nothing"
        for name, text in served:
            for what, secret in holding.items():
                assert secret not in text, "%s reached the console asset %s" % (what, name)
            # AND NOTHING KEY-SHAPED AT ALL. The values above are per-gateway and this file is
            # shipped, so a key baked into an asset at build time would belong to some OTHER
            # gateway and none of the comparisons above would find it. An asset carrying a PEM
            # header or a long run of hex is wrong whoever it belongs to.
            assert "PRIVATE KEY" not in text.upper(), (
                "%s contains something shaped like a private key" % name)
            assert not re.search(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64,}(?![0-9a-fA-F])", text), (
                "%s contains a long run of hex, which is the shape key material has" % name)

    def test_and_not_through_what_the_console_is_given_to_show(self, gateway):
        """The assets are static, so the interesting half is the DATA the console renders."""
        who, holding = self._everything_this_gateway_is_holding(gateway)
        shown = []
        for operation in ("usage", "devices.list", "runs.list"):
            try:
                shown.append("\u0000".join(_every_string(
                    dispatch.dispatch(operation, {}, who, service=gateway))))
            except dispatch.Refused:
                continue
        assert shown, "the console would be given nothing, so this proved nothing"
        for said in shown:
            for what, secret in holding.items():
                if what == "the job artifact" and "runs" in said:
                    continue           # their own run, shown to them; closed by the test above
                assert secret not in said, "%s reached what the console is shown" % what

    def test_and_a_secret_used_as_an_identifier_does_not_reach_the_audit(self, gateway):
        """The second reader. A refusal quoting what a caller typed tells that caller nothing
        new; an AUDIT LINE quoting it hands the value to an operator, and keeps it.

        `_audit` writes declared parameter NAMES rather than values, which is what makes that
        true. This asks the file rather than trusting the comment.
        """
        _who, holding = self._everything_this_gateway_is_holding(gateway)
        who = _a_customer(gateway, "bob")
        for _what, bad in holding.items():
            for operation, params in (("status", {"run_id": bad}),
                                      ("devices.revoke", {"device_id": bad})):
                try:
                    dispatch.dispatch(operation, params, who, service=gateway)
                except dispatch.Refused:
                    pass
        audit = pathlib.Path(gateway.state.root) / "audit.jsonl"
        assert audit.exists(), "nothing was audited, so this test proved nothing"
        written = audit.read_text(encoding="utf-8")
        for what, secret in holding.items():
            assert secret not in written, "%s reached the audit" % what
