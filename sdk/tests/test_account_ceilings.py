"""Task #11: every gateway account can carry its own ceilings.

The plan (PLAN-2026-11.md, Sandbox Cloud) records "keine Limits je Konto". Account ceilings did
exist, but as ONE value for every customer (`account_runs_per_window` etc. in the allowance file):
an operator could not give one customer -- a trial, say -- a smaller allowance than another. These
tests hold the per-account ceiling to four things:

* two accounts with different ceilings are each held to their own, and one account's use neither
  spends nor blocks the other's;
* an account without ceilings of its own keeps the gateway-wide default, and a gateway with no
  per-account file behaves exactly as before;
* the check before the start AND the binding booking both apply it -- read through one function,
  so they cannot disagree -- and concurrent submissions cannot slip past it;
* the file is read strictly, like the allowance itself: an unknown field or a value that is not a
  non-negative whole number stops the gateway taking work rather than being ignored.

No concrete sizes are decided here (that is task #15); the numbers below are test values.
Real accounts, real dispatcher, stand-in container backend.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

import pytest

from agentnode_sdk.access import dispatch
from agentnode_sdk.gateway.allowance import (
    Allowance, OverTheCeiling, read_account_ceilings, write_account_ceilings, write_allowance)
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by, _their_second_machine


@pytest.fixture()
def gateway(tmp_path):
    """The same real gateway `test_two_accounts` uses: real state, stand-in container backend."""
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


def _refused(gateway, who):
    with pytest.raises(dispatch.Refused) as refused:
        _a_run_by(gateway, who)
    assert refused.value.refusal == "over_a_ceiling", refused.value
    return refused.value


# ---- two accounts, two ceilings --------------------------------------------------------------- #


def test_two_accounts_are_each_held_to_their_own_ceiling(gateway):
    alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 1})
    write_account_ceilings(gateway.state.root, bob.account_id, {"account_runs_per_window": 3})

    _a_run_by(gateway, alice)
    _refused(gateway, alice)
    for _ in range(3):
        _a_run_by(gateway, bob)
    _refused(gateway, bob)


def test_one_accounts_use_neither_spends_nor_blocks_anothers(gateway):
    alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
    for who in (alice, bob):
        write_account_ceilings(gateway.state.root, who.account_id, {"account_runs_per_window": 1})
    _a_run_by(gateway, alice)
    _refused(gateway, alice)
    _a_run_by(gateway, bob)                      # alice's exhausted allowance is not bob's
    because = _refused(gateway, bob).because
    assert alice.account_id not in because and alice.device_id not in because


def test_a_second_device_cannot_raise_its_accounts_own_ceiling(gateway):
    alice = _a_customer(gateway, "alice")
    second = _their_second_machine(gateway, alice)
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 1})
    _a_run_by(gateway, alice)
    _refused(gateway, second)


# ---- default and existing gateways ------------------------------------------------------------ #


def test_an_account_without_its_own_ceiling_keeps_the_default(gateway):
    alice, carol = _a_customer(gateway, "alice"), _a_customer(gateway, "carol")
    write_allowance(gateway.state.root, Allowance(account_runs_per_window=2))
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 1})
    _a_run_by(gateway, alice)
    _refused(gateway, alice)
    _a_run_by(gateway, carol)
    _a_run_by(gateway, carol)                    # the gateway-wide two, not alice's one
    _refused(gateway, carol)


def test_an_account_may_be_given_more_than_the_default(gateway):
    """The operator decides per customer; a wider ceiling for one account is theirs to set."""
    alice = _a_customer(gateway, "alice")
    write_allowance(gateway.state.root, Allowance(account_runs_per_window=1))
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 2})
    _a_run_by(gateway, alice)
    _a_run_by(gateway, alice)
    _refused(gateway, alice)


def test_a_gateway_without_the_file_behaves_as_before(gateway):
    alice = _a_customer(gateway, "alice")
    write_allowance(gateway.state.root, Allowance(account_runs_per_window=1))
    assert read_account_ceilings(gateway.state.root) == {}
    assert gateway.allowance_for(alice.account_id) == gateway.allowance()
    _a_run_by(gateway, alice)
    _refused(gateway, alice)


def test_the_device_ceiling_still_applies_beside_the_accounts(gateway):
    alice = _a_customer(gateway, "alice")
    write_allowance(gateway.state.root, Allowance(runs_per_window=1))
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 5})
    _a_run_by(gateway, alice)
    _refused(gateway, alice)                     # the tighter one decides


# ---- one source for the check and the booking ------------------------------------------------- #


def test_the_check_before_the_start_applies_the_accounts_ceiling(gateway):
    alice, carol = _a_customer(gateway, "alice"), _a_customer(gateway, "carol")
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 1})
    _a_run_by(gateway, alice)
    _a_run_by(gateway, carol)
    with pytest.raises(OverTheCeiling):
        gateway.within_its_allowance(alice.client_id, 30, account_id=alice.account_id)
    gateway.within_its_allowance(carol.client_id, 30, account_id=carol.account_id)


def test_the_binding_booking_applies_it_even_if_the_check_let_it_through(gateway, monkeypatch):
    """With the early check switched off, the booking alone must still refuse."""
    alice = _a_customer(gateway, "alice")
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 1})
    _a_run_by(gateway, alice)
    granted = gateway.allowance_for(alice.account_id).digest()
    monkeypatch.setattr(gateway, "within_its_allowance", lambda *a, **k: granted)
    _refused(gateway, alice)


def test_check_and_booking_read_the_ceilings_through_one_function(gateway, monkeypatch):
    alice = _a_customer(gateway, "alice")
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 5})
    asked = []
    real = gateway.allowance_for

    def spy(account_id=""):
        asked.append(account_id)
        return real(account_id)

    monkeypatch.setattr(gateway, "allowance_for", spy)
    run_id = _a_run_by(gateway, alice)
    assert asked.count(alice.account_id) >= 2, asked      # the check and the booking, both
    record = gateway.runs[run_id]
    assert record.admitted_under_values["account_runs_per_window"] == 5
    assert record.admitted_under == real(alice.account_id).digest()


def test_concurrent_submissions_cannot_slip_past_an_accounts_ceiling(gateway):
    for round_ in range(3):
        alice = _a_customer(gateway, "alice-%d" % round_)
        devices = [alice] + [_their_second_machine(gateway, alice) for _ in range(3)]
        write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 1})
        start, results = threading.Barrier(len(devices)), []

        def submit(who):
            start.wait()
            try:
                _a_run_by(gateway, who)
                results.append("admitted")
            except dispatch.Refused as refused:
                results.append(refused.refusal)

        threads = [threading.Thread(target=submit, args=(d,)) for d in devices]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert sorted(results) == ["admitted"] + ["over_a_ceiling"] * 3, results


def test_what_a_customer_is_shown_is_what_they_are_held_to(gateway):
    alice = _a_customer(gateway, "alice")
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 1})
    usage = dispatch.dispatch("usage", {}, alice, service=gateway)
    assert usage["ceilings"]["account_runs_per_window"] == 1


# ---- read strictly --------------------------------------------------------------------------- #


@pytest.mark.parametrize("body", [
    '{"acct-x": {"account_runs_per_hour": 1}}',      # a ceiling this build does not know
    '{"acct-x": {"runs_per_window": 1}}',            # a device ceiling is not an account's
    '{"acct-x": {"account_runs_per_window": -1}}',
    '{"acct-x": {"account_runs_per_window": "1"}}',
    '{"acct-x": {"account_runs_per_window": 1.5}}',
    '["not", "an", "object"]',
    '{"acct-x": 3}',
    "{not json",
])
def test_an_unreadable_file_stops_the_gateway_taking_work(gateway, body):
    alice = _a_customer(gateway, "alice")
    (gateway.state.root / "account-ceilings.json").write_text(body, encoding="utf-8")
    with pytest.raises(dispatch.Refused):
        _a_run_by(gateway, alice)
    assert not gateway.runs, "a run was admitted under ceilings nobody could read"


def test_ceilings_cannot_be_written_for_an_unknown_field(tmp_path):
    with pytest.raises(ValueError):
        write_account_ceilings(tmp_path, "acct-x", {"account_runs_per_hour": 1})
    with pytest.raises(ValueError):
        write_account_ceilings(tmp_path, "acct-x", {"account_runs_per_window": -1})


def test_removing_an_accounts_ceilings_restores_the_default(gateway):
    alice = _a_customer(gateway, "alice")
    write_allowance(gateway.state.root, Allowance(account_runs_per_window=2))
    write_account_ceilings(gateway.state.root, alice.account_id, {"account_runs_per_window": 1})
    write_account_ceilings(gateway.state.root, alice.account_id, None)
    assert read_account_ceilings(gateway.state.root) == {}
    _a_run_by(gateway, alice)
    _a_run_by(gateway, alice)


# ---- the operator's command ------------------------------------------------------------------ #


def _cli(root, *args):
    env = dict(os.environ, PYTHONPATH=str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    return subprocess.run([sys.executable, "-m", "agentnode_sdk.cli", "gateway", "limits",
                           "--dir", str(root), *args], capture_output=True, text=True, env=env,
                          timeout=120)


def test_the_operator_sets_and_sees_one_accounts_ceiling(gateway):
    alice = _a_customer(gateway, "alice")
    root = gateway.state.root
    set_ = _cli(root, "--account", alice.account_id, "--account-runs-per-window", "1")
    assert set_.returncode == 0, set_.stdout + set_.stderr
    assert read_account_ceilings(root) == {alice.account_id: {"account_runs_per_window": 1}}
    shown = _cli(root)
    assert alice.account_id in shown.stdout and "account_runs_per_window" in shown.stdout
    reset = _cli(root, "--account", alice.account_id, "--reset")
    assert reset.returncode == 0 and read_account_ceilings(root) == {}


def test_the_command_refuses_what_it_cannot_mean(gateway):
    root = gateway.state.root
    alice = _a_customer(gateway, "alice")
    from agentnode_sdk.gateway.accounts import new_account_id

    # A WELL-FORMED id nobody holds: refused with a sentence, not a traceback.
    nobody = new_account_id()
    unknown = _cli(root, "--account", nobody, "--account-runs-per-window", "1")
    assert unknown.returncode == 2, unknown.stdout + unknown.stderr
    assert "There is no customer" in unknown.stderr and "Traceback" not in unknown.stderr
    assert read_account_ceilings(root) == {}
    malformed = _cli(root, "--account", "acct-nobody", "--account-runs-per-window", "1")
    assert malformed.returncode == 2 and "Traceback" not in malformed.stderr, malformed.stderr
    assert read_account_ceilings(root) == {}
    device = _cli(root, "--account", alice.account_id, "--runs-per-window", "1")
    assert device.returncode != 0 and read_account_ceilings(root) == {}
