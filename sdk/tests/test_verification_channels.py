"""What a channel may be asked, what an answer can be made by, and what a crossing is tied to.

`EM3C-E6-RECORD-0001`: the tool this package replaces confirmed a sentinel by opening an ssh
session to the gateway machine and running `grep -c -- <value> /home/.../gateway.log`. A sandbox
job's standard output is not written to that file. The grep answered `0` every time, three
external runs read that as "the value did not cross", and what had really happened was that
nobody had asked anywhere the value could have been.

Two properties come out of that, and this file is about both.

A channel is asked what it knows. `TheFarMachineItself` has no method that takes a run id, a value
to look for, or a path to search -- not a rule it follows, an absence of the means. What it has
are questions about the machine, which a shell on that machine can answer.

And provenance is structural. An `Answer` cannot be built outside a channel, so "this came over
the gateway's own transport" is a fact about how the object exists rather than a string this tool
wrote next to it and could equally have written somewhere else.
"""
from __future__ import annotations

import hashlib
import inspect

import pytest

pytest_plugins = ("tests.real_answers",)

from agentnode_sdk.verification import TOOL, channels, sentinels


class AnAnsweringMachine:
    """Something that answers commands the way a shell does, without being one."""

    def __init__(self, answers=None, trouble=None):
        self.answers = answers or {}
        self.trouble = trouble or {}
        self.asked: list = []

    def __call__(self, command):
        self.asked.append(command)
        for needle, boom in self.trouble.items():
            if needle in command:
                raise boom
        for needle, answer in self.answers.items():
            if needle in command:
                return answer
        return True, 0, "", ""


class AnAnsweringGateway(channels.TheGatewayItself):
    """A stand-in for the SERVER, not for the channel.

    It goes through the real `TheGatewayItself` to produce an `Answer`, because an `Answer` can
    only be produced by a channel. And what it hands back is a record a REAL gateway really gave:
    `EM3C-VERIFY-0001` found this file writing `{"run_id": ..., "stdout": ..., "state": ...}` by
    hand, which is a second definition of the wire format sitting in a test, waiting to keep
    agreeing with itself after production has moved on.
    """

    def __init__(self, record=None, trouble=""):
        super().__init__(connection=None)
        self.record = record
        self.trouble = trouble

    def record_of(self, run_id):
        """The real channel's own `record_of`, with only the SERVER replaced.

        `EM3C-VERIFY-0002`: a crossing now refuses anything that is not the channel it has to be
        confirmed by, so this IS one -- it inherits everything the channel does and swaps out the
        one call that would reach a machine. What is doubled is what answers, not what asks."""
        from agentnode_sdk.gateway import client as gc

        def instead(connection, wanted, verify=True):
            if self.trouble:
                raise RuntimeError(self.trouble)
            return self.record

        was = gc.status_of
        gc.status_of = instead
        try:
            return channels.TheGatewayItself.record_of(self, run_id)
        finally:
            gc.status_of = was


@pytest.fixture(scope="module")
def a_record(real_gateway):
    """A record a real gateway really gave, with the sandbox told what to print.

    Nothing here writes a field of it. `real_gateway` starts a real `GatewayService`, pairs a
    real client, and answers through the production client -- so what comes back is whatever the
    production serializer produced, and a change to it reaches these tests by breaking them.
    """
    def made(stdout=""):
        real_gateway.backend.answers = lambda spec, payload: (0, stdout, "")
        try:
            return real_gateway.a_finished_run()
        finally:
            real_gateway.backend.answers = None
    return made


# ---------------------------------------------------------------- provenance is structural


class TestAnAnswerIsMadeByAChannel:

    def test_one_cannot_be_built_by_anything_else(self):
        with pytest.raises(channels.ChannelError) as caught:
            channels.Answer(channel="the gateway's signed answer", asked="x", value=1,
                            answered=True)
        assert "produced by one" in str(caught.value)

    def test_and_carries_the_name_of_the_one_that_made_it(self):
        """The name comes off the class of the thing that spoke. Not off an argument, and not
        off an attribute a caller could set -- `EM3C-VERIFY-0001` found provenance still being,
        in the end, a string somebody supplied."""
        one = channels.ThisMachine()
        one.claimed = "the gateway's signed answer"        # a caller trying to say otherwise
        said = one.identity()
        assert said.channel == channels.ThisMachine.name
        assert said.answered is True

    def test_a_channel_that_could_not_be_asked_says_so_rather_than_answering_no(self):
        """`EM3C-EVIDENCE-0002` cost an external run to those two being one answer."""
        machine = channels.TheFarMachineItself(
            AnAnsweringMachine(trouble={"machine-id": OSError("no route to host")}))
        said = machine.identity()
        assert said.answered is False
        assert "no route to host" in said.trouble
        assert said.value is None

    def test_a_command_that_ran_and_failed_is_not_a_command_that_could_not_run(self):
        machine = channels.TheFarMachineItself(
            AnAnsweringMachine({"machine-id": (True, 1, "", "permission denied")}))
        said = machine.identity()
        assert said.answered is False and "permission denied" in said.trouble

    def test_the_digest_is_of_what_came_back(self):
        machine = channels.TheFarMachineItself(
            AnAnsweringMachine({"machine-id": (True, 0, "abc123", "")}))
        said = machine.identity()
        import json

        assert said.digest() == hashlib.sha256(
            json.dumps("abc123", sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class TestAChannelIsAskedWhatItKnows:

    def test_the_far_machine_has_no_way_to_be_asked_about_a_run(self):
        """Not a rule it follows. There is no method that takes one.

        Its OWN methods, which are the questions. `said` is inherited from `Channel` and is how a
        channel speaks rather than how it is asked -- a channel that could not produce an answer
        would not be one."""
        for name, method in vars(channels.TheFarMachineItself).items():
            if name.startswith("_") or not callable(method):
                continue
            taken = list(inspect.signature(method).parameters)[1:]
            assert taken == [], (name, taken)

    def test_and_none_of_its_questions_is_a_search(self):
        """The shape the evidence contract refuses, refused here too -- at the source."""
        source = inspect.getsource(channels.TheFarMachineItself)
        for shape in ("grep", "|", "awk", "sed", "find ", "cat /home", "cat /var"):
            assert shape not in source, shape

    def test_it_is_asked_what_the_machine_is(self):
        asking = AnAnsweringMachine({"machine-id": (True, 0, "9c5c1e0a", "")})
        machine = channels.TheFarMachineItself(asking)
        assert machine.identity().value == "9c5c1e0a"
        assert asking.asked == ["cat /etc/machine-id"]

    def test_the_gateway_is_the_only_one_asked_about_a_run(self):
        assert hasattr(channels.TheGatewayItself, "record_of")
        assert not hasattr(channels.TheFarMachineItself, "record_of")

    def test_a_crossing_refuses_a_channel_that_is_only_channel_shaped(self, a_record):
        """`EM3C-VERIFY-0002`: which channel answered was read off the answer, so a thing that
        merely behaved like a gateway was credited as one. It has to BE the channel."""
        class ShapedLikeOne:
            name = channels.TheGatewayItself.name

            def record_of(self, run_id):
                return channels.ThisMachine().said("nothing", {"run_id": run_id, "stdout": "x"})

        with pytest.raises(sentinels.SentinelError) as caught:
            sentinels.what_the_client_made(ShapedLikeOne(), "r" * 32, b"x", "x")
        assert "is not the channel" in str(caught.value)

    def test_and_the_other_direction_needs_both_of_the_right_ones(self, a_record):
        gateway = AnAnsweringGateway(a_record("anything"))
        with pytest.raises(sentinels.SentinelError):
            sentinels.what_the_far_machine_is(gateway, channels.ThisMachine(),
                                              gateway.record["run_id"], b"x")

    def test_how_the_channels_are_written_down_comes_off_the_answers(self):
        """Never a literal. `named` reads `.channel` off each `Answer`, and only a channel can
        have made one."""
        import inspect

        source = inspect.getsource(sentinels.named)
        assert "a.channel for a in answers" in source
        one = channels.ThisMachine().identity()
        assert sentinels.named(one, one) == channels.ThisMachine.name

    def test_the_gateway_channel_restates_no_wire_format(self, a_record):  # noqa: D401
        """It calls the production client and returns what came back. A field list here would be
        a second definition of the protocol -- so the fields are taken FROM a record production
        made, and none of them may appear in the channel's source."""
        source = inspect.getsource(channels.TheGatewayItself)
        assert "gc.status_of" in source
        # The record is returned whole and never reached into. `run_id` is how a run is ASKED
        # for -- a parameter name, not a field this channel reads -- so what is checked is
        # reaching in, which is the thing that would make this a second reader of the format.
        fields = [f for f in a_record()]
        assert len(fields) > 5, fields
        for field in fields:
            for reaching in ('.get("%s"' % field, '["%s"]' % field, "['%s']" % field):
                assert reaching not in source, (field, reaching)

    def test_and_what_does_reach_in_asks_the_protocol_for_the_name(self, a_record):
        """`EM3C-VERIFY-0002`: the crossing spelled `run_id` and `stdout` itself, which is a
        second definition of the format. It asks the module that defines it."""
        import inspect

        from agentnode_sdk.gateway import protocol as wire

        source = inspect.getsource(sentinels)
        assert "wire.RUN_ID_FIELD" in source and "wire.STDOUT_FIELD" in source
        for field in (wire.RUN_ID_FIELD, wire.STDOUT_FIELD):
            assert '.get("%s"' % field not in source, field
        record = a_record("something")
        assert wire.RUN_ID_FIELD in record and wire.STDOUT_FIELD in record


# ---------------------------------------------------------------- what a crossing is tied to


class TestACrossingIsTiedToWhatItProves:

    VALUE = "a1b2c3d4e5f60718"
    PAYLOAD = b"print('SENT-FROM-HERE a1b2c3d4e5f60718')"

    def test_a_value_that_is_not_in_the_payload_is_refused_outright(self, a_record):
        with pytest.raises(sentinels.SentinelError):
            sentinels.what_the_client_made(AnAnsweringGateway(a_record()), "r" * 32,
                                           b"print('something else')", self.VALUE)

    def test_a_crossing_that_holds(self, a_record):
        gateway = AnAnsweringGateway(a_record("SENT-FROM-HERE " + self.VALUE))
        one = sentinels.what_the_client_made(gateway, gateway.record["run_id"],
                                             self.PAYLOAD, self.VALUE)
        assert one.holds is True and one.decidable is True
        assert one.made_on == "the client"
        assert one.run_id == gateway.record["run_id"]
        assert one.payload_text == self.PAYLOAD.decode()
        assert one.payload_sha256 == hashlib.sha256(self.PAYLOAD).hexdigest()
        assert one.confirmed_by == channels.TheGatewayItself.name
        assert one.asked.endswith(gateway.record["run_id"])
        assert len(one.record_sha256) == 64

    def test_a_record_about_another_run_decides_nothing(self, a_record):
        """Not a refutation: an answer about a different run says nothing either way."""
        # A real record about a real run; what differs is the run being ASKED about, so nothing
        # here edits a record to make the case.
        gateway = AnAnsweringGateway(a_record("SENT-FROM-HERE " + self.VALUE))
        one = sentinels.what_the_client_made(gateway, "q" * 32, self.PAYLOAD, self.VALUE)
        assert one.decidable is False and one.holds is False
        assert "says nothing about this one" in one.why

    def test_a_gateway_that_could_not_be_asked_decides_nothing(self):
        gateway = AnAnsweringGateway(trouble="connection refused")
        one = sentinels.what_the_client_made(gateway, "q" * 32, self.PAYLOAD, self.VALUE)
        assert one.decidable is False and one.holds is False
        assert "could not be asked" in one.why

    def test_a_record_that_does_not_carry_it_is_a_refutation(self, a_record):
        gateway = AnAnsweringGateway(a_record("nothing of the sort"))
        one = sentinels.what_the_client_made(gateway, gateway.record["run_id"], self.PAYLOAD, self.VALUE)
        assert one.decidable is True and one.holds is False
        assert "does not carry it" in one.why


class TestTheOtherDirectionNeedsTwoChannels:

    PAYLOAD = b"print(open('/etc/machine-id').read())"
    IDENTITY = "9c5c1e0a11d24f0b8b6f2e2f8a3c4d5e"

    def machine(self, answer=None, trouble=None):
        return channels.TheFarMachineItself(AnAnsweringMachine(
            {"machine-id": answer} if answer else {},
            {"machine-id": trouble} if trouble else {}))

    def test_it_holds_when_both_channels_say_the_same_thing(self, a_record):
        gateway = AnAnsweringGateway(a_record(self.IDENTITY))
        one = sentinels.what_the_far_machine_is(
            gateway, self.machine((True, 0, self.IDENTITY, "")), gateway.record["run_id"], self.PAYLOAD)
        assert one.holds is True and one.decidable is True
        assert one.made_on == "the far machine"
        # Both channels are named, and neither of them named itself.
        assert channels.TheGatewayItself.name in one.confirmed_by
        assert channels.TheFarMachineItself.name in one.confirmed_by

    def test_it_fails_when_what_ran_was_somewhere_else(self, a_record):
        gateway = AnAnsweringGateway(a_record("some-other-machine"))
        one = sentinels.what_the_far_machine_is(
            gateway, self.machine((True, 0, self.IDENTITY, "")), gateway.record["run_id"], self.PAYLOAD)
        assert one.decidable is True and one.holds is False
        assert "not on the machine that was asked" in one.why

    def test_a_machine_that_could_not_be_asked_decides_nothing(self, a_record):
        gateway = AnAnsweringGateway(a_record(self.IDENTITY))
        one = sentinels.what_the_far_machine_is(
            gateway, self.machine(trouble=OSError("no route")), gateway.record["run_id"], self.PAYLOAD)
        assert one.decidable is False and one.holds is False
        assert "could not be asked" in one.why

    def test_a_machine_that_says_nothing_decides_nothing(self, a_record):
        gateway = AnAnsweringGateway(a_record(self.IDENTITY))
        one = sentinels.what_the_far_machine_is(
            gateway, self.machine((True, 0, "   ", "")), gateway.record["run_id"], self.PAYLOAD)
        assert one.decidable is False and one.holds is False


class TestBothWaysOrNeither:

    def a_crossing(self, **changes):
        base = dict(what="x", made_on="the client", value="v", run_id="r" * 32,
                    payload_text="v", payload_sha256="a" * 64, confirmed_by="c", asked="a",
                    record_sha256="d" * 64, holds=True, decidable=True, why="")
        base.update(changes)
        return sentinels.Crossing(**base)

    def test_two_that_hold(self):
        held, why = sentinels.both_ways(self.a_crossing(), self.a_crossing(made_on="the far machine"))
        assert held is True and "separate channel" in why

    def test_one_that_does_not(self):
        held, why = sentinels.both_ways(self.a_crossing(),
                                        self.a_crossing(made_on="the far machine", holds=False))
        assert held is False and "did not cross" in why

    def test_undecided_is_not_rounded_to_no(self):
        held, why = sentinels.both_ways(
            self.a_crossing(),
            self.a_crossing(made_on="the far machine", decidable=False, holds=False,
                            why="the far machine could not be asked"))
        assert held is False
        assert "cannot be decided" in why
        assert "did not cross" not in why


class TestThisToolSaysWhichToolItIs:

    def test_it_has_an_identity_of_its_own(self):
        assert TOOL == "verification/2"

    def test_and_the_one_it_replaced_does_not_run(self):
        from agentnode_sdk.tools import external_run as frozen

        assert frozen.run([]) == 2
        assert frozen.main() == 2
        assert "agentnode_sdk.verification" in frozen.WHY

    def test_nothing_here_imports_the_frozen_one(self):
        from agentnode_sdk.verification import config, launcher, run, transport

        for module in (channels, sentinels, transport, config, launcher, run):
            source = inspect.getsource(module)
            assert "tools.external_run" not in source, module.__name__
            assert "tools import external_run" not in source, module.__name__
