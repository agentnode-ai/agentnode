"""O6 follow-up L: no change to the token file loses another change made at the same time.

Every change to `tokens.json` read the whole file, changed its copy and wrote the copy back, with nothing
between two of them. So two changes that overlapped kept only the one that wrote last. Measured on main
(bundle `o6-followups`, E0003 and E0004):

* two pairings that both answered with a credential, and one credential did not work afterwards;
* a revocation that answered True, undone by a pairing that wrote after it, so the revoked credential
  worked again.

Now every change goes through one locked read-decide-write (DECISION-L, from consultations Q0001 and
Q0002). The races here are forced the same way the measurement forced them: each writer's first read
waits until the other writer has read too, and the second writer's write waits until the first has written.
On the unrepaired code that is a lost update every time. With the lock, the second writer cannot read until
the first has finished, so the waits run out and nothing is lost.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from agentnode_sdk.gateway.identity import GatewayState, TokensUnreadable

#: How long a forced wait lasts before it gives up. Only the repaired code ever reaches it, because
#: there the lock makes the other writer wait for this one.
WAIT = 1.5


def _holders(tmp_path, n=2):
    root = str(tmp_path / "state")
    return [GatewayState(root, version="test") for _ in range(n)]


def _close(*states):
    for s in states:
        try:
            s.close()
        except Exception:                                      # noqa: BLE001
            pass


def _names(state):
    return sorted(str(r.get("client_name")) for r in state._read_tokens().values())


def _force_the_interleaving(monkeypatch):
    """The FIRST read by each of the threads `first` and `second` waits for the other's. Then `second`
    writes only after `first` has written. Both waits are bounded by WAIT."""
    real_read, real_write = GatewayState._read_tokens, GatewayState._write_tokens
    gate = threading.Barrier(2, timeout=WAIT)
    has_read = set()
    first_wrote = threading.Event()

    def read(self):
        got = real_read(self)
        me = threading.current_thread().name
        if me in ("first", "second") and me not in has_read:
            has_read.add(me)
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass
        return got

    def write(self, tokens):
        me = threading.current_thread().name
        if me == "second":
            first_wrote.wait(WAIT)
        real_write(self, tokens)
        if me == "first":
            first_wrote.set()

    monkeypatch.setattr(GatewayState, "_read_tokens", read)
    monkeypatch.setattr(GatewayState, "_write_tokens", write)


def _race(monkeypatch, first, second):
    """Run `first` and `second` in the forced interleaving. Returns their answers and any errors."""
    _force_the_interleaving(monkeypatch)
    out, errors = {}, []

    def run(name, fn):
        try:
            out[name] = fn()
        except Exception as exc:                                # noqa: BLE001
            errors.append("%s: %r" % (name, exc))

    threads = [threading.Thread(target=run, name=n, args=(n, fn))
               for n, fn in (("first", first), ("second", second))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    monkeypatch.undo()
    assert not errors, errors
    return out["first"], out["second"]


# ---------------------------------------------------------------- the measured races


def test_1_two_pairings_from_two_holders_both_stand(tmp_path, monkeypatch):
    one, two = _holders(tmp_path)
    try:
        one._issue_token(client_name="seed")
        a, b = _race(monkeypatch, lambda: one._issue_token(client_name="one"),
                     lambda: two._issue_token(client_name="two"))
        assert _names(one) == ["one", "seed", "two"], "a pairing that answered was lost: %s" % _names(one)
        assert one.token_record(a) is not None and one.token_record(b) is not None
    finally:
        _close(one, two)


def test_2_two_pairings_through_one_holder_both_stand(tmp_path, monkeypatch):
    (state,) = _holders(tmp_path, 1)
    try:
        state._issue_token(client_name="seed")
        _race(monkeypatch, lambda: state._issue_token(client_name="one"),
              lambda: state._issue_token(client_name="two"))
        assert _names(state) == ["one", "seed", "two"], "a pairing that answered was lost: %s" % _names(state)
    finally:
        _close(state)


def test_3_a_revocation_then_a_pairing_both_stand(tmp_path, monkeypatch):
    one, two = _holders(tmp_path)
    try:
        one._issue_token(client_name="seed")
        going = one._issue_token(client_name="going")
        revoked, new = _race(monkeypatch, lambda: two.revoke(going),
                             lambda: one._issue_token(client_name="new"))
        assert revoked is True
        assert one.token_record(going) is None, "a revocation that answered True was undone"
        assert _names(one) == ["new", "seed"], _names(one)
    finally:
        _close(one, two)


def test_4_a_pairing_then_a_revocation_both_stand(tmp_path, monkeypatch):
    one, two = _holders(tmp_path)
    try:
        one._issue_token(client_name="seed")
        going = one._issue_token(client_name="going")
        new, revoked = _race(monkeypatch, lambda: one._issue_token(client_name="new"),
                             lambda: two.revoke(going))
        assert revoked is True and one.token_record(going) is None
        assert one.token_record(new) is not None, "a pairing that answered was lost: %s" % _names(one)
    finally:
        _close(one, two)


# ---------------------------------------------------------------- a pairing against every other change


def _seeded(tmp_path):
    one, two = _holders(tmp_path)
    one._issue_token(client_name="seed")
    token = one._issue_token(client_name="target")
    return one, two, token, one.client_id_for(token)


def test_5_an_allowance_and_a_pairing_both_stand(tmp_path, monkeypatch):
    one, two, token, _cid = _seeded(tmp_path)
    try:
        done, _new = _race(monkeypatch, lambda: two.set_client_allowance(token, ["example.com"]),
                           lambda: one._issue_token(client_name="new"))
        assert done is True
        assert one.client_allowance(token) == ["example.com"], "an allowance that answered True was lost"
        assert "new" in _names(one)
    finally:
        _close(one, two)


def test_6_a_move_and_a_pairing_both_stand(tmp_path, monkeypatch):
    one, two, token, cid = _seeded(tmp_path)
    try:
        elsewhere = one.accounts.create(name="elsewhere").account_id
        moved, _new = _race(monkeypatch, lambda: two.move_device_to(cid, elsewhere),
                            lambda: one._issue_token(client_name="new"))
        assert moved is True
        assert one.account_id_for(token) == elsewhere, "a move that answered True was lost"
        assert "new" in _names(one)
    finally:
        _close(one, two)


def test_7_a_rotation_and_a_pairing_both_stand(tmp_path, monkeypatch):
    one, two, token, _cid = _seeded(tmp_path)
    try:
        replacement, _new = _race(monkeypatch, lambda: two.rotate_token(token),
                                  lambda: one._issue_token(client_name="new"))
        assert replacement
        assert one.token_record(token) is None, "the retired credential works again"
        assert one.token_record(replacement) is not None, "the replacement credential was lost"
        assert "new" in _names(one)
    finally:
        _close(one, two)


def test_8_a_revocation_by_device_and_a_pairing_both_stand(tmp_path, monkeypatch):
    one, two, token, cid = _seeded(tmp_path)
    try:
        revoked, _new = _race(monkeypatch, lambda: two.revoke_client(cid),
                              lambda: one._issue_token(client_name="new"))
        assert revoked is True
        assert one.token_record(token) is None, "a revocation that answered True was undone"
        assert "new" in _names(one)
    finally:
        _close(one, two)


def test_9_the_operators_revoke_and_a_pairing_both_stand(tmp_path, monkeypatch, capsys):
    from agentnode_sdk.cli import gateway_commands

    one, two, token, cid = _seeded(tmp_path)
    root = str(one.root)
    _close(two)
    try:
        said, _new = _race(monkeypatch,
                           lambda: gateway_commands.cmd_revoke(SimpleNamespace(dir=root, client=cid)),
                           lambda: one._issue_token(client_name="new"))
        assert said == 0, capsys.readouterr().out
        assert one.token_record(token) is None, "the operator's revocation was undone"
        assert "new" in _names(one)
    finally:
        _close(one)


# ---------------------------------------------------------------- what the lock itself must do


def test_10_a_lock_that_cannot_be_taken_changes_nothing_and_says_so(tmp_path, monkeypatch):
    from agentnode_sdk.gateway.filelock import LockUnavailable

    one, two, token, cid = _seeded(tmp_path)
    try:
        monkeypatch.setattr(GatewayState, "TOKENS_LOCK_SECONDS", 0.3)
        before = (one.root / "tokens.json").read_bytes()
        calls = {
            "_issue_token": lambda: two._issue_token(client_name="new"),
            "set_client_allowance": lambda: two.set_client_allowance(token, ["example.com"]),
            "move_device_to": lambda: two.move_device_to(cid, two.account_id_for(token)),
            "rotate_token": lambda: two.rotate_token(token),
            "revoke": lambda: two.revoke(token),
            "revoke_client": lambda: two.revoke_client(cid),
            "revoke_named_client": lambda: two.revoke_named_client(cid),
        }
        answered = {}
        with one._tokens_lock():
            for name, call in calls.items():
                box = {}

                def run(call=call, box=box):
                    try:
                        box["answer"] = call()
                    except Exception as exc:                    # noqa: BLE001
                        box["raised"] = exc

                t = threading.Thread(target=run)
                t.start()
                t.join(10)
                answered[name] = box
        for name, box in answered.items():
            raised = box.get("raised")
            assert isinstance(raised, TokensUnreadable), (
                "%s, while the lock was held elsewhere, answered %r" % (name, box))
            assert isinstance(raised.__cause__, LockUnavailable), (name, raised.__cause__)
        assert (one.root / "tokens.json").read_bytes() == before, "a change was written without the lock"
    finally:
        _close(one, two)


def test_11_every_change_alone_completes(tmp_path):
    """No locked change calls another locked change: each one, called alone, finishes."""
    one, two, token, cid = _seeded(tmp_path)
    try:
        assert one.set_client_allowance(token, ["example.com"]) is True
        assert one.move_device_to(cid, one.account_id_for(token)) is True
        replacement = one.rotate_token(token)
        assert replacement and one.token_record(replacement) is not None
        assert one.revoke_client(cid) is True
        spare = one._issue_token(client_name="spare")
        assert one.revoke(spare) is True
        named = one._issue_token(client_name="named")
        said = one.revoke_named_client("named")
        assert said["outcome"] == "revoked" and one.token_record(named) is None
    finally:
        _close(one, two)


def test_12_the_operators_revoke_refuses_an_ambiguous_name_and_writes_nothing(tmp_path, capsys):
    from agentnode_sdk.cli import gateway_commands

    (state,) = _holders(tmp_path, 1)
    try:
        a = state._issue_token(client_name="laptop")
        b = state._issue_token(client_name="laptop")
        before = (state.root / "tokens.json").read_bytes()
        said = state.revoke_named_client("laptop")
        assert said["outcome"] == "ambiguous" and len(said["matches"]) == 2, said
        code = gateway_commands.cmd_revoke(SimpleNamespace(dir=str(state.root), client="laptop"))
        out = capsys.readouterr().out
        assert code == 1, out
        assert (state.root / "tokens.json").read_bytes() == before, "an ambiguous revoke wrote"
        assert state.token_record(a) is not None and state.token_record(b) is not None
    finally:
        _close(state)


@pytest.mark.parametrize("wanted, outcome", [("nobody-at-all", "none"), ("", "none")])
def test_13_the_operators_revoke_finds_nobody_without_writing(tmp_path, wanted, outcome):
    (state,) = _holders(tmp_path, 1)
    try:
        state._issue_token(client_name="laptop")
        before = (state.root / "tokens.json").read_bytes()
        assert state.revoke_named_client(wanted)["outcome"] == outcome
        assert (state.root / "tokens.json").read_bytes() == before
    finally:
        _close(state)
