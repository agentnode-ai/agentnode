"""What this gateway writes down, what it refuses to write down, and how it lets go of it.

The redaction tests PLANT each secret shape and then look for it in everything the gateway wrote.
That is deliberately not the same as reading the call sites and concluding they look careful: the
failure this guards against is a field somebody adds later, and only a test that searches the
OUTPUT catches that one.
"""
from __future__ import annotations

import json
import secrets
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
