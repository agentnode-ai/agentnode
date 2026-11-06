"""Task #12: a one-off allowance per account that never refills.

PLAN-2026-11.md, Sandbox Cloud: "Freikontingent nicht einmalig". Every ceiling so far counts in a
window and refills when the window moves on, so a trial never ends. This adds a LIFETIME ceiling on
the account scope from #11 (`account_lifetime_runs`, 0 = none):

* it is counted in the same transaction as every other ceiling, in the same record, and that
  record never forgets it -- not when the window moves on, not across a restart;
* each account is held to its own, one account's use neither spends nor blocks another's, and
  concurrent submissions at the boundary admit exactly as many as are left;
* the check before the start, the booking, the run's record and what the customer is shown all
  read it through `allowance_for` and one counter;
* when it is used up the refusal is structured (`over_a_ceiling`, which=`account_lifetime_runs`)
  and names the way on: a sandbox of your own -- not "wait until the window clears";
* an operator can reset it for exactly one named account; nothing else resets it.

The size is configurable per account. `--trial` uses a placeholder that is marked PROVISIONAL in
the code: the real size is a decision of task #15. Real accounts, real dispatcher, stand-in
container backend.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from agentnode_sdk.access import dispatch
from agentnode_sdk.gateway import allowance as A
from agentnode_sdk.gateway.allowance import Allowance, read_account_ceilings, write_account_ceilings, write_allowance
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by, _their_second_machine

SDK = Path(__file__).resolve().parents[1]


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


def _one_off(gateway, who, runs):
    write_account_ceilings(gateway.state.root, who.account_id, {"account_lifetime_runs": runs})


def _refused(gateway, who):
    with pytest.raises(dispatch.Refused) as refused:
        _a_run_by(gateway, who)
    assert refused.value.refusal == "over_a_ceiling", refused.value
    return refused.value


# ---- the ceiling itself ------------------------------------------------------------------------ #


def test_an_account_may_start_exactly_its_one_off_allowance(gateway):
    alice = _a_customer(gateway, "alice")
    _one_off(gateway, alice, 2)
    _a_run_by(gateway, alice)
    _a_run_by(gateway, alice)
    _refused(gateway, alice)


def test_the_refusal_is_structured_and_names_your_own_sandbox(gateway):
    alice = _a_customer(gateway, "alice")
    _one_off(gateway, alice, 1)
    _a_run_by(gateway, alice)
    refused = _refused(gateway, alice)
    assert "account_lifetime_runs" in refused.because
    assert "does not refill" in refused.because
    said = refused.because + " " + refused.what_to_do
    assert "your own sandbox" in said and "agentnode gateway" in said, said
    assert "window clears" not in refused.what_to_do, refused.what_to_do
    assert refused.what_to_do == A.ONE_OFF_WHAT_TO_DO   # one sentence, wherever it is said


def test_no_lifetime_ceiling_is_todays_behaviour(gateway):
    alice = _a_customer(gateway, "alice")
    assert Allowance().account_lifetime_runs == 0
    for _ in range(4):
        _a_run_by(gateway, alice)


def test_the_gateway_wide_default_applies_to_accounts_without_their_own(gateway):
    alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
    write_allowance(gateway.state.root, Allowance(account_lifetime_runs=1))
    _one_off(gateway, bob, 3)
    _a_run_by(gateway, alice)
    _refused(gateway, alice)
    for _ in range(3):
        _a_run_by(gateway, bob)
    _refused(gateway, bob)


# ---- separation ---------------------------------------------------------------------------------- #


def test_one_accounts_use_neither_spends_nor_blocks_anothers(gateway):
    alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
    for who in (alice, bob):
        _one_off(gateway, who, 1)
    _a_run_by(gateway, alice)
    _refused(gateway, alice)
    _a_run_by(gateway, bob)
    because = _refused(gateway, bob).because
    assert alice.account_id not in because


def test_a_second_device_shares_its_accounts_one_off_allowance(gateway):
    alice = _a_customer(gateway, "alice")
    second = _their_second_machine(gateway, alice)
    _one_off(gateway, alice, 1)
    _a_run_by(gateway, alice)
    _refused(gateway, second)


# ---- it never refills ------------------------------------------------------------------------------ #


def test_the_window_moving_on_does_not_refill_it(gateway):
    """Age the RECORD by 400 days rather than the clock: moving the clock would also age the
    gateway's measurement and refuse for that reason instead."""
    import json

    alice = _a_customer(gateway, "alice")
    _one_off(gateway, alice, 1)
    write_allowance(gateway.state.root, Allowance(account_runs_per_window=1))
    run_id = _a_run_by(gateway, alice)
    # Let the run settle first: its ending writes its figure into the record, and aging the
    # record before that would be undone by it.
    import time as _time

    from agentnode_sdk.gateway.protocol import is_terminal

    deadline = _time.monotonic() + 30
    while not is_terminal(gateway.runs[run_id].state):
        assert _time.monotonic() < deadline, "the run did not finish"
        _time.sleep(0.05)
    _time.sleep(0.2)
    path = gateway.state.root / A.USE_NAME
    body = json.loads(path.read_text(encoding="utf-8"))
    for entries in body.values():
        for entry in entries:
            entry["at"] = float(entry["at"]) - 400 * 24 * 3600
    path.write_text(json.dumps(body), encoding="utf-8")
    assert gateway.use.so_far(alice.account_id)[0] == 0      # the window HAS refilled
    refused = _refused(gateway, alice)                        # the one-off allowance has not
    assert "account_lifetime_runs" in refused.because


def test_a_restart_does_not_refill_it(gateway, tmp_path):
    alice = _a_customer(gateway, "alice")
    _one_off(gateway, alice, 1)
    _a_run_by(gateway, alice)
    again = GatewayService(gateway.state, backend=StandInBackend())
    _store_measurement(again)
    _refused(again, alice)


# ---- one source ----------------------------------------------------------------------------------- #


def test_the_check_before_the_start_applies_it(gateway):
    alice = _a_customer(gateway, "alice")
    _one_off(gateway, alice, 1)
    _a_run_by(gateway, alice)
    with pytest.raises(A.OverTheCeiling) as over:
        gateway.within_its_allowance(alice.client_id, 30, account_id=alice.account_id)
    assert over.value.which == "account_lifetime_runs"


def test_the_booking_applies_it_even_if_the_check_let_it_through(gateway, monkeypatch):
    alice = _a_customer(gateway, "alice")
    _one_off(gateway, alice, 1)
    _a_run_by(gateway, alice)
    granted = gateway.allowance_for(alice.account_id).digest()
    monkeypatch.setattr(gateway, "within_its_allowance", lambda *a, **k: granted)
    _refused(gateway, alice)


def test_the_run_records_it_and_the_customer_is_shown_it(gateway):
    alice = _a_customer(gateway, "alice")
    _one_off(gateway, alice, 3)
    run_id = _a_run_by(gateway, alice)
    assert gateway.runs[run_id].admitted_under_values["account_lifetime_runs"] == 3
    usage = dispatch.dispatch("usage", {}, alice, service=gateway)
    assert usage["ceilings"]["account_lifetime_runs"] == 3
    assert usage["account_lifetime_runs_used"] == 1


# ---- concurrency and repetition ------------------------------------------------------------------- #


def test_concurrent_submissions_at_the_boundary_admit_only_what_is_left(gateway):
    for round_ in range(3):
        alice = _a_customer(gateway, "alice-%d" % round_)
        devices = [alice] + [_their_second_machine(gateway, alice) for _ in range(4)]
        _one_off(gateway, alice, 2)
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
        assert sorted(results) == ["admitted"] * 2 + ["over_a_ceiling"] * 3, results


def test_a_repeated_submission_of_one_run_is_counted_once(gateway):
    import base64
    import hashlib

    alice = _a_customer(gateway, "alice")
    _one_off(gateway, alice, 2)
    code = b"print(1)\n"
    shown = dispatch.dispatch("prepare", {"artifact_sha256": hashlib.sha256(code).hexdigest(),
                                          "artifact_bytes": len(code), "wall_clock_s": 30},
                              alice, service=gateway)
    params = {"run_id": "r" * 32, "artifact": base64.b64encode(code).decode("ascii"),
              "wall_clock_s": 30, "accepted_disclosure": shown["accepted_disclosure"]}
    dispatch.dispatch("submit", params, alice, service=gateway)
    try:
        dispatch.dispatch("submit", dict(params), alice, service=gateway)
    except dispatch.Refused:
        pass                                    # refusing the repeat is fine; counting it is not
    used, _ = gateway.use.so_far(A.lifetime_key(alice.account_id))
    assert used == 1
    _a_run_by(gateway, alice)                   # the second of two is still there


def test_the_store_charges_one_run_once_on_the_lifetime_counter(tmp_path):
    """Directly at the store: the gateway refuses a resubmitted run before booking, so the test
    above cannot reach this -- the counter-check proved it (T-0103, C4)."""
    use = A.Use(tmp_path / "use.json")
    key = A.lifetime_key("acct-0000000000000002")
    for _ in range(3):
        use.claim_every([(key, lambda *_a: None)], "run-1")
    assert use.so_far(key)[0] == 1
    use.claim_every([(key, lambda *_a: None)], "run-2")
    assert use.so_far(key)[0] == 2


def test_every_door_gives_the_same_way_on(gateway):
    """Both translators -- the dispatcher's and the gateway's own -- give the ceiling's sentence.
    The submit path reaches only one of them, so each is asked directly (T-0103, C5)."""
    from agentnode_sdk.gateway.server import name_the_refusal

    over = A.OverTheCeiling("account_lifetime_runs", "used up", what_to_do=A.ONE_OFF_WHAT_TO_DO)
    refused = dispatch._translate(over)
    assert refused.refusal == "over_a_ceiling" and refused.what_to_do == A.ONE_OFF_WHAT_TO_DO
    assert name_the_refusal(over) == ("over_a_ceiling", A.ONE_OFF_WHAT_TO_DO)
    window = A.OverTheCeiling("account_runs_per_window", "full")
    assert "window clears" in dispatch._translate(window).what_to_do
    assert "window clears" in name_the_refusal(window)[1]


def test_deleting_the_customer_deletes_their_lifetime_counter(gateway):
    """A record that is never forgotten by time still goes when the customer does."""
    from agentnode_sdk.gateway import retention

    alice = _a_customer(gateway, "alice")
    _one_off(gateway, alice, 2)
    _a_run_by(gateway, alice)
    assert gateway.use.so_far(A.lifetime_key(alice.account_id))[0] == 1
    retention.delete_account(gateway.state, alice.account_id)
    assert gateway.use.so_far(A.lifetime_key(alice.account_id))[0] == 0


def test_the_lifetime_record_is_never_forgotten_by_the_use_store(tmp_path):
    use = A.Use(tmp_path / "use.json", window=10.0)
    use.note_every([A.lifetime_key("acct-0000000000000001"), "dev-x"], "run-1", now=1000.0)
    use.note("dev-y", "run-2", now=1_000_000.0)        # rewrites the file far outside the window
    assert use.so_far(A.lifetime_key("acct-0000000000000001"), now=1_000_000.0)[0] == 1
    assert use.so_far("dev-x", now=1_000_000.0)[0] == 0


# ---- the operator ----------------------------------------------------------------------------------- #


def _cli(root, *args):
    env = dict(os.environ, PYTHONPATH=str(SDK))
    return subprocess.run([sys.executable, "-m", "agentnode_sdk.cli", "gateway", "limits",
                           "--dir", str(root), *args], capture_output=True, text=True, env=env,
                          timeout=120)


def test_a_reset_gives_back_exactly_the_named_accounts_allowance(gateway):
    alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
    for who in (alice, bob):
        _one_off(gateway, who, 1)
        _a_run_by(gateway, who)
    reset = _cli(gateway.state.root, "--account", alice.account_id, "--reset-lifetime")
    assert reset.returncode == 0, reset.stdout + reset.stderr
    _a_run_by(gateway, alice)                   # alice has hers back
    _refused(gateway, bob)                      # bob does not


def test_trial_sets_the_provisional_size_and_says_it_is_provisional(gateway):
    alice = _a_customer(gateway, "alice")
    done = _cli(gateway.state.root, "--account", alice.account_id, "--trial")
    assert done.returncode == 0, done.stdout + done.stderr
    assert read_account_ceilings(gateway.state.root) == {
        alice.account_id: {"account_lifetime_runs": A.PROVISIONAL_TRIAL_LIFETIME_RUNS}}
    assert "provisional" in done.stdout.lower()
    assert A.PROVISIONAL_TRIAL_LIFETIME_RUNS > 0


def test_the_size_is_configurable_per_account(gateway):
    alice = _a_customer(gateway, "alice")
    done = _cli(gateway.state.root, "--account", alice.account_id, "--account-lifetime-runs", "7")
    assert done.returncode == 0, done.stdout + done.stderr
    assert read_account_ceilings(gateway.state.root)[alice.account_id] == {
        "account_lifetime_runs": 7}
