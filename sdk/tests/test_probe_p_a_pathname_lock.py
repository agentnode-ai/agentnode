"""MEASUREMENT ONLY (finding P), never to be merged: is the accounts lock, opened by PATHNAME, the same lock as the
directory the accounts file is read and written through (by DESCRIPTOR)?

POSIX only, because the descriptor-relative state exists only there. Two processes hold one gateway directory.
A takes the accounts lock, reads, and pauses. Then the directory at the path is swapped for another one, B takes
"the" accounts lock -- by path, so in the other directory -- and the original directory is swapped back before
B reads, so B's identity check (`_fd`, same_object) passes. B suspends one account and finishes. Then A finishes
suspending another.

P1 confirms the defect if B entered while A held the lock, and B's answered suspension is gone afterwards.
P0 is the control without any swap: B must wait for A, and both suspensions must stand.

What the swaps model is what `securedir` exists to defend against: a path rebound under a running gateway
(EM3C-EXTERNAL-0004/0007). This probe only measures; it changes nothing.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="descriptor-relative state exists only on POSIX")

WORKER = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
root, role, ctl, account = sys.argv[2:6]
from agentnode_sdk.gateway import accounts as A
from agentnode_sdk.gateway import filelock
from agentnode_sdk.gateway.identity import GatewayState

def mark(name):
    open(os.path.join(ctl, name), "w").close()

def wait(name, limit=30.0):
    end = time.monotonic() + limit
    while not os.path.exists(os.path.join(ctl, name)):
        if time.monotonic() > end:
            raise SystemExit("timed out waiting for " + name)
        time.sleep(0.01)

state = GatewayState(root, version="probe")
real_load, real_enter = A.Accounts._load, filelock.ProcessLock.__enter__
seen = {"loads": 0}

def load(self):
    body = real_load(self)
    if role == "A" and seen["loads"] == 0:
        seen["loads"] = 1
        mark("A-read")
        wait("A-go")
    return body

def enter(self):
    got = real_enter(self)
    if role == "B" and str(self.path).endswith("accounts.json.lock"):
        mark("B-locked")
        wait("B-continue")
    return got

A.Accounts._load = load
filelock.ProcessLock.__enter__ = enter
if role == "B":
    mark("B-ready")
    wait("B-go")
try:
    state.accounts.suspend(account, "probe " + role)
    print("OK")
except Exception as exc:
    print("ERR %s %s" % (type(exc).__name__, exc))
'''


def _setup(tmp_path):
    from agentnode_sdk.gateway.identity import GatewayState

    root = str(tmp_path / "gw")
    s = GatewayState(root, version="probe")
    one = s.accounts.create(name="one").account_id
    two = s.accounts.create(name="two").account_id
    s.close()
    ctl = tmp_path / "ctl"
    ctl.mkdir()
    return root, str(ctl), one, two


def _start(sdk, root, role, ctl, account):
    return subprocess.Popen([sys.executable, "-c", WORKER, sdk, root, role, ctl, account],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _wait(ctl, name, limit=30.0):
    end = time.monotonic() + limit
    while not os.path.exists(os.path.join(ctl, name)):
        assert time.monotonic() < end, "timed out waiting for " + name
        time.sleep(0.01)


def _suspended(root):
    from agentnode_sdk.gateway.identity import GatewayState

    s = GatewayState(root, version="probe")
    try:
        return {a.name: a.state for a in s.accounts.all()}
    finally:
        s.close()


SDK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_p0_control_without_a_swap_b_waits_and_both_stand(tmp_path):
    root, ctl, one, two = _setup(tmp_path)
    a = _start(SDK, root, "A", ctl, one)
    _wait(ctl, "A-read")
    b = _start(SDK, root, "B", ctl, two)
    _wait(ctl, "B-ready")
    open(os.path.join(ctl, "B-go"), "w").close()
    open(os.path.join(ctl, "B-continue"), "w").close()
    time.sleep(1.0)
    b_entered_while_a_held = os.path.exists(os.path.join(ctl, "B-locked"))
    open(os.path.join(ctl, "A-go"), "w").close()
    out_a, out_b = a.communicate(timeout=60)[0].strip(), b.communicate(timeout=60)[0].strip()
    states = _suspended(root)
    print("P0: B entered while A held the lock: %s; A %r; B %r; accounts %s"
          % (b_entered_while_a_held, out_a, out_b, states))
    assert not b_entered_while_a_held
    assert states == {"one": "suspended", "two": "suspended"}


def test_p1_a_swapped_path_lets_two_writers_in_and_loses_one(tmp_path):
    root, ctl, one, two = _setup(tmp_path)
    a = _start(SDK, root, "A", ctl, one)
    _wait(ctl, "A-read")
    b = _start(SDK, root, "B", ctl, two)
    _wait(ctl, "B-ready")
    # Swap the directory at the path for another one, so a lock opened BY PATH lands elsewhere.
    os.rename(root, root + ".held")
    os.mkdir(root, 0o700)
    open(os.path.join(ctl, "B-go"), "w").close()
    _wait(ctl, "B-locked")
    # B holds "the" lock -- in the other directory. Swap the original back before B reads.
    os.rename(root, root + ".other")
    os.rename(root + ".held", root)
    open(os.path.join(ctl, "B-continue"), "w").close()
    out_b = b.communicate(timeout=60)[0].strip()
    open(os.path.join(ctl, "A-go"), "w").close()
    out_a = a.communicate(timeout=60)[0].strip()
    states = _suspended(root)
    print("P1: B %r while A held its lock; A %r; accounts %s" % (out_b, out_a, states))
    assert not (out_b == "OK" and out_a == "OK" and states.get("two") != "suspended"), (
        "two writers held 'the' accounts lock at once and B's answered suspension was lost: %s" % states)
