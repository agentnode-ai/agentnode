"""A DNS server that answers from a file it re-reads, so a name can change where it points mid-run.

WHY THIS EXISTS. EG8 of the frozen beta acceptance says direct IP destinations, other ports, DNS bypass,
redirects and **DNS rebinding** do not widen the allowance. Three independent review rounds accepted the
first four and refused the last, in these words: the evidence showed *"request-time rejection of an already
non-public result"* but *"it does not show an allowed name's resolution changing"*. Those are different
properties. A name that resolves privately from the start is refused one layer earlier, at activation. A name
that resolves publicly when the policy is activated and privately when the request is made is the actual
rebinding attack, and nothing in the record had exercised it.

To exercise it, something has to be able to change its mind about a name while a proxy is already running.
That is this: a small authoritative-ish resolver whose answers come from a JSON file it reads **on every
query**, so a test changes an answer by writing a file and the very next lookup sees it. No restart, no
cache, no second proxy.

    {"rebind.test": "1.1.1.1"}      ->  write  {"rebind.test": "10.0.0.5"}  ->  the next lookup is private

WHAT IT IS NOT. It is not a general resolver and it is not a mock of the product. Names it has no answer for
are **forwarded upstream unchanged**, because while this is the host's resolver everything else on the
machine still has to be able to resolve -- including the container runtime pulling an image. It never
inspects or alters anything but the question name, and it answers with TTL 0 so that no resolver between it
and the asker is entitled to remember the old answer.

WHY `.test` NAMES ARE USED IN THE TEST THAT DRIVES THIS. `.test` is reserved and never resolves anywhere
else, so an answer for one can only have come from here. That makes the arrangement self-checking: if this
resolver is not wired up, the name does not resolve at all, the proxy refuses the *first* leg too, and the
test fails saying so instead of quietly passing for the wrong reason.

usage:
    python a_resolver_that_can_change_its_mind.py --answers /tmp/eg8-answers.json \
        [--listen 127.0.0.1:53] [--upstream 8.8.8.8] [--quiet]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import socket
import struct
import sys
import time

sys.dont_write_bytecode = True

A_RECORD = 1
IN_CLASS = 1


def read_answers(path: str) -> dict:
    """The table, re-read per query. A missing or unreadable file is an empty table, not a crash.

    Deliberately forgiving: the test writes this file while this process is serving, so a read can
    land on a half-written file. An empty table means "forward upstream", which is the safe answer --
    it never invents an address and never keeps a stale one.
    """
    try:
        raw = io.open(path, "rb").read()
        table = json.loads(raw.decode("utf-8"))
    except Exception:                                             # noqa: BLE001
        return {}
    if not isinstance(table, dict):
        return {}
    return {str(k).rstrip(".").lower(): str(v) for k, v in table.items()}


def question_of(packet: bytes):
    """``(name, qtype, end_offset)`` of the first question, or ``None`` if it cannot be read."""
    try:
        if len(packet) < 12:
            return None
        qdcount = struct.unpack("!H", packet[4:6])[0]
        if qdcount < 1:
            return None
        labels, at = [], 12
        while True:
            if at >= len(packet):
                return None
            length = packet[at]
            if length == 0:
                at += 1
                break
            if length & 0xC0:            # a pointer has no business in a question
                return None
            labels.append(packet[at + 1:at + 1 + length].decode("ascii", "replace"))
            at += 1 + length
        qtype, _qclass = struct.unpack("!HH", packet[at:at + 4])
        return ".".join(labels).lower(), qtype, at + 4
    except Exception:                                             # noqa: BLE001
        return None


def answer_for(packet: bytes, end: int, address: str) -> bytes:
    """One A answer for the question already in ``packet``, TTL 0."""
    header = struct.pack(
        "!HHHHHH",
        struct.unpack("!H", packet[0:2])[0],      # the asker's id, echoed
        0x8180,                                   # response, recursion desired and available
        1, 1, 0, 0,                               # one question, one answer
    )
    body = packet[12:end]                         # the question, echoed verbatim
    rdata = socket.inet_aton(address)
    answer = struct.pack("!HHHIH", 0xC00C, A_RECORD, IN_CLASS, 0, len(rdata)) + rdata
    return header + body + answer


def forward(packet: bytes, upstream: str, timeout: float = 5.0):
    """Ask the real resolver and hand back exactly what it said."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as out:
        out.settimeout(timeout)
        out.sendto(packet, (upstream, 53))
        try:
            reply, _ = out.recvfrom(65535)
            return reply
        except socket.timeout:
            return None


def serve(listen: str, answers_path: str, upstream: str, quiet: bool) -> int:
    host, _, port = listen.partition(":")
    port = int(port or 53)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except PermissionError:
        print("cannot bind %s:%d -- port 53 needs privilege" % (host, port), flush=True)
        return 1
    except OSError as why:
        print("cannot bind %s:%d -- %s" % (host, port, why), flush=True)
        return 1

    def say(*parts):
        if not quiet:
            print("resolver", time.strftime("%H:%M:%SZ", time.gmtime()), *parts, flush=True)

    say("listening on %s:%d, answers from %s, everything else to %s"
        % (host, port, answers_path, upstream))
    while True:
        try:
            packet, who = sock.recvfrom(65535)
        except KeyboardInterrupt:
            say("stopping")
            return 0
        except Exception as why:                                  # noqa: BLE001
            say("recv failed:", type(why).__name__, why)
            continue
        asked = question_of(packet)
        if asked is None:
            say("a question that could not be read, forwarded as it came")
            reply = forward(packet, upstream)
            if reply:
                sock.sendto(reply, who)
            continue
        name, qtype, end = asked
        table = read_answers(answers_path)
        mine = table.get(name)
        if mine and qtype == A_RECORD:
            say("%-28s A    -> %s   (mine, ttl 0)" % (name, mine))
            sock.sendto(answer_for(packet, end, mine), who)
            continue
        if mine and qtype != A_RECORD:
            # A name of mine asked for something that is not an address: answer with no records
            # rather than forwarding, so the name stays entirely this resolver's business.
            say("%-28s type %-4s -> no records (mine, but not an address question)" % (name, qtype))
            header = struct.pack("!HHHHHH", struct.unpack("!H", packet[0:2])[0], 0x8180, 1, 0, 0, 0)
            sock.sendto(header + packet[12:end], who)
            continue
        say("%-28s type %-4s -> upstream %s" % (name, qtype, upstream))
        reply = forward(packet, upstream)
        if reply:
            sock.sendto(reply, who)


def main(argv: list) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--answers", required=True, help="the JSON table, re-read on every query")
    p.add_argument("--listen", default="127.0.0.1:53")
    p.add_argument("--upstream", default=os.environ.get("EG8_UPSTREAM", "8.8.8.8"),
                   help="where names this resolver has no answer for are sent")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv[1:])
    return serve(args.listen, args.answers, args.upstream, args.quiet)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
