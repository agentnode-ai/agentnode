"""A token file this gateway could not read is not a token file that says nobody is paired.

`GatewayState._read_tokens` turned every `OSError` and every parse failure into `{}`, and every
caller reads the file afresh. `dispatch.identify` built one principal out of four separate reads.
So one failed read, inside one request, decided things nobody had decided:

* the owner of a run was told it did not exist (the account read failed, the principal had no
  account, and the run was looked up in the empty namespace);
* a device whose record narrows what it may do was handed the defaults (the device-list read
  failed, so the record was "not found");
* a pairing wrote back a file holding only the new device, and every other device's credential
  stopped working (the read before the write returned nothing).

Measured before the change in `a1e_review/o6-a-failed-read-is-not-an-answer/evidence/E0001`.

What holds now: absence is `{}` (a gateway nobody has paired with yet), and anything else that
stops the file being used raises `TokensUnreadable`. Nothing is written over what could not be read;
identification reads the file once; and every door answers `sandbox_unavailable` (503), never
"not authenticated", "no such run" or a document that says all is well. The operator is told on
stderr, by the request path and nothing else.

Each injected failure is ONE read, at a position counted from a request that did not fail, so the
tests do not depend on how many reads the code happens to make.
"""
from __future__ import annotations

import hashlib
import json
import traceback
from types import SimpleNamespace

import pytest

from agentnode_sdk.access import contract, dispatch, rest
from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway import identity
from agentnode_sdk.gateway.identity import GatewayState, hash_token
from agentnode_sdk.gateway.protocol import sign
from tests.real_answers import RealGateway

TOKENS = "tokens.json"


# ---------------------------------------------------------------------------------------- tools

def _is_unreadable(exc) -> bool:
    # By name, so this file imports on a gateway that has no such class and its tests go red one
    # by one rather than all at once.
    return type(exc).__name__ == "TokensUnreadable"


class Reads:
    """Counts reads of the token file and, when told to, makes exactly one of them fail."""

    def __init__(self, monkeypatch, fail_at: int = 0, inside: str = "", outside: tuple = ()):
        self.count = 0
        self.failed = False
        real = GatewayState._read_private

        def read(state, name):
            if name == TOKENS:
                names = {f.name for f in traceback.extract_stack()}
                if (inside and inside not in names) or names & set(outside):
                    return real(state, name)
                self.count += 1
                if fail_at and self.count == fail_at and not self.failed:
                    self.failed = True
                    raise PermissionError(32, "the file is being replaced")
            return real(state, name)

        monkeypatch.setattr(GatewayState, "_read_private", read)


class Writes:
    def __init__(self, monkeypatch):
        self.count = 0
        real = GatewayState._write_tokens

        def write(state, tokens):
            self.count += 1
            return real(state, tokens)

        monkeypatch.setattr(GatewayState, "_write_tokens", write)


def _ask(method: str, url: str, token: str = "", body: dict | None = None):
    """(status, body), or (None, why) when no HTTP answer came back at all.

    Every way of getting no answer counts as no answer, so a door that drops the connection fails
    the assertion about what the caller was told, with its own message, rather than raising here:
    a server that closes without a response surfaces as `http.client.RemoteDisconnected`, which is
    an `OSError` and not wrapped by urllib (counter-check d of `E0011` met exactly that).
    """
    import http.client

    try:
        if method == "GET":
            return gc._get(url, token=token)
        return gc._post(url, body or {}, headers={rest.TOKEN_HEADER: token} if token else {})
    except (gc.GatewayClientError, OSError, http.client.HTTPException) as exc:
        return None, {"no answer": ("%s: %s" % (type(exc).__name__, exc))[:200]}


def _reads_in(monkeypatch, do) -> int:
    counter = Reads(monkeypatch)
    do()
    monkeypatch.undo()
    return counter.count


def _secret(token: str) -> bytes:
    return hashlib.sha256(f"em3c-sig\n{token}".encode()).digest()


@pytest.fixture
def gw(tmp_path):
    gateway = RealGateway(tmp_path / "gw")
    try:
        yield gateway
    finally:
        gateway.close()


def _state_with_two(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    first = state._issue_token(client_name="first")
    account = state.account_id_for(first)
    second = state._issue_token(client_name="second", account_id=account)
    return state, first, second, account


# ------------------------------------------------------------------- the discriminating tests

def test_1_the_owner_is_never_told_their_own_run_does_not_exist(gw, monkeypatch):
    """Every read the status request makes, failed in turn. Each answer is either the refusal that
    names the gateway's own trouble, or the owner's run itself -- a read that decided nothing may
    fail without changing the answer. Never "not authenticated", never "no such run"."""
    run_id = gw.a_finished_run()["run_id"]
    token = gw.connection.token
    doors = {
        "rest": lambda: _ask("POST", gw.base + "/v1/op/status", token, {"run_id": run_id}),
        "older": lambda: _ask("GET", gw.base + "/v1/jobs/" + run_id, token),
    }
    for door, asking in doors.items():
        n = _reads_in(monkeypatch, asking)
        assert n >= 1, door
        for k in range(1, n + 1):
            Reads(monkeypatch, fail_at=k)
            status, body = asking()
            monkeypatch.undo()
            the_refusal = status == 503 and body.get("refused") == "sandbox_unavailable"
            the_run = status == 200 and body.get("run_id") == run_id and "refused" not in body
            assert the_refusal or the_run, (
                "%s door, read %d of %d failed: the owner was told %r %r about their own run"
                % (door, k, n, status, {key: body.get(key) for key in ("refused", "error")}))


def test_2_a_narrowed_device_is_never_given_more(gw, monkeypatch):
    token = gw.connection.token
    tokens = gw.state._read_tokens()
    tokens[hash_token(token)]["capabilities"] = [contract.READ]
    gw.state._write_tokens(tokens)
    n = _reads_in(monkeypatch, lambda: dispatch.identify(gw.service, token))
    for k in range(1, n + 1):
        Reads(monkeypatch, fail_at=k)
        try:
            held = dispatch.identify(gw.service, token).capabilities
        except Exception as exc:                              # noqa: BLE001
            assert _is_unreadable(exc), "read %d failed and identify raised %r" % (k, exc)
            held = ()
        monkeypatch.undo()
        assert set(held) <= {contract.READ}, (
            "a device recorded with ('read',) was given %r after read %d of %d failed" % (held, k, n))


def test_3_pairing_after_a_failed_read_keeps_every_other_device(tmp_path, monkeypatch):
    state, first, second, account = _state_with_two(tmp_path)
    before = (state.root / TOKENS).read_bytes()
    Reads(monkeypatch, fail_at=1, inside="_issue_token")
    issued, raised = None, None
    try:
        issued = state.redeem_for_connection(client_name="third", account_id=account)
    except Exception as exc:                                  # noqa: BLE001
        raised = exc
    monkeypatch.undo()
    after = (state.root / TOKENS).read_bytes()
    still = [bool(state.client_id_for(t)) for t in (first, second)]
    assert all(still) and after == before, (
        "pairing after one failed read left %d of %d credentials (file unchanged: %s)"
        % (sum(still), 2, after == before))
    assert issued is None and raised is not None and _is_unreadable(raised), (
        "pairing after one failed read issued %r and raised %r" % (bool(issued), raised))


def test_4_no_writer_writes_over_what_it_could_not_read(tmp_path, monkeypatch):
    writers = {
        "set_client_allowance": lambda s, a, cid, acc: s.set_client_allowance(a, ["example.com"]),
        "_issue_token": lambda s, a, cid, acc: s.redeem_for_connection(client_name="x", account_id=acc),
        "move_device_to": lambda s, a, cid, acc: s.move_device_to(cid, acc),
        "rotate_token": lambda s, a, cid, acc: s.rotate_token(a),
        "revoke": lambda s, a, cid, acc: s.revoke(a),
        "revoke_client": lambda s, a, cid, acc: s.revoke_client(cid),
    }
    for i, (name, call) in enumerate(writers.items()):
        state, first, second, account = _state_with_two(tmp_path / str(i))
        client_id = state.client_id_for(first)
        before = (state.root / TOKENS).read_bytes()
        Reads(monkeypatch, fail_at=1)
        writes = Writes(monkeypatch)
        raised = None
        try:
            answered = call(state, first, client_id, account)
        except Exception as exc:                              # noqa: BLE001
            raised, answered = exc, None
        monkeypatch.undo()
        assert writes.count == 0 and (state.root / TOKENS).read_bytes() == before, (
            "%s wrote over what it could not read (%d write(s))" % (name, writes.count))
        assert raised is not None and _is_unreadable(raised), (
            "%s answered %r as if nobody held a token, instead of saying it could not read"
            % (name, answered if raised is None else raised))


def test_4b_the_revoke_command_writes_nothing_and_finds_nobody_falsely(tmp_path, monkeypatch, capsys):
    from agentnode_sdk.cli import gateway_commands

    # The command's own two reads: the one that finds the client, and the one before its write.
    # Reads made while it builds its service object are not counted, so neither position moves.
    positions = {1: dict(inside="_resolve_client"),
                 2: dict(inside="cmd_revoke", outside=("_resolve_client", "_service"))}
    for k, where in positions.items():
        state, first, second, account = _state_with_two(tmp_path / str(k))
        client_id = state.client_id_for(first)
        root = state.root
        before = (root / TOKENS).read_bytes()
        state.close()
        Reads(monkeypatch, fail_at=1, **where)
        writes = Writes(monkeypatch)
        raised = None
        try:
            gateway_commands.cmd_revoke(SimpleNamespace(dir=str(root), client=client_id))
        except Exception as exc:                              # noqa: BLE001
            raised = exc
        monkeypatch.undo()
        said = capsys.readouterr().out
        assert writes.count == 0 and (root / TOKENS).read_bytes() == before, (
            "gateway revoke wrote over what it could not read at read %d" % k)
        assert "No connected client matches" not in said, (
            "gateway revoke said no client matches after read %d failed" % k)
        assert raised is not None and _is_unreadable(raised), (
            "gateway revoke, read %d failed: raised %r" % (k, raised))


def test_5_an_unreadable_file_is_not_read_as_nobody(tmp_path):
    cases = {
        "not JSON": b"{ this is not json",
        "a list": b"[]",
        "an entry that is not an object": b'{"abc": "def"}',
        "not UTF-8": b'{"\xff\xfe": {}}',
    }
    for i, (what, content) in enumerate(cases.items()):
        state = GatewayState(str(tmp_path / str(i)), version="test")
        (state.root / TOKENS).write_bytes(content)
        # Private, as every token file this gateway writes is. Left at the default mode, a POSIX
        # gateway refuses the file as insecure before its content is looked at -- which is right,
        # and is not what this test is about (it went red that way on Linux CI, `O6` E0018).
        import os

        if os.name == "posix":
            os.chmod(state.root / TOKENS, 0o600)
        try:
            got = state._read_tokens()
        except Exception as exc:                              # noqa: BLE001
            assert _is_unreadable(exc), "%s: raised %s rather than TokensUnreadable" % (
                what, type(exc).__name__)
            continue
        pytest.fail("read an unreadable file as nobody: %s gave %r" % (what, got))


def test_6_an_empty_capability_list_holds_nothing(gw):
    token = gw.connection.token
    tokens = gw.state._read_tokens()
    tokens[hash_token(token)]["capabilities"] = []
    gw.state._write_tokens(tokens)
    by_token = dispatch.identify(gw.service, token)
    by_device = dispatch.identify_client(gw.service, by_token.client_id)
    assert by_token.capabilities == () and by_device.capabilities == (), (
        "widened an empty capability list: by token %r, by device %r"
        % (by_token.capabilities, by_device.capabilities))


def test_7_identification_reads_the_token_file_once(gw, monkeypatch):
    token = gw.connection.token
    payload = {"purpose": "a reading count"}
    bearer = _reads_in(monkeypatch, lambda: dispatch.identify(gw.service, token))
    signed = _reads_in(monkeypatch, lambda: dispatch.identify(
        gw.service, token, proof=(payload, sign(_secret(token), payload))))
    assert dispatch.identify(gw.service, token, proof=(payload, sign(_secret(token), payload))).proved
    assert bearer == 1 and signed == 1, (
        "read the token file %d times for a bearer token and %d for a signed one" % (bearer, signed))


def test_8_every_door_says_sandbox_unavailable(gw, monkeypatch):
    token = gw.connection.token
    seen = {}

    payload = {"purpose": "rotate"}
    Reads(monkeypatch, fail_at=1)
    seen["older signed door"] = _ask("POST", gw.base + "/v1/token/rotate", "",
                                     {"token": token, "payload": payload,
                                      "signature": sign(_secret(token), payload)})
    monkeypatch.undo()

    code = gw.state.start_pairing()
    before = (gw.state.root / TOKENS).read_bytes()
    Reads(monkeypatch, fail_at=1, inside="_issue_token")
    seen["pairing"] = _ask("POST", gw.base + "/v1/pair", "", {"code": code, "client_name": "p"})
    monkeypatch.undo()
    assert (gw.state.root / TOKENS).read_bytes() == before, "pairing wrote over what it could not read"

    # Health is not on this list because it identifies nobody: it does not read the token file at
    # all (measured, `E0004`), so there is no read of it to fail. What is held instead is that it
    # stays that way -- a health answer that started depending on who is paired would need this test.
    reads = Reads(monkeypatch, fail_at=1)
    status, body = _ask("GET", gw.base + "/v1/health")
    monkeypatch.undo()
    assert reads.count == 0 and status == 200, (
        "health read the token file %d time(s) and answered %r" % (reads.count, status))

    for door, (status, body) in seen.items():
        assert status == 503 and body.get("refused") == "sandbox_unavailable", (
            "%s, one failed read: told %r %r, which tells them to pair again or that all is well"
            % (door, status, {k: body.get(k) for k in ("refused", "error", "status")}))
        assert "signature" not in body and "binding" not in body, (
            "%s signed an answer it could not know the key for" % door)
    assert "does not match" not in json.dumps(seen["pairing"][1]), "pairing blamed the code"

    Reads(monkeypatch, fail_at=1)
    status, body = _ask("POST", gw.base + rest.MCP_PATH, token,
                        {"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}})
    monkeypatch.undo()
    assert status == 503 and body.get("jsonrpc") == "2.0" and body.get("id") == 7 and (
        "error" in body), "the MCP door, one failed read: told %r %r" % (status, body)


def test_9_the_operator_is_told_by_path_and_nothing_else(gw, monkeypatch, capsys):
    token = gw.connection.token
    run_id = gw.a_finished_run()["run_id"]
    record = json.dumps(gw.state._read_tokens())
    capsys.readouterr()
    Reads(monkeypatch, fail_at=1)
    _ask("POST", gw.base + "/v1/op/status?where=QUERYVALUE", token, {"run_id": run_id})
    monkeypatch.undo()
    said = [line for line in capsys.readouterr().err.splitlines() if TOKENS in line]
    assert len(said) == 1, "the operator was told nothing (or %d times)" % len(said)
    line = said[0]
    assert "/v1/op/status" in line, "the operator was not told which request: %r" % line
    for secret in (token, "QUERYVALUE", run_id) + tuple(
            v for entry in json.loads(record).values() for v in entry.values() if isinstance(v, str)):
        assert secret not in line, "the operator's line carried %r" % secret[:12]


def test_9b_the_operator_is_never_given_an_address_as_the_caller_wrote_it(gw, monkeypatch, capsys):
    """Review round one (O6-R1-F1): test_9 drove a route whose run id is in the BODY. The older
    status door carries it in the ADDRESS, and an unknown operation's address is whatever the caller
    chose. The line must name the route, not repeat either."""
    token = gw.connection.token
    run_id = gw.a_finished_run()["run_id"]
    asked = {
        "/v1/jobs/<run>": lambda: _ask("GET", gw.base + "/v1/jobs/%s?where=QUERYVALUE" % run_id, token),
        "an address this gateway does not name": lambda: _ask(
            "POST", gw.base + "/v1/op/zz-CALLERTEXT", token, {}),
    }
    for route, asking in asked.items():
        capsys.readouterr()
        Reads(monkeypatch, fail_at=1)
        asking()
        monkeypatch.undo()
        said = [line for line in capsys.readouterr().err.splitlines() if TOKENS in line]
        assert len(said) == 1 and route in said[0], (
            "the operator was not told the route %r: %r" % (route, said))
        for caller_data in (run_id, "QUERYVALUE", "zz-CALLERTEXT", token):
            assert caller_data not in said[0], (
                "the operator's line repeated what the caller sent: %r" % caller_data[:12])


# ------------------------------------------------------------------------------- the controls

def test_10_control_absence_is_a_gateway_nobody_has_paired_with(tmp_path):
    state = GatewayState(str(tmp_path / "fresh"), version="test")
    assert not (state.root / TOKENS).exists()
    assert state._read_tokens() == {}
    token = state._issue_token(client_name="the first")
    assert state.client_id_for(token)


def test_11_control_an_ordinary_request_is_unaffected(gw):
    run_id = gw.a_finished_run()["run_id"]
    token = gw.connection.token
    status, body = _ask("POST", gw.base + "/v1/op/status", token, {"run_id": run_id})
    assert status == 200, body
    status, body = _ask("GET", gw.base + "/v1/jobs/" + run_id, token)
    assert status == 200 and body.get("run_id") == run_id, body


def test_12_control_a_withdrawn_token_is_still_not_authenticated(gw):
    token = gw.connection.token
    run_id = gw.a_finished_run()["run_id"]
    assert gw.state.revoke(token)
    status, body = _ask("POST", gw.base + "/v1/op/status", token, {"run_id": run_id})
    assert status == 401 and body.get("refused") == "not_authenticated", (status, body)


def test_13_control_a_record_without_capabilities_holds_the_defaults(gw):
    token = gw.connection.token
    assert "capabilities" not in gw.state._read_tokens()[hash_token(token)]
    by_token = dispatch.identify(gw.service, token)
    by_device = dispatch.identify_client(gw.service, by_token.client_id)
    assert set(by_token.capabilities) == set(contract.CAPABILITIES)
    assert set(by_device.capabilities) == set(contract.CAPABILITIES)
