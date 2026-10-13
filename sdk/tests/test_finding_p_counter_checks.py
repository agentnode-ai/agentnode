"""MEASUREMENT ONLY, never to merge: run finding P's per-lock counter-checks in Linux CI.

The counter-check tool (a1e_review/open-findings/tools/p_counter_checks.py) is reproduced here as one test so
the CI, which runs pytest against the repo, can exercise it. It copies the checked-out sdk tree, reverts one
lock at a time to its pathname form, runs the P test file, and asserts that exactly that lock's P1 case goes red
again. POSIX only, like the P tests themselves.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from agentnode_sdk.gateway import securedir

pytestmark = pytest.mark.skipif(not securedir.SUPPORTED, reason="the P tests it drives are POSIX only")

SDK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDENTITY = os.path.join("agentnode_sdk", "gateway", "identity.py")
TESTS = "tests/test_finding_p_a_pathname_lock.py"
P1 = "test_p1_a_swapped_path_loses_a_mutation"
ACCOUNTS_OLD = "            guard=lambda: self._private_lock(_accounts.ACCOUNTS_NAME, self.TOKENS_LOCK_SECONDS),\n"
ACCOUNTS_NEW = "            guard=lambda: filelock.ProcessLock(self.root / _accounts.ACCOUNTS_NAME),\n"
STORE_LOCK = "            lock=lambda name: self._private_lock(name, self.TOKENS_LOCK_SECONDS),\n"


def _red_set(throwaway):
    report = os.path.join(throwaway, "p.xml")
    if os.path.exists(report):
        os.remove(report)
    subprocess.run([sys.executable, "-m", "pytest", TESTS, "-q", "--no-header", "--color=no",
                    "-p", "no:cacheprovider", "--junitxml", report], cwd=throwaway, capture_output=True, text=True)
    red = set()
    for case in ET.parse(report).getroot().iter("testcase"):
        if (list(case.findall("failure")) or list(case.findall("error"))) and P1 in case.get("name"):
            red.add(case.get("name").split("::")[-1])
    return red


def _throwaway(tmp_path):
    dst = str(tmp_path / "sdk")
    shutil.copytree(SDK, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.egg-info", ".git"))
    return dst


def _revert(dst, old, new):
    target = os.path.join(dst, IDENTITY)
    text = io.open(target, encoding="utf-8", newline="").read()
    assert text.count(old) == 1, "cannot land: %r appears %d times" % (old[:40], text.count(old))
    before = hashlib.sha256(text.encode()).hexdigest()
    io.open(target, "w", encoding="utf-8", newline="").write(text.replace(old, new, 1))
    after = hashlib.sha256(io.open(target, "rb").read()).hexdigest()
    assert after != before, "the mutation did not land"


def test_accounts_lock_reverted_reds_only_accounts(tmp_path):
    dst = _throwaway(tmp_path)
    _revert(dst, ACCOUNTS_OLD, ACCOUNTS_NEW)
    red = _red_set(dst)
    print("accounts lock reverted: P1 red = %s" % sorted(red))
    assert red == {"%s[accounts]" % P1}, "expected only the accounts case red, got %s" % sorted(red)


def test_store_lock_removed_reds_both_counters(tmp_path):
    dst = _throwaway(tmp_path)
    _revert(dst, STORE_LOCK, "")
    red = _red_set(dst)
    print("store lock removed: P1 red = %s" % sorted(red))
    assert red == {"%s[throttle]" % P1, "%s[budget]" % P1}, (
        "expected both counter cases red, got %s" % sorted(red))
