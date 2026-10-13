"""Finding J: a joining pairing (an invitation into an account that already exists) that fails half way loses
nothing for good.

`dispatch._pair` redeemed a joining invitation by removing it from the Joining store and then issuing the
token; a token-file failure spent the invitation and refused the device, with the existing account left
untouched (bundle `a1e_review/open-findings`, E0028). It is now a transaction (DECISION-J), reusing T's
journal: claim into a `kind:"joining"` journal -> remove from the store -> commit the token -> remove the
journal. A failure before the commit-to-return boundary revokes any token and RESTORES the invitation; a
crash is resolved at the next take-over. One invitation never yields two working credentials.
"""
from __future__ import annotations

import errno

import pytest


class _Stop(BaseException):
    """A process stopping here. BaseException, so no `except Exception` in the product catches it."""


@pytest.fixture()
def service(tmp_path):
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService
    from tests.test_em3c_gateway import StandInBackend, _store_measurement

    state = GatewayState(str(tmp_path / "state"), version="test")
    svc = GatewayService(state, backend=StandInBackend())
    _store_measurement(svc)
    try:
        yield svc
    finally:
        for closing in (svc.close, state.close):
            try:
                closing()
            except Exception:                              # noqa: BLE001
                pass


def _root(service):
    return str(service.state.root)


def _an_account_with_an_invitation(service):
    first = service.state._issue_token(client_name="first")
    account = service.state.account_id_for(first)
    code = service.joining.offer(account, label="second machine")["code"]
    return account, code


def _pair(service, code, name="second"):
    from agentnode_sdk.access import dispatch

    try:
        return dispatch._pair(service, {"code": code, "client_name": name}), None
    except BaseException as exc:                            # noqa: BLE001 - _Stop included
        return None, exc


def _devices(service, account):
    return sorted(str(r.get("client_name")) for r in service.state.devices_in(account))


def _open(service, account):
    return len(service.joining.outstanding(account))


def _journals(service):
    import os
    return sorted(n for n in os.listdir(service.state.root) if n.startswith(".pairing-redemption-"))


def _token_write_fails(monkeypatch):
    from agentnode_sdk.gateway.identity import GatewayState

    def refuse(self, tokens):
        raise OSError(errno.ENOSPC, "No space left on device (injected)")
    monkeypatch.setattr(GatewayState, "_write_tokens", refuse)


def _take_over(root):
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService
    from tests.test_em3c_gateway import StandInBackend, _store_measurement

    state = GatewayState(root, version="test")
    svc = GatewayService(state, backend=StandInBackend(), recover=True)
    _store_measurement(svc)
    return svc


# ---------------------------------------------------------------- the measured cases


@pytest.mark.parametrize("which", ["write", "read"])
def test_1_2_a_joining_pairing_failure_keeps_the_invitation(service, monkeypatch, which):
    from agentnode_sdk.gateway.identity import GatewayState, TokensUnreadable

    account, code = _an_account_with_an_invitation(service)
    if which == "write":
        _token_write_fails(monkeypatch)
    else:
        def refuse(self):
            raise TokensUnreadable("injected")
        monkeypatch.setattr(GatewayState, "_read_tokens", refuse)
    got, raised = _pair(service, code)
    monkeypatch.undo()
    assert got is None and isinstance(raised, TokensUnreadable), raised
    assert getattr(raised, "invitation_kept", None) is True, "the invitation was kept but the redeemer was not told"
    assert _open(service, account) == 1, "the joining invitation was spent by a failed pairing"
    assert _devices(service, account) == ["first"], "a device was added despite the failure"
    again, _ = _pair(service, code)
    assert again, "the invitation cannot be redeemed again after a failure"
    assert _devices(service, account) == ["first", "second"]
    assert _open(service, account) == 0 and _journals(service) == []


# ---------------------------------------------------------------- a crash at each durable step


def _crash_after(monkeypatch, step):
    from agentnode_sdk.gateway import joining as joining_module
    from agentnode_sdk.gateway.identity import GatewayState

    def after(owner, name):
        real = getattr(owner, name)

        def stopped(self, *a, **k):
            r = real(self, *a, **k)
            raise _Stop(step)
        monkeypatch.setattr(owner, name, stopped)       # via monkeypatch, so undo() fully restores it

    if step == "journal-written":
        # stop right after the journal is first written (state verified), before the store removal
        real = GatewayState._write_journal

        def write(self, nm, doc):
            real(self, nm, doc)
            if doc.get("kind") == "joining" and doc.get("state") == "verified":
                raise _Stop(step)
        monkeypatch.setattr(GatewayState, "_write_journal", write)
    elif step == "store-removed":
        after(joining_module._Redemption, "remove")
    elif step == "token-committed":
        after(GatewayState, "_commit_pairing_token")
    elif step == "journal-removed":
        after(GatewayState, "_remove_journal")


@pytest.mark.parametrize("step", ["journal-written", "store-removed", "token-committed", "journal-removed"])
def test_3_a_crash_at_each_step_is_resolved_at_take_over(service, monkeypatch, step):
    account, code = _an_account_with_an_invitation(service)
    _crash_after(monkeypatch, step)
    got, raised = _pair(service, code)
    monkeypatch.undo()
    assert isinstance(raised, _Stop), "the crash at %r was not reached: %r" % (step, raised)
    committed = list(service.state._read_tokens().values())
    root = _root(service)
    service.close()
    again = _take_over(root)
    try:
        if step == "journal-removed":
            # past the commit-to-return boundary: the credential stands, the invitation is spent
            assert len(committed) == 2 and "second" in _devices(again, account)
            later, why = _pair(again, code)
            assert later is None, "a spent joining code was honoured again"
            assert _journals(again) == []
        else:
            # before the boundary: any token is revoked, the invitation is restored, retry works
            assert _devices(again, account) == ["first"], (
                "after a crash at %r, take-over left a device in force: %s" % (step, _devices(again, account)))
            assert _open(again, account) == 1, "after a crash at %r the invitation was not restored" % step
            got2, why2 = _pair(again, code)
            assert got2, "after a crash at %r and take-over, the retry failed: %r" % (step, why2)
            assert _devices(again, account) == ["first", "second"] and _journals(again) == []
    finally:
        again.close()


# ---------------------------------------------------------------- what still protects joining


def test_4_a_wrong_code_still_spends_only_its_attempt_and_is_refused(service):
    from agentnode_sdk.access.dispatch import Refused

    account, code = _an_account_with_an_invitation(service)
    wrong = "ZZZZ-ZZZZ-ZZZZ" if code != "ZZZZ-ZZZZ-ZZZZ" else "YYYY-YYYY-YYYY"
    got, why = _pair(service, wrong)
    # `_pair` turns the operator path's PairingError into dispatch.Refused; a wrong joining code falls
    # through to that path and is refused there.
    assert got is None and isinstance(why, Refused), "a wrong code was not refused: %r / %r" % (got, why)
    # the real invitation is untouched and still works
    assert _open(service, account) == 1
    again, _ = _pair(service, code)
    assert again and _devices(service, account) == ["first", "second"]


def test_5_a_success_spends_the_invitation_and_leaves_no_journal(service):
    account, code = _an_account_with_an_invitation(service)
    got, _ = _pair(service, code)
    assert got and _devices(service, account) == ["first", "second"]
    assert _open(service, account) == 0 and _journals(service) == []
    again, why = _pair(service, code)
    assert again is None, "a redeemed joining code was honoured again"
