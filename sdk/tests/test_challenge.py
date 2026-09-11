"""The value a gateway makes for one run: what it is bound to, where it goes, and what is kept.

`EM3C-E7-RECORD-0001`: the seventh external run tried to show that a job had run on the far machine
by having it print `/etc/machine-id` from inside the sandbox and comparing that with what the
machine said about itself. A container does not share that file with its host, so two correct
answers disagreed. `EM3C-CROSSING-DECISION-0001` chose a challenge the gateway issues instead, and
this file is about the gateway's half of it.

The tool's half -- what a client checks before crediting the value -- is in
`test_verification_channels.py`. What is here is what the gateway does: makes one, binds it, puts
it somewhere the job can read and nothing on the host can, writes down its digest before the job
starts, and forgets the value when the run ends.
"""
from __future__ import annotations

import inspect
import json
import time

import pytest

from agentnode_sdk.gateway import challenge as ch
from agentnode_sdk.gateway import client as gc

from tests.test_em3c_gateway import StandInBackend, _granted, _paired  # noqa: F401
from tests.test_em3c_gateway import gateway  # noqa: F401


@pytest.fixture()
def a_gateway(gateway):  # noqa: F811
    """The real gateway from the suite next door, under a name a test can take as an
    argument without shadowing the fixture it came from."""
    return gateway


def a_binding(**changes) -> ch.Binding:
    made = dict(run_id="r" * 32, gateway_id="gw-1", backend_instance="StandInBackend:abcd",
                effective_policy_sha256="p" * 64, value="a1b2c3d4e5f60718", delivered=True)
    made.update({k: v for k, v in changes.items() if k in made})
    binding = ch.bind(**made)
    for field, value in changes.items():
        if field not in made:
            binding = ch.Binding(**{**binding.as_dict(), field: value})
    return binding


class TestWhatIsWrittenDownAndWhatIsNot:

    def test_the_binding_has_no_room_for_the_value(self):
        """Not a convention. There is no field for it, so nothing can put one there."""
        assert "challenge" not in ch.FIELD_NAMES
        assert "value" not in ch.FIELD_NAMES
        assert "challenge_sha256" in ch.FIELD_NAMES

    def test_and_the_value_is_not_in_what_it_serialises_to(self):
        value = "a1b2c3d4e5f60718"
        written = json.dumps(a_binding(value=value).as_dict())
        assert value not in written
        assert ch.digest_of(value) in written

    def test_binding_it_says_what_it_belongs_to(self):
        binding = a_binding()
        assert binding.run_id == "r" * 32
        assert binding.gateway_id == "gw-1"
        assert binding.backend_instance == "StandInBackend:abcd"
        assert binding.effective_policy_sha256 == "p" * 64
        assert binding.expires_at > binding.issued_at

    def test_two_challenges_are_not_the_same_challenge(self):
        made = {ch.a_fresh_challenge() for _ in range(200)}
        assert len(made) == 200
        assert all(len(one) == ch.CHALLENGE_BYTES * 2 for one in made)

    def test_a_document_this_build_does_not_describe_is_refused(self):
        with pytest.raises(ch.ChallengeError) as caught:
            ch.read({**a_binding().as_dict(), "surprise": 1})
        assert "does not describe" in str(caught.value)

    def test_a_document_missing_what_it_needs_is_refused(self):
        for name in ch.FIELD_NAMES:
            if name == "not_delivered_because":
                continue
            document = a_binding().as_dict()
            document.pop(name)
            with pytest.raises(ch.ChallengeError) as caught:
                ch.read(document)
            assert name in str(caught.value)

    def test_something_that_is_not_a_document(self):
        for value in ([], "a string", 3, None):
            with pytest.raises(ch.ChallengeError):
                ch.read(value)


class TestWhatMakesItHoldAndWhatDoesNot:

    ASKED = dict(run_id="r" * 32, gateway_id="gw-1", effective_policy_sha256="p" * 64,
                 value="a1b2c3d4e5f60718")

    def test_the_one_that_was_issued_holds(self):
        assert ch.why_it_does_not_hold(a_binding(), **self.ASKED) == ""

    def test_a_value_that_is_not_it(self):
        why = ch.why_it_does_not_hold(a_binding(), **{**self.ASKED, "value": "f" * 16})
        assert "not what this gateway issued" in why

    def test_another_run(self):
        why = ch.why_it_does_not_hold(a_binding(run_id="q" * 32), **self.ASKED)
        assert "says nothing about this one" in why

    def test_another_gateway(self):
        why = ch.why_it_does_not_hold(a_binding(gateway_id="somebody-else"), **self.ASKED)
        assert "was paired with" in why

    def test_another_policy(self):
        why = ch.why_it_does_not_hold(a_binding(effective_policy_sha256="q" * 64), **self.ASKED)
        assert "under policy" in why

    def test_one_that_has_expired(self):
        binding = ch.Binding(**{**a_binding().as_dict(), "expires_at": 1000.0})
        why = ch.why_it_does_not_hold(binding, **self.ASKED, now=1000.0 + 10_000)
        assert "expired" in why

    def test_and_a_little_skew_is_not_expiry(self):
        binding = ch.Binding(**{**a_binding().as_dict(), "expires_at": 1000.0})
        assert ch.why_it_does_not_hold(binding, **self.ASKED, now=1000.0 + 10) == ""

    def test_one_that_never_reached_the_job(self):
        binding = a_binding(value="", delivered=False, not_delivered_because=ch.BROUGHT_ITS_OWN_COMMAND)
        why = ch.why_it_does_not_hold(binding, **{**self.ASKED, "value": ""})
        assert "brought its own command" in why

    def test_nothing_came_back(self):
        why = ch.why_it_does_not_hold(a_binding(), **{**self.ASKED, "value": ""})
        assert "nothing came back" in why


class TestWhereTheValueTravels:
    """`EM3C-CROSSING-DECISION-0001`, F-A-ARGV-EXPOSURE: a value on the container runtime's
    command line is one anybody listing processes on the host can read."""

    def test_the_bootstrap_reads_it_off_standard_input(self):
        command = ch.bootstrap(command_was_given=False)
        joined = " ".join(command)
        assert "sys.stdin.readline()" in joined
        assert ch.INSIDE_THE_SANDBOX in joined
        # The NAME is on the command line, which is what a name is for. No value is.
        assert "AGENTNODE_RUN_CHALLENGE" in joined

    def test_a_job_that_brought_its_own_command_gets_no_bootstrap(self):
        assert ch.bootstrap(command_was_given=True) == []

    def test_what_the_sandbox_reads_is_the_value_then_the_job(self):
        assert ch.on_stdin("abcd", "cGF5bG9hZA==") == "abcd\ncGF5bG9hZA==".replace("\\n", "\n")

    def test_the_bootstrap_really_does_what_it_says(self):
        """Run it, for real, as a child process -- with the challenge on its standard input and
        the job in base64 after it."""
        import base64
        import subprocess
        import sys

        job = base64.b64encode(
            b"import os;print('SAW', os.environ['" + ch.INSIDE_THE_SANDBOX.encode() + b"'])"
        ).decode("ascii")
        command = ch.bootstrap(command_was_given=False)
        done = subprocess.run([sys.executable] + command[1:], capture_output=True, timeout=120,
                              input=ch.on_stdin("a1b2c3d4", job).encode("utf-8"))
        out = done.stdout.decode("utf-8", "replace")
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
        assert "SAW a1b2c3d4" in out

    def test_the_gateway_puts_no_value_in_the_spec(self, a_gateway):
        """The spec is what becomes the runtime's command line. Nothing of the challenge is in
        it -- not under any key, not under any name."""
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="in-the-spec")
        gc.wait_for(conn, "in-the-spec", timeout=20)
        assert backend.specs, "the job never reached the sandbox"
        binding = ch.read(service.ledger.challenge_for("in-the-spec"))
        for spec in backend.specs:
            everything = (list(spec.command) + list((spec.env or {}).values())
                          + list((spec.env or {}).keys()) + [spec.name or ""])
            for piece in everything:
                assert ch.digest_of(str(piece)) != binding.challenge_sha256, piece
            # And nothing that merely looks like it either: no part of what becomes the runtime's
            # command line is a value of this shape at all.
            for value in (spec.env or {}).values():
                assert not (len(str(value)) == ch.CHALLENGE_BYTES * 2
                            and all(c in "0123456789abcdef" for c in str(value))), value


class TestWhatTheGatewayDoes:

    def test_it_writes_the_binding_down_before_the_job_ends(self, a_gateway):
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="written-down")
        gc.wait_for(conn, "written-down", timeout=20)
        binding = ch.read(service.ledger.challenge_for("written-down"))
        assert binding.run_id == "written-down"
        assert binding.gateway_id == state.identity.gateway_id
        assert binding.delivered is True
        assert len(binding.challenge_sha256) == 64
        assert binding.backend_instance.startswith("StandInBackend:")

    def test_the_value_is_gone_when_the_run_has_ended(self, a_gateway):
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="forgotten")
        gc.wait_for(conn, "forgotten", timeout=20)
        assert service.runs["forgotten"].challenge == ""
        # And it was never in the ledger to begin with.
        assert "challenge_sha256" in service.ledger.challenge_for("forgotten")
        assert json.dumps(service.ledger.challenge_for("forgotten")).count("a1b2") == 0

    def test_it_is_not_in_what_a_client_may_see(self, a_gateway):
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="not-public")
        answer = gc.wait_for(conn, "not-public", timeout=20)
        assert "challenge" not in answer
        assert "challenge" not in service.runs["not-public"].public()

    def test_a_job_that_brings_its_own_command_is_told_so(self, a_gateway):
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="own-command",
                  command=("python", "-c", "print(1)"))
        gc.wait_for(conn, "own-command", timeout=20)
        binding = ch.read(service.ledger.challenge_for("own-command"))
        assert binding.delivered is False
        assert binding.challenge_sha256 == ""
        assert "brought its own command" in binding.not_delivered_because

    def test_and_its_standard_input_is_left_alone(self, a_gateway):
        """Putting something on it would be altering the job."""
        import base64

        base, state, service, backend = a_gateway
        seen: list = []

        real = backend.run_process

        def watching(spec, input_text=None, timeout=120.0):
            seen.append(input_text)
            return real(spec, input_text=input_text, timeout=timeout)

        backend.run_process = watching
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="untouched",
                  command=("python", "-c", "print(1)"))
        gc.wait_for(conn, "untouched", timeout=20)
        assert seen and seen[0] == base64.b64encode(b"print('x')").decode("ascii")

    def test_two_runs_get_two_challenges(self, a_gateway):
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        for run in ("first", "second"):
            gc.submit(conn, b"print('x')", granted=_granted(service), run_id=run)
            gc.wait_for(conn, run, timeout=20)
        one = ch.read(service.ledger.challenge_for("first"))
        other = ch.read(service.ledger.challenge_for("second"))
        assert one.challenge_sha256 != other.challenge_sha256

    def test_resubmitting_a_run_does_not_issue_another(self, a_gateway):
        """A replay is refused before anything is issued, so the first binding stands."""
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="once-only")
        gc.wait_for(conn, "once-only", timeout=20)
        first = dict(service.ledger.challenge_for("once-only"))
        again = gc.submit(conn, b"print('x')", granted=_granted(service), run_id="once-only")
        assert again["state"] == "refused" and "replay" in str(again.get("refusal"))
        assert service.ledger.challenge_for("once-only") == first


class TestTheReadOnlySurface:
    """`EM3C-CROSSING-DECISION-0001`, F-A-READ-SURFACE."""

    def command(self, root, **kw):
        import types

        from agentnode_sdk.cli import gateway_commands

        return gateway_commands.cmd_challenge(
            types.SimpleNamespace(dir=str(root), **kw))

    def test_it_answers_about_the_run_it_is_asked_about(self, a_gateway, capsys):
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="asked-about")
        gc.wait_for(conn, "asked-about", timeout=20)
        assert self.command(state.root, run="asked-about") == 0
        printed = json.loads(capsys.readouterr().out)
        assert printed["run_id"] == "asked-about"
        assert set(printed) == set(ch.FIELD_NAMES)

    def test_it_refuses_to_answer_about_no_run_in_particular(self, a_gateway, capsys):
        base, state, _service, _backend = a_gateway
        assert self.command(state.root, run="") == 2
        assert "one run" in capsys.readouterr().out

    def test_a_run_it_has_nothing_for(self, a_gateway, capsys):
        base, state, _service, _backend = a_gateway
        assert self.command(state.root, run="never-heard-of-it") == 1
        assert "nothing written down" in capsys.readouterr().out

    def test_it_cannot_be_asked_to_list(self):
        import inspect as look

        from agentnode_sdk.cli import gateway_commands

        source = look.getsource(gateway_commands.cmd_challenge)
        for shape in ("for run", "runs.items", "keys()", "--all", "glob"):
            assert shape not in source, shape

    def test_it_changes_nothing(self, a_gateway):
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="unchanged")
        gc.wait_for(conn, "unchanged", timeout=20)
        ledger = state.root / "ledger.json"

        def settled():
            """The bytes on disk, once the gateway has finished putting them there.

            Windows will not let a file be read while it is being replaced, and the worker
            writes the run's last state after the answer comes back. A retry here is about that
            and nothing else -- what is being established is that the READ-ONLY command changed
            nothing, which needs a before and an after that are both readable."""
            for _ in range(50):
                try:
                    return ledger.read_bytes()
                except PermissionError:
                    time.sleep(0.05)
            return ledger.read_bytes()

        before = settled()
        self.command(state.root, run="unchanged")
        assert settled() == before

    def test_the_ledger_reader_answers_about_one_run(self):
        from agentnode_sdk.gateway import ledger as module

        taken = list(inspect.signature(module.Ledger.challenge_for).parameters)
        assert taken == ["self", "run_id"]


class TestTheSandboxIsNotWeakened:

    def test_nothing_new_is_mounted(self, a_gateway):
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        gc.submit(conn, b"print('x')", granted=_granted(service), run_id="no-mounts")
        gc.wait_for(conn, "no-mounts", timeout=20)
        for spec in backend.specs:
            assert list(spec.mounts) == [], spec.mounts
            assert spec.clean_home is True

    def test_nothing_reaches_for_the_runtime_socket_or_a_host_file(self):
        # From after the module's own docstring, which NAMES the file the seventh run compared
        # because that is the defect it exists to describe. A check that cannot tell a
        # description from a use is a check about prose.
        whole = inspect.getsource(ch)
        opened = whole.index('"""')
        source = whole[whole.index('"""', opened + 3) + 3:]
        for shape in ("docker.sock", "/etc/machine-id", "/var/run", "open(", "Path("):
            assert shape not in source, shape

    def test_the_challenge_is_not_a_credential(self):
        """It is stated where a reader will meet it, because a value that travels looks like one
        until somebody says otherwise."""
        said = inspect.getdoc(ch) or ""
        assert "NOT a credential" in said
        assert "does NOT protect against a gateway that lies" in said
        assert "does NOT identify the physical machine" in said
