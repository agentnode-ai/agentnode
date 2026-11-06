"""Task #26: deleting a customer clears what #11 and #12 keep about them -- and only them.

`retention.delete_account` removed sessions, devices, counters, audit lines, the ledger, the
metering and the export record, but not the customer's own ceilings in `account-ceilings.json`
(task #11): a deleted customer's id stayed in a file that survives everything. The counters in
`use.json` -- the window ones and the one-off counter from #12 -- were removed, but a damaged
`use.json` made the deletion RAISE halfway, after the devices were already revoked, instead of
naming what could not be done like every other step.

Held here, for each of those files: exactly the named account goes and the other is untouched,
byte for byte where it can be; deleting twice, an unknown id and a malformed one change nothing; a
damaged file or a failing write is NAMED in `problems` with `complete` False and leaves no
half-written file; and a later deletion finishes the job. Local test accounts only, through the
regular lifecycle path. Ids are always named, never picked by position.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentnode_sdk.gateway import accounts as _accounts
from agentnode_sdk.gateway import allowance as A
from agentnode_sdk.gateway import retention
from agentnode_sdk.gateway.allowance import read_account_ceilings, write_account_ceilings
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by

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


@pytest.fixture()
def two(gateway):
    """Two local test accounts with DIFFERENT ceilings of their own and a one-off counter each."""
    alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
    write_account_ceilings(gateway.state.root, alice.account_id,
                           {"account_runs_per_window": 3, "account_lifetime_runs": 5})
    write_account_ceilings(gateway.state.root, bob.account_id,
                           {"account_runs_per_window": 7, "account_lifetime_runs": 9})
    _a_run_by(gateway, alice)
    _a_run_by(gateway, bob)
    return alice, bob


def _ceilings_path(gateway) -> Path:
    return gateway.state.root / A.ACCOUNT_CEILINGS_NAME


def _lifetime(gateway, who) -> int:
    return gateway.use.so_far(A.lifetime_key(who.account_id))[0]


# ---- exactly the named account --------------------------------------------------------------- #


def test_deleting_one_account_removes_only_its_own_ceilings(gateway, two):
    alice, bob = two
    went = retention.delete_account(gateway, alice.account_id)
    assert went["complete"] is True, went["problems"]
    assert read_account_ceilings(gateway.state.root) == {
        bob.account_id: {"account_runs_per_window": 7, "account_lifetime_runs": 9}}
    assert went["account_ceilings"] == 1
    assert _lifetime(gateway, alice) == 0 and _lifetime(gateway, bob) == 1
    # and the window counter of the account (#11's account scope), only the named one
    assert gateway.use.so_far(alice.account_id)[0] == 0
    assert gateway.use.so_far(bob.account_id)[0] == 1


def test_the_operator_command_does_the_same(gateway, two):
    alice, bob = two
    env = dict(os.environ, PYTHONPATH=str(SDK))
    done = subprocess.run([sys.executable, "-m", "agentnode_sdk.cli", "gateway", "delete",
                           "--dir", str(gateway.state.root), "--account", alice.account_id,
                           "--yes"], capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert set(read_account_ceilings(gateway.state.root)) == {bob.account_id}


# ---- repetition and ids that do not belong to anyone ------------------------------------------- #


def test_deleting_twice_changes_nothing_more(gateway, two):
    alice, bob = two
    retention.delete_account(gateway, alice.account_id)
    before = _ceilings_path(gateway).read_bytes()
    again = retention.delete_account(gateway, alice.account_id)
    assert again["complete"] is True and again["account_ceilings"] == 0
    assert _ceilings_path(gateway).read_bytes() == before


def test_an_unknown_but_well_formed_id_touches_nothing(gateway, two):
    _alice, bob = two
    before = _ceilings_path(gateway).read_bytes()
    nobody = _accounts.new_account_id()
    went = retention.delete_account(gateway, nobody)
    assert went["account_ceilings"] == 0
    assert _ceilings_path(gateway).read_bytes() == before
    assert _lifetime(gateway, bob) == 1


def test_a_malformed_id_is_refused_and_touches_nothing(gateway, two):
    before = _ceilings_path(gateway).read_bytes()
    with pytest.raises(_accounts.NoSuchAccount):
        retention.delete_account(gateway, "acct-nobody")
    assert _ceilings_path(gateway).read_bytes() == before


# ---- damage and failure are named, never half-done ---------------------------------------------- #


def test_a_damaged_ceilings_file_is_named_and_left_as_it_was(gateway, two):
    alice, bob = two
    _ceilings_path(gateway).write_text("{not json", encoding="utf-8")
    went = retention.delete_account(gateway, alice.account_id)
    assert went["complete"] is False
    assert any("own ceilings" in p for p in went["problems"]), went["problems"]
    assert _ceilings_path(gateway).read_text(encoding="utf-8") == "{not json"
    assert _lifetime(gateway, alice) == 0 and _lifetime(gateway, bob) == 1   # the rest went on


def test_a_damaged_use_record_is_named_instead_of_breaking_off(gateway, two):
    alice, bob = two
    (gateway.state.root / A.USE_NAME).write_text("{not json", encoding="utf-8")
    went = retention.delete_account(gateway, alice.account_id)       # must not raise
    assert went["complete"] is False
    assert any("counters" in p for p in went["problems"]), went["problems"]
    assert set(read_account_ceilings(gateway.state.root)) == {bob.account_id}  # later steps ran
    assert went["account_record"] is not None


def test_a_write_that_fails_halfway_leaves_the_file_whole_and_a_retry_finishes(gateway, two,
                                                                             monkeypatch):
    alice, bob = two
    before = _ceilings_path(gateway).read_bytes()
    real = A._atomically

    def failing(path, text):
        if Path(path).name == A.ACCOUNT_CEILINGS_NAME:
            raise OSError("simulated: the disk refused the write")
        return real(path, text)

    monkeypatch.setattr(A, "_atomically", failing)
    went = retention.delete_account(gateway, alice.account_id)
    assert went["complete"] is False
    assert any("own ceilings" in p for p in went["problems"]), went["problems"]
    assert _ceilings_path(gateway).read_bytes() == before            # nothing half-written
    monkeypatch.setattr(A, "_atomically", real)
    again = retention.delete_account(gateway, alice.account_id)
    assert again["complete"] is True, again["problems"]
    assert read_account_ceilings(gateway.state.root) == {
        bob.account_id: {"account_runs_per_window": 7, "account_lifetime_runs": 9}}


def test_the_ceilings_file_is_never_left_empty_of_others_by_a_deletion(gateway, two):
    """The other customer's entry is the same object, value for value, after the deletion."""
    alice, bob = two
    theirs = json.loads(_ceilings_path(gateway).read_text(encoding="utf-8"))[bob.account_id]
    retention.delete_account(gateway, alice.account_id)
    assert json.loads(_ceilings_path(gateway).read_text(encoding="utf-8"))[bob.account_id] == theirs
