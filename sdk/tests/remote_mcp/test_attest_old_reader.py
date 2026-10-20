"""Rollback compatibility (attest-renewal C1, C18; counter-check 31): the UNMODIFIED register code of the
live release a36ddcdd loads and verifies -- signature, every history link, the pinned head -- a register
the head has written through renewals (equivalent and not), a block of the managed default and the
operator's way back.

`data/targets_a36ddcdd.py.txt` is that release's `remote_mcp/targets.py`, byte for byte; its git blob id is
pinned below, so the copy cannot drift from the release it stands for."""
from __future__ import annotations

import copy
import hashlib
import types
from pathlib import Path

from agentnode_sdk.remote_mcp import targets as T

from .target_fixtures import activate, customer_spec, make_register, raw_pub

PARENT_BLOB = "9b89d634fa097e8e5b2c9d7a381e7a80136a35ee"      # a36ddcdd:sdk/agentnode_sdk/remote_mcp/targets.py


def _old_module():
    raw = (Path(__file__).parent / "data" / "targets_a36ddcdd.py.txt").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest() == PARENT_BLOB
    import sys
    mod = types.ModuleType("targets_a36ddcdd")
    sys.modules[mod.__name__] = mod                            # dataclasses look their module up
    exec(compile(raw.decode("utf-8"), "targets_a36ddcdd.py", "exec"), mod.__dict__)   # noqa: S102 - pinned source
    return mod


def _renewed(rec, *, platform=None):
    new = copy.deepcopy(rec)
    new["measured_at"] = rec["measured_at"] + 60
    new["expires_at"] = rec["expires_at"] + 60
    new["challenge_nonces"] = ["eg-" + hashlib.sha256(str(new["measured_at"]).encode()).hexdigest()[:24]]
    if platform:
        new["properties"] = dict(new["properties"], reported_platform={"image_digest": platform})
    new["record_digest"] = T.digest({k: new[k] for k in sorted(new) if k != "record_digest"})
    return new


def test_the_old_release_reads_everything_the_head_writes(tmp_path):
    reg, op = make_register(tmp_path / "stand")
    reg.add_customer(customer_spec("cust-a", "acct-00000000000000a1", "c1"), actor="t")
    activate(reg, "cust-a")

    def start(tid):
        t = reg.load().target(tid)
        return {"status": t.status, "identity_uri": t.identity_uri, "endpoint_digest": t.endpoint_digest,
                "record_digest": t.attestation["record_digest"]}
    for tid in ("managed-w1", "cust-a"):                       # equivalent renewals
        reg.renew_attestation(tid, _renewed(reg.load().target(tid).attestation), start(tid), actor="t")
    reg.renew_attestation("managed-w1", _renewed(reg.load().target("managed-w1").attestation,
                                                 platform="sha256:" + "1" * 64), start("managed-w1"), actor="t")
    reg.block("managed-w1", actor="t", why="test: positive evidence")
    reg.set_attestation("managed-w1", _renewed(reg.load().target("managed-w1").attestation), actor="t")
    reg.set_status("managed-w1", T.ACTIVE, actor="t")         # the operator's probe brought it back
    reg.block("cust-a", actor="t", why="test")
    head = reg.load()

    old = _old_module()
    snap = old.Register(reg.stand, raw_pub(op), pin_clock=False).load()   # verifies the whole chain
    assert snap.generation == head.generation and snap.digest == head.digest
    assert snap.target("managed-w1").status == T.ACTIVE and snap.target("cust-a").status == T.SUSPENDED
    assert snap.target("managed-w1").attestation == head.target("managed-w1").attestation
    for g in range(1, head.generation + 1):                    # and every generation on its own
        errs = old.validate_document(old.json.loads((Path(reg.stand) / "targets" / "history" / ("%d.json" % g)).read_text()))
        assert errs == [], (g, errs)
