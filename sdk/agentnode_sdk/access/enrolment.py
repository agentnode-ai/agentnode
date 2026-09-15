"""Proving that a particular AI can actually use this sandbox, rather than being told so.

"Compatible" is a claim a product makes in public, and the tempting way to make it is to look at
the AI's documentation, see that it supports tool calling, and write COMPATIBLE. That establishes
nothing: documentation is a claim about a product, not an observation of one, and the failures
that matter are the ones where a tool interface exists and does not work.

So compatibility here means exactly one thing: **that connection carried out an operation, and
this gateway recorded it**. Everything below is about making sure the observation cannot be
satisfied by anything other than the thing being tested.

An observation is bound to six things, and a test that fails any of them proves nothing:

* the ACCOUNT it was set up from;
* the DEVICE -- a connection enrolled for this test and no other, so a job started earlier, or by
  some other device on the same account, cannot be mistaken for it;
* the CHANNEL -- calling over REST does not establish that the MCP connection works;
* the OPERATION -- reading `capabilities` is not evidence a job can be run;
* a NONCE -- this run of the setup flow and not a previous one;
* a short EXPIRY, and one use.

The device is the load-bearing one. A challenge issued for a freshly enrolled connection cannot
be satisfied by anything that existed before it: there was no such device to make the call.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from agentnode_sdk.gateway.filelock import ProcessLock, atomically


#: How long somebody has to finish setting up their AI and make it do something. Long enough to
#: paste a file into a config and restart a program; short enough that a challenge left lying
#: around is not a standing one.
GOOD_FOR_SECONDS = 30 * 60
#: How long a download ticket lasts. It is spent within seconds of being issued, by a form the
#: page submits itself, so this is a bound rather than a budget.
DOWNLOAD_SECONDS = 2 * 60


class NoSuchChallenge(Exception):
    """Asked about a challenge this gateway did not issue, or one that has lapsed."""


def setup_file(channel: str, base_url: str, token: str, label: str) -> tuple:
    """What to hand somebody for the connection they chose. Returns `(filename, text)`.

    Written here rather than in the page, because the page never sees it. A setup file carries a
    working credential, so it goes from this gateway to the person's disk without passing through
    any JavaScript, any URL, or anything that ends up in a browser's history.
    """
    if channel in ("rest", "cli"):
        return ("agentnode-connection.txt",
                "# AgentNode -- the connection called %s\n"
                "# This file contains an access credential. Treat it like a password: do not\n"
                "# paste it into a chat, and do not put it in a shared folder.\n"
                "AGENTNODE_URL=%s\n"
                "AGENTNODE_TOKEN=%s\n" % (label, base_url, token))
    return ("agentnode-tools.json",
            json.dumps({"mcpServers": {"agentnode": {
                "url": base_url + "/v1/mcp",
                "headers": {"X-AgentNode-Token": token}}}}, indent=2) + "\n")


class Connections:
    """The connections being set up, and what each one has to do to be believed."""

    def __init__(self, root, clock=time.time) -> None:
        self._path = os.path.join(str(root), "enrolling.json")
        self._clock = clock
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ starting one

    def begin(self, account: str, channel: str, label: str, operation: str = "submit",
              started_by: str = "") -> dict:
        """Start setting a connection up. Returns what to show and what to poll.

        No device yet: the credential is created when the setup file is actually downloaded, so
        a challenge somebody starts and abandons leaves no connection behind that could be used.

        Two identities, because there are two questions and they have different answers:

        * `account` decides who may SEE and finish this setup. A person who starts it on their
          laptop and finishes on their desktop is one customer doing one thing.
        * `started_by` is the device that began it, which is what a WITHDRAWAL reaches. These
          were one field for a while, and the day `account` started holding an account id,
          withdrawing a device silently stopped dropping the enrolments it had started -- and an
          unspent download ticket mints a fresh credential when it is collected.
        """
        now = self._clock()
        challenge = secrets.token_urlsafe(24)
        entry = {
            "account": str(account),
            "started_by": str(started_by or ""),
            "channel": str(channel),
            "label": str(label or ""),
            "operation": str(operation),
            "nonce": secrets.token_hex(16),
            "began_at": now,
            "expires_at": now + GOOD_FOR_SECONDS,
            "device": "",
            "ticket": secrets.token_urlsafe(24),
            "ticket_until": now + DOWNLOAD_SECONDS,
            "satisfied_at": 0.0,
        }
        with self._lock, ProcessLock(self._path):
            kept = self._read()
            kept[challenge] = entry
            self._write(self._tidied(kept, now))
        return dict(entry, challenge=challenge)

    def about(self, challenge: str) -> dict:
        with self._lock:
            kept = self._read()
        entry = kept.get(str(challenge))
        if entry is None or self._clock() >= float(entry.get("expires_at", 0)):
            raise NoSuchChallenge(
                "this sandbox is not setting up a connection under that name, or the window for "
                "it has closed")
        return dict(entry, challenge=str(challenge))

    # ------------------------------------------------------------------ the download

    def spend_the_ticket(self, challenge: str, ticket: str, device: str) -> dict:
        """Bind the connection that was just created, and use up the ticket.

        One download. A setup file carries a working credential, so handing the same one out
        twice would mean the thing under test is no longer the only holder of it.
        """
        now = self._clock()
        with self._lock, ProcessLock(self._path):
            kept = self._read()
            entry = kept.get(str(challenge))
            if entry is None or now >= float(entry.get("expires_at", 0)):
                raise NoSuchChallenge("that setup is no longer being offered")
            if not ticket or ticket != entry.get("ticket"):
                raise NoSuchChallenge("that is not this setup's download")
            if now >= float(entry.get("ticket_until", 0)):
                raise NoSuchChallenge("that download has expired; start the setup again")
            entry["ticket"] = ""
            entry["device"] = str(device)
            kept[str(challenge)] = entry
            self._write(kept)
        return dict(entry, challenge=str(challenge))

    # ------------------------------------------------------------------ the observation

    def satisfied_by(self, challenge: str, audit) -> dict:
        """Whether the gateway has recorded the call this challenge is waiting for.

        `audit` is a callable returning the gateway's own lines. Nothing the claimant supplies
        takes part in this: the device, the channel and the operation are compared against what
        the dispatcher wrote down when it carried something out.
        """
        entry = self.about(challenge)
        if entry.get("satisfied_at"):
            return dict(entry, satisfied=True)
        if not entry.get("device"):
            # Nothing has been downloaded, so no connection exists that could have called.
            return dict(entry, satisfied=False, why="the setup has not been collected yet")
        for line in audit():
            if line.get("device") != entry["device"]:
                continue
            if line.get("via") != entry["channel"]:
                continue
            if line.get("operation") != entry["operation"]:
                continue
            if line.get("outcome") != "carried_out":
                continue
            if float(line.get("at", 0)) < float(entry["began_at"]):
                # Cannot be evidence for a challenge that did not exist when it happened.
                continue
            with self._lock, ProcessLock(self._path):
                kept = self._read()
                if str(challenge) in kept:
                    kept[str(challenge)]["satisfied_at"] = float(line["at"])
                    self._write(kept)
            return dict(entry, satisfied=True, satisfied_at=float(line["at"]))
        return dict(entry, satisfied=False,
                    why="nothing has been recorded from that connection yet")

    # ------------------------------------------------------------------ withdrawal

    def drop_everything_touching(self, device: str) -> int:
        """Forget every setup this device started or was to become. Returns how many.

        Withdrawing a device has to reach what it had already been GIVEN, not only what it might
        ask for next. A challenge it started carries an unspent download ticket, and that ticket
        mints a fresh credential when it is collected -- so a withdrawal that left one standing
        would be a withdrawal somebody could walk straight back through.
        """
        device = str(device)
        with self._lock, ProcessLock(self._path):
            kept = self._read()
            going = [k for k, v in kept.items()
                     if v.get("started_by") == device or v.get("device") == device
                     # An entry written before `started_by` existed put the starting device in
                     # `account`. Matching it here keeps a withdrawal complete across an upgrade;
                     # an account id can never equal a device id, so this cannot over-match.
                     or v.get("account") == device]
            for k in going:
                del kept[k]
            if going:
                self._write(kept)
            return len(going)

    # ------------------------------------------------------------------ the file

    def _tidied(self, kept: dict, now: float) -> dict:
        return {k: v for k, v in kept.items() if now < float(v.get("expires_at", 0))}

    def _read(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as fh:
                kept = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise
        return kept if isinstance(kept, dict) else {}

    def _write(self, kept: dict) -> None:
        """Beside and renamed over, with a temp name NOBODY ELSE IS USING.

        What stood here wrote `<file>.new` -- one fixed name, shared by every writer of this
        file. Two of them at once is not a near-miss: the second one renames a path the first
        has already renamed away and fails with `FileNotFoundError`, and in the version where it
        does not fail, one writer's whole file silently replaces the other's. A concurrent
        deletion drill on Linux found the first; the second is the one worth fixing it for.

        `filelock.atomically` takes a unique temp name from `mkstemp` and carries the bounded
        retry Windows needs on the rename. One implementation, for the same reason.
        """
        atomically(self._path, json.dumps(kept, sort_keys=True))
