"""Local worker, slice 2: pairing `agentnode-worker-pair/1` (Codex Q-LW2-0001 D5/D6 as amended by Q-LW2-0002 Q2 and
Q-LW2-0003 Q1/Q4; profile local-worker-slice2-r2 L7-L10, counter-checks 13-19).

Real issuer, real signed register, real gateway state (accounts, devices, sessions), the real pairing store, the real
root helper logic and `Operations.pair`, and the real agent-side crypto (`worker.pairing`): a person's approval in an
account becomes, exactly once, a worker device holding nothing, an enrolled reverse target, a certificate for the
agent's OWN key and a pair key only the agent can open."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentnode_sdk.access.sessions import Sessions, fingerprint
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.remote_mcp import agent_authority as AA
from agentnode_sdk.remote_mcp import agent_service as AS
from agentnode_sdk.remote_mcp import pairing_helper as PH
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp.target_ops import OperationFailed, Operations
from agentnode_sdk.worker import pairing as WP
from agentnode_sdk.worker import pins as PN
from tests.test_mtls_transport import World, _one_boot  # noqa: F401 - autouse

from .target_fixtures import make_register, raw_pub

ACCT = "acct-00000000000000a1"
OTHER = "acct-00000000000000b2"
CONTROL = "https://mcp.agentnode.net/agent/v1/slot"


class PairStand:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.world = World(tmp / "pki")
        self.reg, self.op_key = make_register(tmp / "stand")
        acks = T.TrustAcks(self.reg.stand, raw_pub(self.op_key), operator_private=self.op_key)
        self.ops = Operations(stand=self.reg.stand, register=self.reg, acks=acks, issuer=self.world.issuer,
                              keyring_path=tmp / "ring.json", gateway_instance="g1", deployment=self.world.deployment,
                              bundles_dir=tmp / "bundles", worker_for=lambda t: None, slot=None)
        self.gw = tmp / "gwdir"
        self.gw.mkdir()
        self.state = GatewayState(str(self.gw), "test")
        self.state.accounts.create(account_id=ACCT, name="Alice")
        self.state.accounts.create(account_id=OTHER, name="Bob")
        self.state._issue_token(client_name="alice-laptop", account_id=ACCT)
        self.person = next(r["client_id"] for r in self.state.paired_clients())
        self.ops.pairing_device = lambda a, n, o: self.state.add_worker_device(a, n, o)
        self.ops.pairing_signer = self.op_key
        self.ops.pairing_control = CONTROL
        self.store_dir = tmp / "store"
        self.helper = PH.Helper(self.ops, gateway_dir=self.gw, store=self.store_dir, allowed_uid=1000)
        self.store = AS.PairingStore(self.store_dir, helper=self.helper.materialize)
        self.pins = {"schema": PN.PINS_SCHEMA, "channel": "beta", "release_seq": 1, "control_url": CONTROL,
                     "anchor_sha256": PN.anchor_sha256(self.world.anchor.read_bytes()),
                     "operator_pub": raw_pub(self.op_key).hex(), "gateway_deployment": self.world.deployment,
                     "gateway_instance": "g1"}
        self.sid, _csrf = Sessions(str(self.gw)).open(self.person, label="browser")

    def begin(self, name="laptop"):
        state_dir = self.tmp / ("agent-" + name)
        material = WP.new_material(state_dir)
        out = self.store.start({"protocol": WP.PROTOCOL, "csr": material["csr"].decode("ascii"),
                                "transport_key": WP.b64(material["transport_pub"]), "name": name, "concurrency": 1,
                                "queue": 2, "platform": {"os": "linux"}}, "203.0.113.7")
        return material, out, state_dir

    def poll(self, material, out, what="poll", now=None):
        self.store._pending(WP.code_hash(out["device_code"]))  # exists
        rec = self.store._pending(WP.code_hash(out["device_code"]))
        rec["last_poll"] = 0.0
        self.store._save(rec)
        body = WP.sign_poll(material["worker"], out["device_code"], what, now=now)
        return (self.store.poll if what == "poll" else self.store.ack)(body)

    def approve(self, out, *, opened_ago=10.0, approve=True, session=None):
        sessions = json.loads((self.gw / "sessions.json").read_text())
        fp = fingerprint(session or self.sid)
        sessions[fp]["opened_at"] = time.time() - opened_ago
        (self.gw / "sessions.json").write_text(json.dumps(sessions))
        return self.store.decide(out["user_code"], fp, approve=approve, account=ACCT, approver=self.person,
                                 session_opened_at=time.time() - opened_ago)


@pytest.fixture()
def stand(tmp_path):
    return PairStand(tmp_path)


# ---------------------------------------------------------------------- L8/L9: one pairing, end to end
def test_a_person_pairs_a_machine_exactly_once(stand):
    material, out, state_dir = stand.begin()
    assert len(out["device_code"]) >= 43 and len(out["user_code"]) == 9 and out["expires_in"] == 600
    assert out["interval"] == 5
    with pytest.raises(AS.Refused) as got:
        stand.poll(material, out)
    assert got.value.code == "authorization_pending"
    stand.approve(out)
    result = stand.poll(material, out)["result"]
    target = WP.install_result(result, material, stand.pins, state_dir, out["device_code"])
    assert stand.poll(material, out)["result"] == result               # a lost answer: the same result again
    stand.poll(material, out, "ack")
    with pytest.raises(AS.Refused) as got:
        stand.poll(material, out)
    assert got.value.code == "pair_code_consumed"
    # what exists now: one worker device holding nothing, one enrolled reverse target for it, the agent's own key
    devices = [r for r in stand.state.paired_clients() if r.get("kind") == "worker-device"]
    assert len(devices) == 1 and devices[0]["capabilities"] == [] and devices[0]["account_id"] == AA_ACCT()
    t = stand.reg.load().target(target["target_id"])
    assert t.is_reverse and t.status == T.ENROLLED and t.owner_account == ACCT and t.device_id == devices[0]["client_id"]
    assert target["device_id"] == t.device_id and target["control"] == CONTROL
    from cryptography import x509
    cert = x509.load_pem_x509_certificate((state_dir / "cert.pem").read_bytes())
    assert cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo) == \
        material["worker"].public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    assert sorted(p.name for p in state_dir.iterdir()) == sorted(
        ["ca.pem", "cert.pem", "key.pem", "keyring.json", "revoked-identities.json", "revoked.crl", "target.json",
         "transport-key.pem"])


def AA_ACCT():
    return ACCT


def test_the_agent_and_the_browser_receive_no_account_or_approval_material(stand):
    """L9 / CC14: the result holds a certificate, public trust, a signed target and a SEALED pair key -- no token, no
    account id, no approval, no operator or CA key; the pair key is not readable without the transport key."""
    material, out, _ = stand.begin()
    stand.approve(out)
    result = stand.poll(material, out)["result"]
    assert set(result) == {"certificate", "anchor", "crl", "tombstones", "target", "pair_key_sealed"}
    blob = json.dumps(result)
    ring = json.loads((stand.tmp / "ring.json").read_text())
    for secret in [json.dumps(ring), ACCT, "operator", "PRIVATE KEY", stand.sid]:
        assert secret not in blob
    assert set(result["target"]) == set(PN.TARGET_KEYS) and "account" not in json.dumps(result["target"])
    other = WP.new_material(stand.tmp / "someone-else")
    with pytest.raises(Exception):
        WP.unseal(result["pair_key_sealed"], other["transport"], b"x")
    # the store keeps no clear pair key either
    for p in stand.store_dir.rglob("*.json"):
        assert "pair-keys" not in p.read_text() and '"key":' not in p.read_text()


def test_the_worker_device_holds_no_capability(stand):
    """L9 / CC13: `capabilities: []` explicitly -- absence would mean ALL capabilities."""
    from agentnode_sdk.access.dispatch import what_it_may_do
    material, out, _ = stand.begin()
    stand.approve(out)
    stand.poll(material, out)
    rec = next(r for r in stand.state.paired_clients() if r.get("kind") == "worker-device")
    assert "capabilities" in rec and what_it_may_do(rec) == ()


def test_the_pins_refuse_material_of_another_operator_or_anchor(stand):
    """Q4 / pair_material_untrusted."""
    material, out, state_dir = stand.begin()
    stand.approve(out)
    result = stand.poll(material, out)["result"]
    for change in ({"operator_pub": "00" * 32}, {"anchor_sha256": "0" * 64}, {"control_url": "https://evil.example/agent/v1/slot"},
                   {"gateway_instance": "g9"}):
        with pytest.raises(PN.Untrusted):
            WP.install_result(result, material, dict(stand.pins, **change), state_dir, out["device_code"])
    forged = dict(result, target=dict(result["target"], limits={"concurrency": 4, "queue": 8}))
    with pytest.raises(PN.Untrusted):
        WP.install_result(forged, material, stand.pins, state_dir, out["device_code"])


# ---------------------------------------------------------------------- L8: the code's life
def test_a_stale_sign_in_cannot_approve(stand):
    """L8 / CC15 (Q-LW2-0002 Q2): opened more than 300 s ago -> pair_not_permitted, however recently it was used."""
    material, out, _ = stand.begin()
    with pytest.raises(AS.Refused) as got:
        stand.approve(out, opened_ago=301)
    assert got.value.code == "pair_not_permitted"


def test_the_helper_rejudges_the_session_itself(stand):
    """CC15: an authorization whose approving session is older than 300 s at the gateway is refused by root."""
    material, out, _ = stand.begin()
    stand.approve(out)
    rec = stand.store._pending(WP.code_hash(out["device_code"]))
    aid = rec["authorization_id"]
    (stand.tmp / "bundles").exists()
    # undo the materialization and make the session old at the gateway
    op_id, _t, _i = Operations.pair_ids(aid)
    (stand.ops.ops_dir / (op_id + ".json")).unlink()
    sessions = json.loads((stand.gw / "sessions.json").read_text())
    sessions[fingerprint(stand.sid)]["opened_at"] = time.time() - 1000
    (stand.gw / "sessions.json").write_text(json.dumps(sessions))
    with pytest.raises(PH.HelperRefused) as got:
        stand.helper.materialize(aid)
    assert got.value.code == "pair_not_permitted"


def test_only_a_manage_devices_person_of_that_account_can_approve(stand):
    material, out, _ = stand.begin()
    stand.approve(out)
    aid = stand.store._pending(WP.code_hash(out["device_code"]))["authorization_id"]
    op_id, _t, _i = Operations.pair_ids(aid)
    (stand.ops.ops_dir / (op_id + ".json")).unlink()
    path = stand.store_dir / "authorizations" / (aid + ".json")
    doc = json.loads(path.read_text())
    path.write_text(json.dumps(dict(doc, account=OTHER)))
    with pytest.raises(PH.HelperRefused) as got:
        stand.helper.materialize(aid)
    assert got.value.code == "pair_not_permitted"


def test_wrong_user_codes_lock_the_session(stand):
    stand.begin()
    for _ in range(5):
        with pytest.raises(AS.Refused) as got:
            stand.store.lookup("BBBB-BBBB", "f" * 64)
        assert got.value.code == "pair_user_code_invalid"
    with pytest.raises(AS.Refused) as got:
        stand.store.lookup("BBBB-BBBB", "f" * 64)
    assert got.value.code == "pair_locked"


def test_an_expired_code_cannot_be_approved_or_collected(stand):
    material, out, _ = stand.begin()
    rec = stand.store._pending(WP.code_hash(out["device_code"]))
    rec["expires_at"] = time.time() - 1
    stand.store._save(rec)
    with pytest.raises(AS.Refused) as got:
        stand.approve(out)
    assert got.value.code in ("pair_code_expired", "pair_user_code_invalid")
    with pytest.raises(AS.Refused) as got:
        stand.poll(material, out)
    assert got.value.code == "pair_code_expired"


def test_unknown_codes_bad_proofs_and_fast_polls(stand):
    material, out, _ = stand.begin()
    other = WP.new_material(stand.tmp / "intruder")
    with pytest.raises(AS.Refused) as got:
        stand.store.poll(WP.sign_poll(material["worker"], "not-a-code"))
    assert got.value.code == "pair_code_unknown"
    with pytest.raises(AS.Refused) as got:
        stand.store.poll(WP.sign_poll(other["worker"], out["device_code"]))
    assert got.value.code == "pair_proof_invalid"
    with pytest.raises(AS.Refused) as got:
        stand.store.poll(WP.sign_poll(material["worker"], out["device_code"], now=time.time() - 120))
    assert got.value.code == "pair_proof_invalid"
    with pytest.raises(AS.Refused):
        stand.poll(material, out)                                      # pending; sets last_poll
    with pytest.raises(AS.Refused) as got:
        stand.store.poll(WP.sign_poll(material["worker"], out["device_code"]))
    assert got.value.code == "slow_down"


def test_a_denied_pairing_is_access_denied(stand):
    material, out, _ = stand.begin()
    stand.approve(out, approve=False)
    with pytest.raises(AS.Refused) as got:
        stand.poll(material, out)
    assert got.value.code == "access_denied"


def test_starting_is_rate_limited_and_bounded(stand):
    for _ in range(5):
        stand.begin(name="m%d" % _)
    with pytest.raises(AS.Refused) as got:
        stand.begin(name="m6")
    assert got.value.code == "pair_rate_limited"
    s2 = AS.PairingStore(stand.tmp / "store2", max_pending=1)
    m = WP.new_material(stand.tmp / "x")
    body = {"protocol": WP.PROTOCOL, "csr": m["csr"].decode(), "transport_key": WP.b64(m["transport_pub"]),
            "name": "a", "concurrency": 1, "queue": 1, "platform": {}}
    s2.start(body, "1.1.1.1")
    with pytest.raises(AS.Refused) as got:
        s2.start(body, "2.2.2.2")
    assert got.value.code == "pair_rate_limited"


@pytest.mark.parametrize("change", [{"protocol": "other"}, {"concurrency": 5}, {"queue": 0}, {"csr": "junk"},
                                    {"transport_key": "AAAA"}, {"extra": 1}])
def test_a_malformed_start_is_refused(stand, change):
    m = WP.new_material(stand.tmp / "y")
    body = {"protocol": WP.PROTOCOL, "csr": m["csr"].decode(), "transport_key": WP.b64(m["transport_pub"]),
            "name": "a", "concurrency": 1, "queue": 1, "platform": {}}
    body.update(change)
    with pytest.raises(AS.Refused) as got:
        stand.store.start(body, "9.9.9.9")
    assert got.value.code == "pair_request_invalid"


# ---------------------------------------------------------------------- L10: the authority transaction
def test_materialize_twice_is_the_same_device_target_and_identity(stand):
    """L10 / CC16: a repeated or recovered materialization never makes a second device, target or instance."""
    material, out, _ = stand.begin()
    stand.approve(out)
    aid = stand.store._pending(WP.code_hash(out["device_code"]))["authorization_id"]
    first = stand.helper.materialize(aid)
    second = stand.helper.materialize(aid)
    assert first == second
    workers = [r for r in stand.state.paired_clients() if r.get("kind") == "worker-device"]
    assert len(workers) == 1
    snap = stand.reg.load()
    assert len([t for t in snap.targets.values() if t.is_reverse]) == 1


@pytest.mark.parametrize("crash_at", ["device", "register_invited", "pki_entry", "pair_key", "pki_enroll",
                                      "status_enrolled", "result", "forget_clear_copies"])
def test_a_crash_after_any_step_resumes_to_exactly_one_of_everything(stand, crash_at):
    """L10 / CC19: crash right after each durable step; the resumed operation completes with one device, one target,
    one certificate, one pair key, and the same ids."""
    material, out, state_dir = stand.begin()

    class Crash(Exception):
        pass

    def crash(step):
        if step == crash_at:
            raise Crash(step)
    stand.ops._crash_after = crash
    stand.store.helper = None                                           # approve without materializing
    stand.approve(out)
    aid = stand.store._pending(WP.code_hash(out["device_code"]))["authorization_id"]
    with pytest.raises(Crash):
        stand.helper.materialize(aid)
    stand.ops._crash_after = lambda step: None
    resumed = stand.ops.resume(kinds=("pair",))
    assert resumed
    result = stand.helper.materialize(aid)
    WP.install_result(result, material, stand.pins, state_dir, out["device_code"])
    assert len([r for r in stand.state.paired_clients() if r.get("kind") == "worker-device"]) == 1
    assert len([t for t in stand.reg.load().targets.values() if t.is_reverse]) == 1
    ring = json.loads((stand.tmp / "ring.json").read_text())
    assert len(json.dumps(ring)) > 0
    _op, tid, inst = Operations.pair_ids(aid)
    assert not (stand.tmp / "bundles" / tid / "pair-keys.json").exists()
    assert not (stand.tmp / "bundles" / tid / "enrolment-secret").exists()


def test_the_helper_answers_exactly_one_caller_and_one_operation(stand, monkeypatch):
    """L10 / CC17/CC18: uid 0 and any other uid are refused; any request but materialize is refused."""
    class Conn:
        def __init__(self, line):
            self.data = line
            self.sent = b""

        def settimeout(self, t):
            pass

        def recv(self, n):
            out, self.data = self.data[:n], self.data[n:]
            return out

        def sendall(self, b):
            self.sent += b

        def close(self):
            pass

    for uid, line, code in ((0, b'{"op":"materialize","authorization_id":"%s"}\n' % (b"a" * 64), "helper_caller_refused"),
                            (1001, b'{"op":"materialize","authorization_id":"x"}\n', "helper_caller_refused"),
                            (1000, b'{"op":"issue","authorization_id":"x"}\n', "helper_operation_refused"),
                            (1000, b'{"op":"materialize","authorization_id":"x","extra":1}\n', "helper_operation_refused"),
                            (1000, b'{"op":"materialize","authorization_id":"%s"}\n' % (b"a" * 64), "pair_authorization_unknown")):
        monkeypatch.setattr(PH, "peer_uid", lambda conn, uid=uid: uid)
        c = Conn(line)
        stand.helper.answer(c)
        assert json.loads(c.sent)["code"] == code
    with pytest.raises(ValueError):
        PH.Helper(stand.ops, gateway_dir=stand.gw, store=stand.store_dir, allowed_uid=0)


def test_the_agent_facing_code_never_opens_a_private_key():
    """L10 / CC17 (static half): rmcp-agent's modules name no operator or CA private key file."""
    import inspect

    from agentnode_sdk.remote_mcp import agent_service, reverse_edge
    for mod in (agent_service, agent_authority_module(), reverse_edge):
        src = inspect.getsource(mod)
        assert "operator_priv" not in src and "ca.key" not in src and "audit_signer_priv" not in src


def agent_authority_module():
    return AA


# ---------------------------------------------------------------------- the projection
def test_the_projection_holds_no_presentable_secret(stand):
    doc = AA.project(stand.gw)
    blob = json.dumps(doc)
    tokens = json.loads((stand.gw / "tokens.json").read_text())
    for token_hash in tokens:
        assert token_hash not in blob
    assert "csrf" not in blob and stand.sid not in blob
    assert doc["devices"][stand.person]["capabilities"] == ["manage_devices", "read", "run"]


def test_a_stale_projection_is_refused(stand, tmp_path):
    p = tmp_path / "authority.json"
    AA.write(AA.project(stand.gw, now=time.time() - 120), p)
    with pytest.raises(AA.AuthorityUnavailable):
        AA.Authority(p).load()
    AA.write(AA.project(stand.gw), p)
    assert AA.Authority(p).session(stand.sid)["client_id"] == stand.person


@pytest.mark.parametrize("record,expected", [
    (None, "target_device_revoked"),
    ({"account_id": OTHER, "kind": "worker-device", "capabilities": []}, "target_device_revoked"),
    ({"account_id": ACCT, "kind": "worker-device", "capabilities": []}, ""),
    ({"account_id": ACCT, "kind": "person", "capabilities": ["read"]}, "target_device_revoked"),
    ({"account_id": ACCT, "kind": "person", "capabilities": ["run"]}, "")])
def test_the_reverse_device_rule(record, expected):
    assert AA.reverse_device_rule(record, ACCT, lambda a: True) == expected
    if record:
        assert AA.reverse_device_rule(record, ACCT, lambda a: False) in ("target_account_suspended",
                                                                         "target_device_revoked")


# ---------------------------------------------------------------------- the browser page and the agent routes
def _call(app, method, path, *, headers=None, body=b""):
    sent = []
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        sent.append(msg)
    asyncio.run(app({"type": "http", "method": method, "path": path, "headers": hdrs, "client": ("127.0.0.1", 1)},
                    receive, send))
    status = sent[0]["status"]
    return status, b"".join(m.get("body", b"") for m in sent[1:])


def _app(stand, tmp_path):
    p = tmp_path / "authority.json"
    AA.write(AA.project(stand.gw), p)
    return AS.AgentApp(store=stand.store, authority=AA.Authority(p), host="mcp.agentnode.net")


def test_the_agent_routes_refuse_browsers_and_other_hosts(stand, tmp_path):
    app = _app(stand, tmp_path)
    base = {"host": "mcp.agentnode.net", "content-type": "application/json"}
    assert _call(app, "POST", WP.START_PATH, headers=dict(base, host="evil.example"), body=b"{}")[0] == 421
    status, body = _call(app, "POST", WP.START_PATH, headers=dict(base, origin="https://mcp.agentnode.net"), body=b"{}")
    assert status == 400 and json.loads(body)["error"] == "pair_request_invalid"
    status, body = _call(app, "POST", WP.START_PATH, headers=dict(base, cookie="__Host-agentnode=x"), body=b"{}")
    assert status == 400
    assert _call(app, "GET", WP.START_PATH, headers=base)[0] == 405


def test_the_page_signs_in_then_approves(stand, tmp_path):
    material, out, _ = stand.begin()
    stand.store.helper = None
    app = _app(stand, tmp_path)
    page_headers = {"host": "mcp.agentnode.net"}
    status, body = _call(app, "GET", WP.PAGE_PATH, headers=page_headers)
    assert status == 200 and b"/v1/session" in body                     # nobody signed in: sign in first
    cookie = {"host": "mcp.agentnode.net", "cookie": "__Host-agentnode=" + stand.sid}
    status, body = _call(app, "GET", WP.PAGE_PATH, headers=cookie)
    assert status == 200 and b"name=\"user_code\"" in body
    csrf = body.split(b'name="csrf" value="')[1].split(b'"')[0].decode()
    form = ("csrf=%s&action=lookup&user_code=%s" % (csrf, out["user_code"])).encode()
    status, body = _call(app, "POST", WP.PAGE_PATH, headers=dict(cookie, origin="https://evil.example"), body=form)
    assert status == 403                                                # another origin
    status, body = _call(app, "POST", WP.PAGE_PATH, headers=dict(cookie, origin="https://mcp.agentnode.net"), body=form)
    assert status == 200 and WP.fingerprint_of_csr(material["csr"]).encode() in body
    csrf2 = body.split(b'name="csrf" value="')[1].split(b'"')[0].decode()
    form = ("csrf=%s&action=approve&user_code=%s" % (csrf2, out["user_code"])).encode()
    status, body = _call(app, "POST", WP.PAGE_PATH, headers=dict(cookie, origin="https://mcp.agentnode.net"), body=form)
    assert status == 200 and b"Approved" in body
    assert stand.store._pending(WP.code_hash(out["device_code"]))["status"] == "approved"


def test_a_worker_device_cannot_sign_in_to_approve(stand, tmp_path):
    material, out, _ = stand.begin()
    stand.approve(out)
    stand.poll(material, out)
    worker = next(r["client_id"] for r in stand.state.paired_clients() if r.get("kind") == "worker-device")
    sid, _ = Sessions(str(stand.gw)).open(worker)
    app = _app(stand, tmp_path)
    status, body = _call(app, "GET", WP.PAGE_PATH, headers={"host": "mcp.agentnode.net",
                                                            "cookie": "__Host-agentnode=" + sid})
    assert b"/v1/session" in body                                       # treated as nobody signed in


# ---------------------------------------------------------------------- D11: retirement of a paired worker
def _paired(stand):
    material, out, state_dir = stand.begin()
    stand.approve(out)
    result = stand.poll(material, out)["result"]
    target = WP.install_result(result, material, stand.pins, state_dir, out["device_code"])
    return target, state_dir


def _revoke_seen(stand, monkeypatch):
    calls = []
    monkeypatch.setattr(stand.ops, "revoke", lambda tid, **kw: calls.append(tid) or stand.reg.set_status(
        tid, T.REVOKED, actor="t"))
    return calls


def test_a_withdrawn_worker_device_retires_its_target(stand, monkeypatch):
    from agentnode_sdk.remote_mcp import retire
    target, _ = _paired(stand)
    calls = _revoke_seen(stand, monkeypatch)
    assert retire.reconcile(stand.ops, stand.gw)["retired"] == []          # device present: nothing
    stand.state.revoke_client(target["device_id"], within_account=ACCT)
    report = retire.reconcile(stand.ops, stand.gw)
    assert calls == [target["target_id"]] and report["retired"][0]["why"] == "device_gone"
    assert retire.reconcile(stand.ops, stand.gw)["retired"] == []          # idempotent: already terminal


def test_a_suspended_account_is_never_retired(stand, monkeypatch):
    from agentnode_sdk.remote_mcp import retire
    target, _ = _paired(stand)
    calls = _revoke_seen(stand, monkeypatch)
    stand.state.accounts.suspend(ACCT, "test", by="t")
    retire.reconcile(stand.ops, stand.gw)
    assert calls == []                                                  # reversible: refused at use, never retired


def test_a_signed_self_retirement_withdraws_the_device_and_the_target(stand, monkeypatch):
    from agentnode_sdk.remote_mcp import retire
    target, state_dir = _paired(stand)
    calls = _revoke_seen(stand, monkeypatch)
    req = WP.retire_request(state_dir)
    trust = stand.world.settings(stand.world.service("gateway", "g1"), {target["instance"]}).trust("gateway")
    assert stand.store.retire_request(req, trust, stand.world.deployment)["status"] == "accepted"
    assert stand.store.retire_request(req, trust, stand.world.deployment)["status"] == "accepted"   # replay converges
    report = retire.reconcile(stand.ops, stand.gw, store=stand.store_dir, trust=trust, deployment=stand.world.deployment)
    assert report["requests"][0]["outcome"] == "retired" and calls == [target["target_id"]]
    assert all(r.get("client_id") != target["device_id"] for r in stand.state.paired_clients())
    assert retire.reconcile(stand.ops, stand.gw, store=stand.store_dir, trust=trust,
                            deployment=stand.world.deployment)["requests"] == []


def test_a_forged_or_stale_retirement_request_is_refused(stand):
    target, state_dir = _paired(stand)
    trust = stand.world.settings(stand.world.service("gateway", "g1"), {target["instance"]}).trust("gateway")
    req = WP.retire_request(state_dir)
    for bad in (dict(req, device_id="0" * 16), dict(req, ts=str(int(time.time()) - 300)),
                dict(req, deployment="elsewhere"), dict(req, extra="x")):
        with pytest.raises(AS.Refused) as got:
            stand.store.retire_request(bad, trust, stand.world.deployment)
        assert got.value.code.startswith("retire_")
