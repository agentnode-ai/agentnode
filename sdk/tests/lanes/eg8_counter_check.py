"""Revert the request-time screening and require the rebinding test to go red for its own phrase.

WHY THIS IS IN THE REPOSITORY AND RUN BY THE LANE. The rebinding test needs a container runtime and a
resolver that can change its mind, so on a workstation without them it skips -- and a skip is not a red
test. A counter-check that can only ever be skipped establishes nothing, so the mutation is driven where the
arrangement exists: here, in the lane that makes it.

WHAT IS MUTATED. `screen_addrinfos` is the function that refuses an address the proxy may not reach. This
replaces the screened result with a pass-through of whatever was resolved, which is precisely the protection
EG8 is about. With it gone, a name that has started pointing at a private address is tunnelled to, and the
test's second leg must fail saying `the second leg was not screened`.

WHAT MAKES IT A CHECK RATHER THAN A GESTURE, in order:

  * the test must be GREEN before the mutation, or a red afterwards says nothing about the mutation;
  * the mutation must be shown to have LANDED, by the file's digest changing and the new text being there;
  * the failure must contain the PREDICTED phrase, read only from pytest's own failure lines with colour
    stripped -- a phrase matched anywhere in the output could be the source echoed inside a traceback, and a
    colourised failure line does not begin with `E `;
  * the file must be restored BYTE-EXACTLY, proved by the digest it started with.

usage:  python tests/lanes/eg8_counter_check.py        (from the sdk directory)
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.path.dirname(os.path.dirname(HERE))

PROXY = os.path.join(SDK, "agentnode_sdk", "sandbox", "egress_proxy.py")
TEST = ("tests/test_a_name_that_changes_where_it_points.py"
        "::TestAnAllowedNameThatStartsPointingSomewhereElse"
        "::test_the_second_request_is_screened_although_the_name_is_still_allowed")
PHRASE = "the second leg was not screened"

AS_IT_IS = "    return screen_addrinfos(infos)"
AS_IF_NOBODY_SCREENED = "    return [(info[0], info[4]) for info in infos]"

#: a colourised failure line starts with an escape sequence, not with "E ". Composed rather than
#: written as an escape, so nothing between here and the disk can reinterpret it.
ANSI = re.compile(chr(27) + r"\[[0-9;]*m")


def digest() -> str:
    return hashlib.sha256(io.open(PROXY, "rb").read()).hexdigest()


def run_the_test():
    done = subprocess.run([sys.executable, "-m", "pytest", TEST, "-q", "--no-header",
                           "--color=no", "-p", "no:cacheprovider", "-p", "no:randomly"],
                          cwd=SDK, capture_output=True, text=True, timeout=1800)
    said = ANSI.sub("", (done.stdout or "") + (done.stderr or ""))
    return done.returncode, said


def failure_lines(said: str) -> list:
    return [line for line in said.splitlines()
            if line.startswith("E ") or line.startswith("E\t") or line.startswith("FAILED")]


def main() -> int:
    print("#### the request-time screening, reverted, against the test that rests on it")
    before = digest()
    text = io.open(PROXY, encoding="utf-8").read()
    if AS_IT_IS not in text:
        print("THE MUTATION CANNOT LAND: %r is not in %s" % (AS_IT_IS, PROXY))
        return 1

    rc, said = run_the_test()
    if rc != 0:
        print("the test is NOT GREEN before the mutation, so nothing below would mean anything:")
        for line in failure_lines(said)[:15]:
            print("   " + line[:200])
        if not failure_lines(said):
            print(said[-3000:])
        return 1
    print("   unmutated: GREEN")

    io.open(PROXY, "w", encoding="utf-8", newline="\n").write(
        text.replace(AS_IT_IS, AS_IF_NOBODY_SCREENED, 1))
    landed = digest() != before and AS_IF_NOBODY_SCREENED in io.open(PROXY, encoding="utf-8").read()
    print("   mutation landed: %s" % landed)

    try:
        rc, said = run_the_test()
        lines = failure_lines(said)
        red_for_its_reason = any(PHRASE in line for line in lines)
        print("   mutated  : %s" % ("RED" if rc != 0 else "STILL GREEN"))
        print("   red for %r: %s" % (PHRASE, red_for_its_reason))
        if not red_for_its_reason:
            for line in lines[:15]:
                print("      " + line[:200])
            if not lines:
                print(said[-3000:])
    finally:
        io.open(PROXY, "w", encoding="utf-8", newline="\n").write(text)
        after = digest()
        print("   restored byte-exactly: %s" % (after == before))

    good = landed and rc != 0 and red_for_its_reason and after == before
    print("VERDICT: %s" % ("the screening is what refuses a rebound name"
                           if good else "THIS CHECK DID NOT ESTABLISH WHAT IT CLAIMS"))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
