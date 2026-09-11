"""The places this tool may get a fact from, and what each of them knows.

`EM3C-E6-RECORD-0001`: the previous tool asked a shell on the far machine what a sandbox job had
printed. A shell does not know that. It answered the only way it could -- it found nothing -- and
the tool read "found nothing" as "the value did not cross", when what had actually happened was
that nobody had asked anywhere it could have been.

So a channel here is a thing with a name and a short list of what it can be asked. Asking it
anything else is not possible, because there is no method for it.

And what answered is not a label. An `Answer` cannot be constructed outside a channel: the only
way to hold one is to have been given it by the channel that produced it, so "this came over the
gateway's own transport" is a fact about how the object exists rather than a string this tool
wrote and could equally have written somewhere else.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

#: Held by this module and by nothing else. An `Answer` refuses to be built without it, which is
#: what makes a channel the only way to produce one.
_ONLY_A_CHANNEL = object()


class ChannelError(Exception):
    """The channel could not be asked, or answered something this tool will not read."""


@dataclass(frozen=True)
class Answer:
    """What a channel said, inseparable from the channel that said it."""

    channel: str
    #: What was asked, in the channel's own terms -- an endpoint, a command. Not a description.
    asked: str
    #: What came back, as it came back.
    value: Any
    #: True when the channel was reached and answered at all. A channel that could not be asked
    #: is not a channel that answered "no": `EM3C-EVIDENCE-0002` cost an external run to that.
    answered: bool
    #: Why it could not be asked, when it could not.
    trouble: str = ""

    def __post_init__(self) -> None:
        if getattr(self, "_made_by", None) is not _ONLY_A_CHANNEL:
            raise ChannelError(
                "an Answer says which channel produced it, so it is produced by one. Building it "
                "anywhere else would make provenance a label this tool writes for itself, which "
                "is the thing that must not be possible")

    def digest(self) -> str:
        """A digest of what came back, for a record to hold against what was decided from it."""
        return hashlib.sha256(
            json.dumps(self.value, sort_keys=True, separators=(",", ":"),
                       default=str).encode("utf-8")).hexdigest()


class Channel:
    """Something that can be asked, and the only thing that can produce an `Answer`.

    `EM3C-VERIFY-0001`: what stood here was a module-level factory taking the channel's name as
    an argument. The private token made ordinary construction impossible and left the factory
    open, so provenance was still, in the end, a string a caller supplied. It is not a string
    anybody supplies now: `said` takes no name, and the name it writes comes off the class of the
    object doing the saying. To produce an `Answer` claiming to be a channel, one has to BE a
    channel -- which is the honest boundary, because a class that answers questions about a
    machine is a channel whatever it is called.
    """

    #: What this channel is, on every answer it gives.
    name = ""

    def said(self, asked: str, value: Any, answered: bool = True, trouble: str = "") -> Answer:
        made = Answer.__new__(Answer)
        object.__setattr__(made, "_made_by", _ONLY_A_CHANNEL)
        object.__setattr__(made, "channel", type(self).name)
        object.__setattr__(made, "asked", asked)
        object.__setattr__(made, "value", value)
        object.__setattr__(made, "answered", answered)
        object.__setattr__(made, "trouble", trouble)
        made.__post_init__()
        return made


class TheGatewayItself(Channel):
    """The gateway's own transport. It knows about runs, and it signs what it says.

    Nothing about the wire format is written here. `status_of` is the production client, the
    verification inside it is the production client's own, and what comes back is whatever the
    production serializer produced. A change to any of those reaches this tool by breaking it,
    which is the only way this tool wants to hear about one.
    """

    name = "the gateway's signed answer"

    def __init__(self, connection) -> None:
        self.connection = connection

    def record_of(self, run_id: str) -> Answer:
        """The signed record of one run, verified, or an answer saying it could not be had."""
        from agentnode_sdk.gateway import client as gc

        asked = "GET /v1/jobs/" + run_id
        try:
            record = gc.status_of(self.connection, run_id)
        except Exception as exc:                              # noqa: BLE001
            return self.said(asked, None, False, f"{type(exc).__name__}: {exc}")
        return self.said(asked, record)


class TheFarMachineItself(Channel):
    """A shell on the far machine. It knows what the machine is. It is not asked anything else.

    There is no method here that takes a run id, a value to look for, or a path to search, and
    that is deliberate: the previous tool's whole defect was asking this channel a question it
    could not answer and then believing the answer.
    """

    name = "the far machine, over ssh"

    def __init__(self, ask) -> None:
        #: Something that takes one command and returns `(ran, exit_code, stdout, stderr)`.
        #: Supplied rather than built here so this class has no opinion about transport.
        self.ask = ask

    def _one(self, what: str, command: str) -> Answer:
        try:
            ran, code, out, err = self.ask(command)
        except Exception as exc:                              # noqa: BLE001
            return self.said(command, None, False, f"{type(exc).__name__}: {exc}")
        if not ran:
            return self.said(command, None, False, err or "the command did not run")
        if code != 0:
            return self.said(command, None, False, f"exit {code}: {(err or out).strip()[:200]}")
        return self.said(command, out.strip())

    def identity(self) -> Answer:
        """What this machine calls itself, in a form a machine does not share with another."""
        return self._one("machine identity", "cat /etc/machine-id")

    def kernel(self) -> Answer:
        return self._one("kernel", "uname -sr")

    def hostname(self) -> Answer:
        return self._one("hostname", "hostname")

    def listening(self) -> Answer:
        return self._one("listening sockets", "ss -ltn")


class TheGatewaysOwnRecord(Channel):
    """What the gateway wrote down about a run, read from its own durable ledger over ssh.

    A third channel, and deliberately not the second one. `TheFarMachineItself` is asked what the
    MACHINE is and has no method that takes a run; this one is asked what the GATEWAY recorded for
    one run, through the gateway's own read-only command. It cannot list, it cannot search, and
    it takes one run id because a document about one run is the only thing it answers with.

    What it cannot return is the challenge itself. The ledger holds the digest and never held the
    value, so somebody holding this channel's output cannot produce a value -- they can only be
    told whether a value they already have is the one that was issued. That is what makes this a
    second channel rather than a second copy of the first.
    """

    name = "the gateway's own record, over ssh"

    def __init__(self, ask, gateway_bin: str, state_dir: str, as_user: str,
                 token: str) -> None:
        self.ask = ask
        self.gateway_bin = gateway_bin
        self.state_dir = state_dir
        self.as_user = as_user
        #: Who is asking. The command answers to the client that submitted the run and to nobody
        #: else, and it takes this on the far side's STANDARD INPUT -- never as an argument.
        self.token = token

    def for_run(self, run_id: str) -> Answer:
        """The binding this gateway wrote down for one run, as it wrote it."""
        import json

        # Who is asking goes on the far side's STANDARD INPUT, because the command line of a
        # process is readable by anybody on that host. `printf` is a shell builtin, so the token
        # never becomes a process of its own either. And what is WRITTEN DOWN is `shown`, with a
        # word where the value is: an evidence file that carried a live credential would be a
        # worse thing than the one it is evidence of.
        # A token this gateway issues is `secrets.token_urlsafe`: letters, digits, a dash and
        # an underscore, and nothing a shell reads. Anything else is not put in a command at
        # all, rather than quoted and hoped about.
        plain = self.token.replace('-', '').replace('_', '')
        if not plain.isascii() or not plain.isalnum():
            return self.said("(a token this channel will not put in a command)", None, False,
                             "this token is not the shape this gateway issues, so it is not sent")
        shown = ("printf '%%s' <the client's own token> | sudo -u %s %s gateway challenge "
                 "--dir %s --run %s" % (self.as_user, self.gateway_bin, self.state_dir, run_id))
        command = shown.replace("<the client's own token>", "'" + self.token + "'")
        try:
            ran, code, out, err = self.ask(command)
        except Exception as exc:                              # noqa: BLE001
            return self.said(shown, None, False, f"{type(exc).__name__}: {exc}")
        if not ran:
            return self.said(shown, None, False, err or "the command did not run")
        if code != 0:
            return self.said(shown, None, False,
                             f"exit {code}: {(err or out).strip()[:200]}")
        try:
            return self.said(shown, json.loads(out))
        except ValueError as exc:
            return self.said(shown, None, False, f"what came back is not readable: {exc}")


class ThisMachine(Channel):
    """The client this tool is running on. It knows what IT is, and nothing about the far side."""

    name = "this machine"

    def identity(self) -> Answer:
        import platform
        import socket

        return self.said("platform.node()+platform.platform()",
                         {"node": socket.gethostname(), "platform": platform.platform()})
