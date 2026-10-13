"""Finding W: a Windows read-sharing transient does not make a private file read wrong.

R gave tokens.json's read a bounded Windows-EACCES retry. The other private readers had none: beside an
ordinary writer that replaces the file, identity.json raised a bare PermissionError, accounts.json
fail-closed on a transient, and pairing.json and the joining store reported "nothing there" for a file that
merely could not be opened (a1e_review/open-findings, E0050). W applies one shared bounded retry
(filelock.read_retrying_on_a_windows_share) to each, and -- for pairing and joining -- makes a PERSISTENT
unreadable a refusal rather than a false "absent" (DECISION-W).

Every test forces the retry's Windows branch on any platform (filelock.RETRY_A_SHARING_VIOLATION = True) and
scripts the read to fail a few times then succeed, so they are deterministic and fast. The persistent-error
tests fake the helper's clock (filelock.time) so they do not spend the real 2 s.

Each reader is driven where its retry actually lives: the three that read through GatewayState._read_private
(identity, accounts, pairing) are exercised by replacing that bound method on the instance; the joining store
and the admission budget read their own file, so they are driven as direct units with Path.read_text replaced.
"""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from agentnode_sdk.gateway import filelock


def _sharing() -> PermissionError:
    return PermissionError(
        errno.EACCES, "Permission denied (injected sharing violation)"
    )


def _flaky(real, times, error=_sharing):
    """A stand-in for `real` that raises `error()` its first `times` calls, then delegates. Counts calls.

    Returned as a plain function so that, patched onto a CLASS (Path.read_text), it still binds the instance;
    patched onto an INSTANCE (state._read_private) it wraps the already-bound method captured in `real`.
    """

    def call(*a, **k):
        call.calls += 1
        if call.calls <= times:
            raise error()
        return real(*a, **k)

    call.calls = 0
    return call


@pytest.fixture()
def force_retry(monkeypatch):
    """Turn the Windows branch on everywhere. raising=False so this file also runs on the pre-W base."""
    monkeypatch.setattr(filelock, "RETRY_A_SHARING_VIOLATION", True, raising=False)


@pytest.fixture()
def fake_clock(monkeypatch):
    """A clock the helper reads, so a persistent-error test ends at once rather than after 2 real seconds."""
    t = {"now": 0.0}
    monkeypatch.setattr(filelock.time, "monotonic", lambda: t["now"])
    monkeypatch.setattr(
        filelock.time, "sleep", lambda s: t.__setitem__("now", t["now"] + s)
    )


@pytest.fixture()
def state(tmp_path):
    from agentnode_sdk.gateway.identity import GatewayState

    s = GatewayState(str(tmp_path / "state"), version="test")
    s.identity  # establish identity.json and the counter markers
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------- identity.json


def test_identity_a_transient_is_waited_out(state, force_retry, monkeypatch):
    flaky = _flaky(state._read_private, 3)
    monkeypatch.setattr(state, "_read_private", flaky)
    assert state.identity.gateway_id, (
        "a transient denial lost the gateway's own identity"
    )
    assert flaky.calls >= 4


def test_identity_a_persistent_denial_is_a_named_refusal(
    state, force_retry, fake_clock, monkeypatch
):
    from agentnode_sdk.gateway.identity import PairingError

    monkeypatch.setattr(state, "_read_private", _flaky(state._read_private, 10**9))
    with pytest.raises(PairingError):
        _ = state.identity  # not a bare PermissionError escaping


# ---------------------------------------------------------------- accounts.json


def test_accounts_a_transient_is_waited_out(state, force_retry, monkeypatch):
    from agentnode_sdk.gateway.accounts import ACTIVE

    account = state.accounts.create(name="c").account_id
    flaky = _flaky(state._read_private, 3)
    monkeypatch.setattr(state, "_read_private", flaky)
    assert state.accounts.get(account).state == ACTIVE, (
        "a transient refused a good account read"
    )


def test_accounts_a_persistent_denial_stays_fail_closed(
    state, force_retry, fake_clock, monkeypatch
):
    from agentnode_sdk.gateway.accounts import AccountsUnreadable

    state.accounts.create(name="c")
    monkeypatch.setattr(state, "_read_private", _flaky(state._read_private, 10**9))
    with pytest.raises(AccountsUnreadable):
        state.accounts.all()


# ---------------------------------------------------------------- pairing.json


def test_pairing_a_transient_is_waited_out(state, force_retry, monkeypatch):
    state.start_pairing()
    state._pairing = None  # force the disk read rather than the in-memory copy
    flaky = _flaky(state._read_private, 3)
    monkeypatch.setattr(state, "_read_private", flaky)
    assert state.pairing_active() is True, (
        "a transient reported a live pairing as absent"
    )


def test_pairing_a_persistent_denial_is_not_read_as_absent(
    state, force_retry, fake_clock, monkeypatch
):
    from agentnode_sdk.gateway.identity import PairingError

    state.start_pairing()
    state._pairing = None
    monkeypatch.setattr(state, "_read_private", _flaky(state._read_private, 10**9))
    with pytest.raises(PairingError):
        state.pairing_active()  # a live invitation must not become "no pairing"


# ---------------------------------------------------------------- the joining store


def test_joining_a_transient_is_waited_out(tmp_path, force_retry, monkeypatch):
    from agentnode_sdk.gateway.joining import Joining

    j = Joining(str(tmp_path))
    j.offer(
        "acct-11111111", label="second"
    )  # writes the file before the reader is made flaky
    monkeypatch.setattr(Path, "read_text", _flaky(Path.read_text, 3))
    assert len(j.outstanding("acct-11111111")) == 1, (
        "a transient reported a live invitation as absent"
    )


def test_joining_a_persistent_denial_is_not_read_as_empty(
    tmp_path, force_retry, fake_clock, monkeypatch
):
    from agentnode_sdk.gateway.joining import Joining, JoiningUnreadable

    j = Joining(str(tmp_path))
    j.offer("acct-11111111", label="second")
    monkeypatch.setattr(Path, "read_text", _flaky(Path.read_text, 10**9))
    with pytest.raises(JoiningUnreadable):
        j.outstanding("acct-11111111")  # live invitations must not vanish


# ---------------------------------------------------------------- the admission budget


def test_budget_a_transient_does_not_spend_the_whole_allowance(
    tmp_path, force_retry, monkeypatch
):
    from agentnode_sdk.gateway.throttle import Budget

    path = tmp_path / "pairing-admission.json"
    marker = tmp_path / "identity.json"
    marker.write_text("{}", encoding="utf-8")
    path.write_text(json.dumps({"spent": [], "last_seen": 0.0}), encoding="utf-8")
    budget = Budget(path=path, established_marker=marker)
    monkeypatch.setattr(Path, "read_text", _flaky(Path.read_text, 3))
    assert budget.remaining() == budget.allowance, (
        "a transient read made the whole allowance look spent"
    )


# ---------------------------------------------------------------- what is not a sharing violation


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: FileNotFoundError(errno.ENOENT, "no file"),
        lambda: PermissionError(errno.EPERM, "not a sharing violation"),
        lambda: OSError(errno.EIO, "io error"),
    ],
    ids=["not-found", "eperm-not-eacces", "an-io-error"],
)
def test_a_non_sharing_error_is_not_retried(
    state, force_retry, monkeypatch, make_error
):
    flaky = _flaky(state._read_private, 10**9, error=make_error)
    monkeypatch.setattr(state, "_read_private", flaky)
    try:
        _ = state.identity
    except Exception:  # noqa: BLE001
        pass
    assert flaky.calls == 1, (
        "an error that is not a Windows sharing violation was retried"
    )


def test_the_retry_is_off_except_on_windows():
    """Read without force_retry: the production default is Windows-only."""
    import os

    assert filelock.RETRY_A_SHARING_VIOLATION is (os.name == "nt")
