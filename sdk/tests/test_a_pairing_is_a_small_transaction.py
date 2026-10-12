"""O6 follow-up T: a pairing that fails half way loses nothing for good.

Redeeming an invitation used to work like this: it renamed the invitation away, read it, deleted it,
created the customer's account and then wrote the credential. A failure at the credential write left the
invitation spent and, for a new customer, an account that holds no device (bundle `o6-followups`, E0020).

A redemption is now a small transaction with a journal (DECISION-T, from consultations Q0003-Q0005). The
steps are: claim -> verified -> account -> token -> journal removed -> return.

A failure before the removal rolls the attempt back:
* it revokes any token the attempt committed;
* it forgets the account the attempt made;
* it keeps the invitation (`retryable`).

A crash at any step is resolved the same way when the directory is next taken over. No journal holds a
credential. Nothing replays one: the invariant is ONE working credential at a time.
"""
from __future__ import annotations

import errno
import threading
import time

import pytest

from agentnode_sdk.gateway.identity import GatewayState, PairingError, TokensUnreadable


class _Stop(BaseException):
    """A process stopping at this point. BaseException, so no `except Exception` in the product catches it."""


@pytest.fixture()
def root(tmp_path):
    return str(tmp_path / "state")


def _state(root):
    return GatewayState(root, version="test")


def _devices(state):
    return sorted(str(r.get("client_name")) for r in state._read_tokens().values())


def _orphans(state):
    held = {str(r.get("account_id") or "") for r in state._read_tokens().values()}
    return sorted(a.account_id for a in state.accounts.all() if a.account_id not in held)


def _journals(state):
    return sorted(p.name for p in state.root.iterdir() if p.name.startswith(".pairing-redemption-"))


def _redeem(state, code, name="laptop"):
    try:
        return state.redeem_pairing(code, client_name=name), None
    except BaseException as exc:                                # noqa: BLE001 - _Stop included
        return None, exc


def _token_write_fails(monkeypatch):
    def refuse(self, tokens):
        raise OSError(errno.ENOSPC, "No space left on device (injected)")

    monkeypatch.setattr(GatewayState, "_write_tokens", refuse)


def _take_over(root):
    """A new process taking the directory over, which is where recovery runs."""
    again = _state(root)
    again.recover_pairings()
    return again


# ---------------------------------------------------------------- the measured cases


@pytest.mark.parametrize("which", ["write", "read"])
def test_1_2_a_new_customers_failed_pairing_can_be_retried_and_leaves_no_empty_account(root, monkeypatch,
                                                                                         which):
    state = _state(root)
    try:
        code = state.start_pairing()
        if which == "write":
            _token_write_fails(monkeypatch)
        else:
            def refuse(self):
                raise TokensUnreadable("injected")
            monkeypatch.setattr(GatewayState, "_read_tokens", refuse)
        issued, raised = _redeem(state, code)
        monkeypatch.undo()
        assert issued is None and isinstance(raised, TokensUnreadable), raised
        assert getattr(raised, "invitation_kept", None) is True, (
            "the invitation was kept, and the redeemer was not told so")
        assert _orphans(state) == [], "a failed pairing left an account holding no device"
        again, raised_again = _redeem(state, code)
        assert again and raised_again is None, (
            "the invitation was spent by a pairing that failed: %r" % (raised_again,))
        assert _devices(state) == ["laptop"] and _orphans(state) == [] and _journals(state) == []
    finally:
        state.close()


def test_3_an_invitation_to_an_existing_account_survives_a_failed_pairing(root, monkeypatch):
    state = _state(root)
    try:
        first = state._issue_token(client_name="first")
        account = state.account_id_for(first)
        code = state.start_pairing(for_account=account)
        _token_write_fails(monkeypatch)
        issued, raised = _redeem(state, code, "second")
        monkeypatch.undo()
        assert issued is None and getattr(raised, "invitation_kept", None) is True, raised
        again, _ = _redeem(state, code, "second")
        assert again, "the invitation to an existing account was spent by a pairing that failed"
        assert state.account_id_for(again) == account
        assert _devices(state) == ["first", "second"]
    finally:
        state.close()


# ---------------------------------------------------------------- a crash at each durable step


def _crash_after(monkeypatch, step):
    """Stop the process right AFTER `step` has done its durable write."""
    from agentnode_sdk.gateway import accounts as accounts_module

    def after(owner, name):
        real = getattr(owner, name)

        def stopped(self, *a, **k):
            got = real(self, *a, **k)
            if name != "_write_journal" or (len(a) > 1 and a[1].get("state") == "verified"):
                raise _Stop(step)
            return got

        monkeypatch.setattr(owner, name, stopped)

    {"claim": lambda: after(GatewayState, "_claim_into_journal"),
     "verified": lambda: after(GatewayState, "_write_journal"),
     "account": lambda: after(accounts_module.Accounts, "create"),
     "token": lambda: after(GatewayState, "_commit_pairing_token"),
     "removal": lambda: after(GatewayState, "_remove_journal")}[step]()


@pytest.mark.parametrize("step", ["claim", "verified", "account", "token", "removal"])
def test_4_a_crash_at_each_step_is_resolved_at_the_next_take_over(root, monkeypatch, step):
    state = _state(root)
    code = state.start_pairing()
    _crash_after(monkeypatch, step)
    issued, raised = _redeem(state, code)
    monkeypatch.undo()
    assert isinstance(raised, _Stop), "the crash at %r was not reached: %r" % (step, raised)
    committed = [r for r in state._read_tokens().values()]
    state.close()
    again = _take_over(root)
    try:
        if step == "claim":
            got, why = _redeem(again, code)
            assert got is None and isinstance(why, PairingError), (
                "a claim that never proved its code was kept after a crash: %r" % (got,))
            assert _devices(again) == [] and _orphans(again) == [] and _journals(again) == []
        elif step == "removal":
            # Past the commit-to-return boundary: the credential stands, the code is spent.
            assert len(committed) == 1 and _devices(again) == ["laptop"]
            got, why = _redeem(again, code)
            assert got is None and isinstance(why, PairingError), "a spent code was honoured again"
            assert _orphans(again) == [] and _journals(again) == []
        else:
            # Before the boundary: whatever the attempt committed is revoked, its account forgotten, and
            # the invitation can be redeemed again -- for a NEW credential, the only one that works.
            assert _devices(again) == [], (
                "after a crash at %r, take-over left a credential in force: %s" % (step, _devices(again)))
            assert _orphans(again) == [], "after a crash at %r an account holds no device" % step
            got, why = _redeem(again, code)
            assert got, "after a crash at %r and a take-over, the retry failed: %r" % (step, why)
            assert _devices(again) == ["laptop"], "two credentials from one invitation: %s" % _devices(again)
            assert again.token_record(got) is not None and _orphans(again) == [] and _journals(again) == []
    finally:
        again.close()


def test_5_the_service_runs_recovery_when_it_takes_the_directory_over(root, monkeypatch):
    from agentnode_sdk.gateway.server import GatewayService
    from tests.test_em3c_gateway import StandInBackend

    state = _state(root)
    code = state.start_pairing()
    _crash_after(monkeypatch, "token")
    _redeem(state, code)
    monkeypatch.undo()
    state.close()
    again = _state(root)
    service = GatewayService(again, backend=StandInBackend(), recover=True)
    try:
        assert _devices(again) == [], "the service took the directory over and left the half-done credential"
        got, why = _redeem(again, code)
        assert got, "a take-over by the service did not keep the invitation: %r" % (why,)
    finally:
        service.close()
        again.close()


# ---------------------------------------------------------------- what still protects pairing


def test_6_a_wrong_code_spends_the_kept_invitation(root, monkeypatch):
    state = _state(root)
    try:
        code = state.start_pairing()
        _token_write_fails(monkeypatch)
        _redeem(state, code)
        monkeypatch.undo()
        wrong = "ZZZZ-ZZZZ-ZZZZ" if code != "ZZZZ-ZZZZ-ZZZZ" else "YYYY-YYYY-YYYY"
        _got, why = _redeem(state, wrong)
        assert isinstance(why, PairingError)
        got, why = _redeem(state, code)
        assert got is None and isinstance(why, PairingError), (
            "a kept invitation survived a wrong guess, so it accepts more than one")
        assert _orphans(state) == [] and _devices(state) == [] and _journals(state) == []
    finally:
        state.close()


@pytest.mark.parametrize("how", ["a new invitation", "a withdrawal"])
def test_7_a_kept_invitation_is_replaced_like_any_other(root, monkeypatch, how):
    state = _state(root)
    try:
        code = state.start_pairing()
        _token_write_fails(monkeypatch)
        _redeem(state, code)
        monkeypatch.undo()
        if how == "a new invitation":
            newer = state.start_pairing()
            # The newer one is redeemed FIRST: once it is live, the old code is a wrong code for it and
            # would spend it, as T-P4 requires of any wrong code (CORRECTION-T-T1).
            fresh, _ = _redeem(state, newer, "other")
            assert fresh and _devices(state) == ["other"]
        else:
            assert state.withdraw_pairing() is True
        got, why = _redeem(state, code)
        assert got is None and isinstance(why, PairingError), "a replaced invitation still works"
        assert _orphans(state) == [] and _journals(state) == []
    finally:
        state.close()


def test_8_a_second_redeemer_during_a_redemption_gets_nothing(root, monkeypatch):
    state = _state(root)
    other = _state(root)
    try:
        code = state.start_pairing()
        inside, release = threading.Event(), threading.Event()
        real = GatewayState._commit_pairing_token

        def slow(self, *a, **k):
            if threading.current_thread().name == "first":
                inside.set()
                release.wait(10)
            return real(self, *a, **k)

        monkeypatch.setattr(GatewayState, "_commit_pairing_token", slow)
        box = {}
        t = threading.Thread(name="first", target=lambda: box.update(first=_redeem(state, code)))
        t.start()
        assert inside.wait(10)
        t2 = threading.Thread(name="second", target=lambda: box.update(second=_redeem(other, code, "intruder")))
        t2.start()
        time.sleep(0.5)
        release.set()
        t.join(20)
        t2.join(20)
        monkeypatch.undo()
        first_token, _ = box["first"]
        second_token, second_why = box["second"]
        assert first_token
        assert second_token is None and isinstance(second_why, PairingError), (
            "a second redeemer of one invitation was given %r" % (second_token,))
        assert _devices(state) == ["laptop"]
    finally:
        state.close()
        other.close()


def test_9_after_a_success_the_code_is_spent_and_no_journal_is_left(root):
    state = _state(root)
    try:
        code = state.start_pairing()
        got, _ = _redeem(state, code)
        assert got and _journals(state) == []
        again, why = _redeem(state, code)
        assert again is None and isinstance(why, PairingError), "a redeemed code was honoured again"
        assert not any(".pairing-redemption-" in p.name and p.read_text(encoding="utf-8").find(got) >= 0
                       for p in state.root.iterdir() if p.is_file())
    finally:
        state.close()


def test_10_the_door_says_the_invitation_was_kept_only_when_it_was():
    from agentnode_sdk.gateway.server import the_pairing_remedy

    kept = TokensUnreadable("x")
    kept.invitation_kept = True
    unknown = TokensUnreadable("y")
    assert the_pairing_remedy(kept) == TokensUnreadable.WHAT_TO_DO
    assert the_pairing_remedy(unknown) == TokensUnreadable.WHAT_TO_DO_WHEN_PAIRING
