"""Task #27: a customer's export carries the limits that apply to them and their one-off use.

`retention.export_account` says it is "everything AgentNode holds about one account on this
gateway", and it left out two things this gateway holds per account: the account's own entry in
`account-ceilings.json` (task #11) and the one-off lifetime counter in `use.json` (task #12). What
it must carry now, and how:

* the account's OWN ceilings exactly as written, and the EFFECTIVE account ceilings -- the
  gateway-wide ones with its own in place, computed the way admission computes them;
* the one-off allowance: how many runs it has used (never forgotten by time) and its limit;
* nothing of any other account -- not their ceilings, not their counters;
* an unknown or ill-formed account id is refused, not exported as an empty shell;
* an ABSENT file is an honest zero / "no own limits"; a DAMAGED file is said to be unreadable
  and never turned into a number, and the rest of the export is still there;
* ONE moment: the use record is read once and the window and the lifetime count come from that
  same read, at the time the export names.

All data here is synthetic.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.gateway import accounts as _accounts
from agentnode_sdk.gateway import allowance, retention
from tests.test_two_accounts import _a_customer
from tests.test_what_is_kept import gateway  # noqa: F401 - the fixture

T = 2_000_000_000.0
DAY = allowance.WINDOW_SECONDS


def _use(root, body: dict) -> None:
    (root / allowance.USE_NAME).write_text(json.dumps(body, sort_keys=True), encoding="utf-8")


def _runs(prefix: str, *ats: float) -> list:
    return [{"run_id": "%s-%d" % (prefix, i), "at": at, "seconds": 2.0} for i, at in enumerate(ats)]


def _root(gateway):
    return gateway.state.root


# ---- the limits ---------------------------------------------------------------------------------- #


def test_the_accounts_own_ceilings_and_the_effective_ones_are_in_the_export(gateway):
    alice = _a_customer(gateway, "alice")
    allowance.write_allowance(_root(gateway), allowance.Allowance(account_runs_per_window=50,
                                                                  account_concurrent_runs=2))
    allowance.write_account_ceilings(_root(gateway), alice.account_id,
                                     {"account_runs_per_window": 7, "account_lifetime_runs": 3})
    limits = retention.export_account(gateway, alice.account_id, now=T)["limits_that_apply"]
    assert limits["source"] == "own"
    assert limits["own"] == {"account_runs_per_window": 7, "account_lifetime_runs": 3}
    assert limits["effective"] == {"account_concurrent_runs": 2, "account_runs_per_window": 7,
                                   "account_seconds_per_window": 0, "account_lifetime_runs": 3}


def test_an_account_without_its_own_ceilings_is_told_the_gateway_wide_ones(gateway):
    alice = _a_customer(gateway, "alice")
    allowance.write_allowance(_root(gateway), allowance.Allowance(account_runs_per_window=50))
    limits = retention.export_account(gateway, alice.account_id, now=T)["limits_that_apply"]
    assert limits["source"] == "gateway-wide" and limits["own"] == {}
    assert limits["effective"]["account_runs_per_window"] == 50


def test_the_effective_limits_are_the_ones_admission_applies(gateway):
    alice = _a_customer(gateway, "alice")
    allowance.write_allowance(_root(gateway), allowance.Allowance(account_seconds_per_window=900))
    allowance.write_account_ceilings(_root(gateway), alice.account_id, {"account_concurrent_runs": 1})
    effective = retention.export_account(gateway, alice.account_id, now=T)[
        "limits_that_apply"]["effective"]
    applied = gateway.allowance_for(alice.account_id).as_dict()
    assert effective == {k: applied[k] for k in allowance.ACCOUNT_FIELDS}


# ---- the one-off use --------------------------------------------------------------------------- #


def test_the_one_off_use_is_in_the_export_and_is_not_forgotten_by_time(gateway):
    alice = _a_customer(gateway, "alice")
    allowance.write_account_ceilings(_root(gateway), alice.account_id, {"account_lifetime_runs": 10})
    _use(_root(gateway), {
        allowance.lifetime_key(alice.account_id): _runs("a-life", T - 30 * DAY, T - 3 * DAY, T - 60),
        alice.account_id: _runs("a-win", T - 2 * DAY, T - 60)})
    out = retention.export_account(gateway, alice.account_id, now=T)
    assert out["one_off_allowance"] == {"runs_used": 3, "limit": 10, "refills": False}
    assert out["what_has_been_used_in_the_current_window"] == {"runs": 1, "seconds": 2.0}


def test_no_one_off_limit_is_said_as_none(gateway):
    alice = _a_customer(gateway, "alice")
    out = retention.export_account(gateway, alice.account_id, now=T)
    assert out["one_off_allowance"] == {"runs_used": 0, "limit": None, "refills": False}


# ---- one account ------------------------------------------------------------------------------- #


def test_nothing_of_another_account_reaches_the_export(gateway):
    alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
    allowance.write_account_ceilings(_root(gateway), alice.account_id, {"account_lifetime_runs": 3})
    allowance.write_account_ceilings(_root(gateway), bob.account_id,
                                     {"account_lifetime_runs": 4242, "account_runs_per_window": 4141})
    _use(_root(gateway), {
        allowance.lifetime_key(alice.account_id): _runs("a-life", T - 60),
        allowance.lifetime_key(bob.account_id): _runs("bob-secret-run", T - 60, T - 50, T - 40),
        bob.account_id: _runs("bob-window-run", T - 60)})
    out = retention.export_account(gateway, alice.account_id, now=T)
    text = json.dumps(out)
    for foreign in (bob.account_id, "4242", "4141", "bob-secret-run", "bob-window-run"):
        assert foreign not in text, "another account's data reached the export: " + foreign
    assert out["one_off_allowance"]["runs_used"] == 1
    assert out["limits_that_apply"]["own"] == {"account_lifetime_runs": 3}


# ---- which account ----------------------------------------------------------------------------- #


def test_an_unknown_account_is_refused_not_exported_empty(gateway):
    _a_customer(gateway, "alice")
    with pytest.raises(_accounts.NoSuchAccount):
        retention.export_account(gateway, "acct-" + "9" * 16, now=T)


@pytest.mark.parametrize("bad", ["", "../acct", "ACCT-UPPER", "acct-" + "0" * 200, "lifetime:acct"])
def test_an_ill_formed_account_id_is_refused(gateway, bad):
    with pytest.raises(_accounts.NoSuchAccount):
        retention.export_account(gateway, bad, now=T)


@pytest.mark.parametrize("bad", ["acct-" + "9" * 16, "../acct"])
def test_the_command_refuses_and_writes_nothing_for_an_account_that_is_not_here(gateway, tmp_path, bad):
    from agentnode_sdk.cli.main import main

    where = tmp_path / "out.json"
    code = main(["gateway", "export", "--dir", str(_root(gateway)), "--account", bad,
                 "--to", str(where)])
    assert code != 0 and not where.exists()
    assert retention.exports_of(_root(gateway)) == []


# ---- absent and damaged files ------------------------------------------------------------------ #


def test_absent_files_are_honest_zeros(gateway):
    alice = _a_customer(gateway, "alice")
    for name in (allowance.ALLOWANCE_NAME, allowance.ACCOUNT_CEILINGS_NAME, allowance.USE_NAME):
        assert not (_root(gateway) / name).exists()
    out = retention.export_account(gateway, alice.account_id, now=T)
    assert out["limits_that_apply"]["source"] == "gateway-wide"
    assert out["one_off_allowance"]["runs_used"] == 0
    assert out["what_has_been_used_in_the_current_window"] == {"runs": 0, "seconds": 0.0}
    assert out["files_that_could_not_be_read"] == []


@pytest.mark.parametrize("name", [allowance.ACCOUNT_CEILINGS_NAME, allowance.ALLOWANCE_NAME])
def test_damaged_limits_are_said_to_be_unreadable_and_never_become_a_number(gateway, name):
    alice = _a_customer(gateway, "alice")
    (_root(gateway) / name).write_text("{ not json", encoding="utf-8")
    _use(_root(gateway), {allowance.lifetime_key(alice.account_id): _runs("a", T - 60)})
    out = retention.export_account(gateway, alice.account_id, now=T)
    assert out["limits_that_apply"] == {"state": "could not be read", "file": name}
    assert name in out["files_that_could_not_be_read"]
    assert out["one_off_allowance"]["limit"] == "could not be read"
    assert out["one_off_allowance"]["runs_used"] == 1, "the rest of the export is still there"
    assert out["account"]["account_id"] == alice.account_id


def test_a_damaged_use_record_is_said_to_be_unreadable_and_never_becomes_a_zero(gateway):
    alice = _a_customer(gateway, "alice")
    allowance.write_account_ceilings(_root(gateway), alice.account_id, {"account_lifetime_runs": 5})
    (_root(gateway) / allowance.USE_NAME).write_text("[1, 2", encoding="utf-8")
    out = retention.export_account(gateway, alice.account_id, now=T)
    assert out["what_has_been_used_in_the_current_window"] == {
        "state": "could not be read", "file": allowance.USE_NAME}
    assert out["one_off_allowance"]["runs_used"] == "could not be read"
    assert out["one_off_allowance"]["limit"] == 5
    assert allowance.USE_NAME in out["files_that_could_not_be_read"]


# ---- one moment -------------------------------------------------------------------------------- #


def test_the_use_record_is_read_once_and_window_and_lifetime_come_from_that_read(gateway, monkeypatch):
    alice = _a_customer(gateway, "alice")
    first = {allowance.lifetime_key(alice.account_id): _runs("one", T - 60),
             alice.account_id: _runs("one-w", T - 60)}
    later = {allowance.lifetime_key(alice.account_id): _runs("two", T - 60, T - 50, T - 40, T - 30),
             alice.account_id: _runs("two-w", T - 60, T - 50, T - 40)}
    reads = []

    def load(self):
        reads.append(1)
        return json.loads(json.dumps(first if len(reads) == 1 else later))

    monkeypatch.setattr(allowance.Use, "_load", load)
    out = retention.export_account(gateway, alice.account_id, now=T)
    assert len(reads) == 1, "the use record was read %d times for one export" % len(reads)
    assert out["one_off_allowance"]["runs_used"] == 1
    assert out["what_has_been_used_in_the_current_window"]["runs"] == 1


def test_the_limits_are_read_once(gateway, monkeypatch):
    alice = _a_customer(gateway, "alice")
    calls = []
    real = allowance.read_account_ceilings

    def counted(root):
        calls.append(1)
        return real(root)

    monkeypatch.setattr(allowance, "read_account_ceilings", counted)
    retention.export_account(gateway, alice.account_id, now=T)
    assert len(calls) == 1


def test_the_export_names_the_moment_its_window_was_judged_at(gateway):
    alice = _a_customer(gateway, "alice")
    _use(_root(gateway), {alice.account_id: _runs("w", T - DAY - 1, T - DAY + 1)})
    out = retention.export_account(gateway, alice.account_id, now=T)
    assert out["as_of"] == T
    assert out["what_has_been_used_in_the_current_window"]["runs"] == 1


def test_the_command_writes_the_limits_and_the_one_off_use(gateway, tmp_path):
    from agentnode_sdk.cli.main import main

    alice = _a_customer(gateway, "alice")
    allowance.write_account_ceilings(_root(gateway), alice.account_id, {"account_lifetime_runs": 3})
    where = tmp_path / "out.json"
    assert main(["gateway", "export", "--dir", str(_root(gateway)), "--account", alice.account_id,
                 "--to", str(where)]) == 0
    body = json.loads(where.read_text(encoding="utf-8"))
    assert body["limits_that_apply"]["own"] == {"account_lifetime_runs": 3}
    assert body["one_off_allowance"]["limit"] == 3
