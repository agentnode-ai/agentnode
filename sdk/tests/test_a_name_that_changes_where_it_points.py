"""EG8's missing case: an allowed name whose resolution CHANGES, refused at the request.

WHAT THREE REVIEW ROUNDS REFUSED TO ACCEPT, AND WHY THEY WERE RIGHT. EG8 of the frozen beta acceptance says
direct IP destinations, other ports, DNS bypass, redirects and DNS rebinding do not widen the allowance. The
first four were established. Rebinding was not, and the reviewer's objection was exact: the evidence showed
*"request-time rejection of an already non-public result"* and *"does not show an allowed name's resolution
changing"*. Those are two different properties:

  * a name that resolves privately from the start is refused when the policy is ACTIVATED -- one layer
    earlier, by the conformance measurement, which requires every destination to come back ALLOWED;
  * a name that resolves publicly while the policy is activated and privately when a request is made is the
    rebinding attack, and until this file nothing had exercised it.

HOW THIS EXERCISES IT, AND WHAT MAKES IT A MEASUREMENT RATHER THAN A STORY.

  * ONE proxy, started once, never restarted. Both legs go through the same container with the same
    allowlist. A second proxy would let a reader say the refusal came from a fresh process rather than from
    re-resolution, so the proxy's id and start time are asserted to be the same at the end as at the start.
  * The resolution is changed by a real resolver -- `a_resolver_that_can_change_its_mind.py` -- which re-reads
    its answer table on every query. The test changes one answer by writing a file. Nothing is monkeypatched
    and no product function is replaced; the proxy resolves through the container runtime's resolver exactly
    as it does in production.
  * The decision is read from THE PRODUCT'S OWN RECORD: the proxy writes one line per decision, and
    `SCREENED` is the word it uses for "allowed by name, but it resolves to an address this proxy will not
    reach". The payload's own view is captured too, but the proxy's log is what the assertion rests on,
    because a boundary whose refusals are only self-reported by the thing being refused is not evidenced.
  * The name is under `.test`, which is reserved and resolves nowhere else. So an answer for it can only have
    come from this test's resolver -- which makes the arrangement self-checking. If the resolver is not wired
    up the name does not resolve at all, the FIRST leg is screened too, and the test fails saying exactly
    that rather than passing for the wrong reason.

WHAT WOULD MAKE THIS GO RED, as the arc's profile requires of any test it rests a criterion on: removing the
request-time screening in `egress_proxy.screen_addrinfos` lets the second leg through, and the assertion on
the second leg fails with `the second leg was not screened`. That mutation and its predicted phrase are
driven by the lane, because a counter-check that can only be skipped is not a counter-check.

WHERE THIS RUNS. It needs a container runtime, the pinned image, and the resolver wired in as the runtime's
resolver -- which is an arrangement of the machine, not of this file. The lane
`.github/workflows/eg8-rebinding.yml` makes that arrangement and then requires this test to have RUN: a skip
there is a lane failure. On a workstation without a runtime it skips, with the reason naming what is missing.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import time
import uuid

import pytest

from agentnode_sdk.sandbox.policy import get_default_backend
from agentnode_sdk.sandbox.types import ProcessSpec

#: the name whose answer changes. `.test` is reserved: nothing on the internet can answer for it.
THE_NAME = "rebind.test"

#: a globally routable address, so the FIRST leg gets past screening. Whether it answers on 443 is not
#: the point and is not relied on -- `ALLOWED` and `UNREACHED` both mean the screening let it through,
#: and either is accepted below. What must not happen on this leg is `SCREENED`.
A_PUBLIC_ADDRESS = "1.1.1.1"

#: where the same name points for the second leg: an address the proxy must refuse to reach.
A_PRIVATE_ADDRESS = "10.0.0.5"

#: the answers file the resolver re-reads; the lane tells the test where it is
ANSWERS = os.environ.get("EG8_ANSWERS", "")


def _runtime_is_there():
    try:
        backend = get_default_backend()
    except Exception as why:                                      # noqa: BLE001
        return None, "no backend could be built: %s" % (why,)
    available = backend.check_available()
    if not available.available:
        return None, "no usable container runtime: %s" % (available.reason or "unavailable")
    return backend, ""


needs_the_arrangement = pytest.mark.skipif(
    os.name != "posix" or not ANSWERS,
    reason="needs the stub resolver the eg8 lane arranges: set EG8_ANSWERS to its answers file and make "
           "it the resolver the container runtime uses (see .github/workflows/eg8-rebinding.yml)")


def _write_answers(where_the_name_points: str) -> None:
    """Point the name somewhere. The resolver reads this on its next query -- no restart, no cache."""
    body = json.dumps({THE_NAME: where_the_name_points}, indent=2) + "\n"
    io.open(ANSWERS, "w", encoding="utf-8", newline="\n").write(body)


def _proxy_log(handle) -> str:
    done = subprocess.run([handle.runtime, "logs", handle.proxy_name],
                          capture_output=True, text=True, timeout=60)
    return (done.stdout or "") + (done.stderr or "")


def _decisions_about_the_name(log: str) -> list:
    return [line.strip() for line in log.splitlines()
            if "egress-proxy" in line and THE_NAME in line]


def _one_leg(backend, handle, label: str) -> dict:
    """Ask for the name through the proxy, from inside the network that has no other way out."""
    source = (
        "import json,urllib.request,socket\n"
        "socket.setdefaulttimeout(15)\n"
        "out={}\n"
        "try:\n"
        "    urllib.request.urlopen('https://%s/', timeout=15)\n"
        "    out['payload_saw']='opened'\n"
        "except urllib.error.HTTPError as e:\n"
        "    out['payload_saw']='http:'+str(e.code)\n"
        "except Exception as e:\n"
        "    out['payload_saw']='refused:'+type(e).__name__\n"
        "print('PAYLOAD '+json.dumps(out))\n" % THE_NAME
    )
    spec = ProcessSpec(command=["python", "-c", source], network="egress", egress=handle.spec,
                       clean_home=True,
                       name="agentnode-eg8-%s-%s" % (label, uuid.uuid4().hex[:8]))
    rc, out, err = backend.run_process(spec, timeout=120)
    return {"rc": rc, "stdout": out or "", "stderr": err or ""}


@needs_the_arrangement
class TestAnAllowedNameThatStartsPointingSomewhereElse:
    """One proxy, one allowlist, two requests, and a different answer in between."""

    def test_the_second_request_is_screened_although_the_name_is_still_allowed(self):
        from agentnode_sdk.sandbox import egress as egress_mod

        backend, why_not = _runtime_is_there()
        if backend is None:
            pytest.skip(why_not)

        # Where the name points for the FIRST leg: somewhere the proxy is willing to reach.
        _write_answers(A_PUBLIC_ADDRESS)

        handle = egress_mod.start_egress_proxy((THE_NAME,), backend=backend)
        try:
            started_as = subprocess.run(
                [handle.runtime, "inspect", handle.proxy_name, "--format",
                 "{{.Id}} {{.State.StartedAt}}"],
                capture_output=True, text=True, timeout=60).stdout.strip()

            # ---- leg one: the name is allowed and points somewhere public ----------------------
            first = _one_leg(backend, handle, "first")
            after_first = _proxy_log(handle)
            said_first = _decisions_about_the_name(after_first)

            assert said_first, (
                "the proxy wrote down no decision about %s at all, so this test measured nothing. "
                "payload said %r / %r" % (THE_NAME, first["stdout"][-400:], first["stderr"][-400:]))
            # THE SELF-CHECK. A `.test` name resolves nowhere but through this test's resolver. If the
            # first leg is screened, the resolver is not answering and the second leg would be screened
            # for that reason instead of for the one this test is about.
            assert not any("SCREENED" in line for line in said_first), (
                "the FIRST leg was screened, which means %s did not resolve to %s -- the stub resolver "
                "is not the resolver the runtime is using, so a screening on the second leg would prove "
                "nothing. What the proxy wrote: %r" % (THE_NAME, A_PUBLIC_ADDRESS, said_first))
            assert any(("ALLOWED" in line or "UNREACHED" in line) for line in said_first), (
                "the first leg neither got through nor failed to connect; the proxy said %r" % (said_first,))

            # ---- the name starts pointing somewhere else, with nothing restarted ---------------
            _write_answers(A_PRIVATE_ADDRESS)

            # ---- leg two: the same name, the same proxy, the same allowlist --------------------
            second = _one_leg(backend, handle, "second")
            after_second = _proxy_log(handle)
            said_second = [line for line in _decisions_about_the_name(after_second)
                           if line not in said_first]

            assert said_second, (
                "the proxy wrote down no decision about the second request, so the change was never "
                "put to it. payload said %r / %r"
                % (second["stdout"][-400:], second["stderr"][-400:]))
            assert any("SCREENED" in line for line in said_second), (
                "the second leg was not screened: the name now resolves to %s, which is not an address "
                "the proxy may reach, and it was allowed through anyway. What the proxy wrote: %r"
                % (A_PRIVATE_ADDRESS, said_second))
            # AND IT MUST SAY WHICH REFUSAL IT IS. `resolve_and_screen` raises the same exception for a
            # name that could not be resolved and for a name that resolved to something forbidden, and
            # until this arc the proxy's log said the second in both cases -- so this very assertion
            # would have passed if the resolver had simply stopped answering. The proxy now carries the
            # exception's own words, and what this leg must show is the address it refused.
            assert any(("non-public address resolved" in line and A_PRIVATE_ADDRESS in line)
                       for line in said_second), (
                "the refusal does not name the private address it resolved to, so it is not evidence "
                "about rebinding rather than about a resolver that stopped answering: %r"
                % (said_second,))

            # ---- and it was the same proxy throughout -----------------------------------------
            still_as = subprocess.run(
                [handle.runtime, "inspect", handle.proxy_name, "--format",
                 "{{.Id}} {{.State.StartedAt}}"],
                capture_output=True, text=True, timeout=60).stdout.strip()
            assert still_as == started_as and started_as, (
                "the proxy was not the same process across the two legs, so the second refusal could "
                "have come from a restart rather than from re-resolution: %r then %r"
                % (started_as, still_as))
        finally:
            egress_mod.stop_egress_proxy(handle)
            _write_answers(A_PUBLIC_ADDRESS)

    def test_the_resolver_this_rests_on_really_does_change_its_answer(self):
        """The instrument, checked separately -- because the test above would also pass if the resolver
        simply stopped answering after the first leg.

        A screening and a resolution failure are both `EgressBlocked`, and both are logged as `SCREENED`.
        So the claim "the name now points somewhere private" needs its own evidence: the resolver is asked
        directly, before and after, and has to give two different addresses.
        """
        import socket

        resolver = os.environ.get("EG8_RESOLVER", "")
        if not resolver:
            pytest.skip("set EG8_RESOLVER to host:port of the stub resolver to check it directly")
        host, _, port = resolver.partition(":")
        port = int(port or 53)

        def ask() -> str:
            query = (b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
                     + b"".join(bytes([len(p)]) + p.encode("ascii")
                                for p in THE_NAME.split(".")) + b"\x00\x00\x01\x00\x01")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(10)
                s.sendto(query, (host, port))
                reply, _ = s.recvfrom(4096)
            assert reply[6:8] != b"\x00\x00", "the resolver answered with no records for %s" % THE_NAME
            return ".".join(str(b) for b in reply[-4:])

        _write_answers(A_PUBLIC_ADDRESS)
        time.sleep(0.1)
        first = ask()
        _write_answers(A_PRIVATE_ADDRESS)
        time.sleep(0.1)
        second = ask()
        _write_answers(A_PUBLIC_ADDRESS)

        assert first == A_PUBLIC_ADDRESS, (
            "the resolver did not give the public address it was told to: %r" % (first,))
        assert second == A_PRIVATE_ADDRESS, (
            "the resolver did not change its mind when the file changed: %r" % (second,))
        assert first != second, "the two answers are the same, so nothing changed"
