"""Finding P: a lock that guards a file read and written through the held directory descriptor must belong to that
directory, not to its pathname.

`accounts.json`, the pairing throttle and the admission budget each read and write through the gateway's verified
directory descriptor, but lock by pathname. When the path is rebound under a running gateway -- which the descriptor
discipline (EM3C-EXTERNAL-0004/0007) exists to survive -- two holders take two different lock files and neither
excludes the other, so one answered mutation is lost. Measured on main in Linux CI (a1e_review/open-findings,
E0005, for accounts).

POSIX only: descriptor-relative state and directory rebinding need it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from agentnode_sdk.gateway import securedir

pytestmark = pytest.mark.skipif(
    not securedir.SUPPORTED, reason="descriptor-relative state and directory rebinding require POSIX")

SDK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# One worker, for every file. It holds the gateway directory by descriptor, and -- for role A only -- pauses its
# one operation between the read and the write of that operation's critical section, announcing each step through
# files in a control directory so the test drives the race deterministically.
WORKER = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
root, role, ctl, what, arg = sys.argv[2:7]
from agentnode_sdk.gateway.identity import GatewayState

def mark(n): open(os.path.join(ctl, n), "w").close()
def wait(n, limit=30.0):
    end = time.monotonic() + limit
    while not os.path.exists(os.path.join(ctl, n)):
        if time.monotonic() > end: raise SystemExit("timeout waiting for " + n)
        time.sleep(0.01)

state = GatewayState(root, version="probe")
first = {"paused": False}

def pause_after_read(owner, name):
    real = getattr(owner, name)
    def hooked(self, *a, **k):
        r = real(self, *a, **k)
        if role == "A" and not first["paused"]:
            first["paused"] = True
            mark("A-read"); wait("A-go")
        return r
    setattr(owner, name, hooked)

if what == "accounts":
    from agentnode_sdk.gateway import accounts as A
    pause_after_read(A.Accounts, "_load")
    op = lambda: state.accounts.suspend(arg, "probe " + role)
elif what == "throttle":
    from agentnode_sdk.gateway.throttle import Throttle
    pause_after_read(Throttle, "_read_locked")
    op = lambda: state.a_pairing_attempt_failed()
elif what == "budget":
    from agentnode_sdk.gateway.throttle import Budget
    pause_after_read(Budget, "_read")
    op = lambda: state.spend_a_pairing_attempt()

if role == "B":
    mark("B-ready"); wait("B-go")
try:
    op(); print("OK")
except Exception as exc:
    print("ERR %s %s" % (type(exc).__name__, exc))
state.close()
'''


def _wait(ctl, name, limit=30.0):
    end = time.monotonic() + limit
    while not os.path.exists(os.path.join(ctl, name)):
        assert time.monotonic() < end, "timed out waiting for " + name
        time.sleep(0.01)


def _gw(root):
    from agentnode_sdk.gateway.identity import GatewayState
    return GatewayState(root, version="probe")


def _setup(tmp_path, what):
    """A fresh established gateway directory, a control directory, and -- for accounts -- the two account ids the
    two roles each suspend."""
    root = str(tmp_path / "gw")
    s = _gw(root)
    s.identity                                           # establishes identity, throttle and budget markers
    a_id = b_id = ""
    if what == "accounts":
        a_id = s.accounts.create(name="A-target").account_id
        b_id = s.accounts.create(name="B-target").account_id
    s.close()
    ctl = str(tmp_path / "ctl")
    os.mkdir(ctl)
    return root, ctl, a_id, b_id


def _count(root, what):
    """The number that falls by one if an answered mutation was lost, read fresh from disk."""
    if what == "accounts":
        s = _gw(root)
        try:
            return sum(1 for a in s.accounts.all() if a.state == "suspended")
        finally:
            s.close()
    name = "pairing-throttle.json" if what == "throttle" else "pairing-admission.json"
    body = json.loads(open(os.path.join(root, name), encoding="utf-8").read())
    return len(body.get("failures" if what == "throttle" else "spent") or [])


def _start(root, role, ctl, what, arg):
    return subprocess.Popen([sys.executable, "-c", WORKER, SDK, root, role, ctl, what, arg],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _race(tmp_path, what, swap):
    root, ctl, a_id, b_id = _setup(tmp_path, what)
    a = _start(root, "A", ctl, what, a_id)
    _wait(ctl, "A-read")                                 # A has read and is paused inside its critical section
    b = _start(root, "B", ctl, what, b_id)
    _wait(ctl, "B-ready")
    if swap:
        os.rename(root, root + ".held")
        os.mkdir(root, 0o700)                            # a different directory now sits at the pathname
    open(os.path.join(ctl, "B-go"), "w").close()
    time.sleep(0.5)                                      # let B take "the" lock and read
    b_done_before_a = b.poll() is not None
    if swap:
        os.rename(root, root + ".other")
        os.rename(root + ".held", root)                 # the real directory is back for B's write and A's
    open(os.path.join(ctl, "A-go"), "w").close()
    out_a = a.communicate(timeout=60)[0].strip()
    out_b = b.communicate(timeout=60)[0].strip()
    return root, out_a, out_b, b_done_before_a


def test_p0_accounts_control_without_a_swap_both_stand(tmp_path):
    root, out_a, out_b, b_done_before_a = _race(tmp_path, "accounts", swap=False)
    print("P0 accounts: A %r B %r; B finished before A was released: %s; suspended %d"
          % (out_a, out_b, b_done_before_a, _count(root, "accounts")))
    assert not b_done_before_a, "B passed the lock while A held it, with no swap"
    assert out_a == "OK" and out_b == "OK"
    assert _count(root, "accounts") == 2


@pytest.mark.parametrize("what", ["accounts", "throttle", "budget"])
def test_p1_a_swapped_path_loses_a_mutation(tmp_path, what):
    root, out_a, out_b, _ = _race(tmp_path, what, swap=True)
    count = _count(root, what)
    print("P1 %s: A %r B %r; count %d" % (what, out_a, out_b, count))
    assert out_a == "OK" and out_b == "OK", "a worker failed: A %r B %r" % (out_a, out_b)
    assert count == 2, "two answered %s mutations, but the count is %d (one was lost)" % (what, count)
