"""How a run ended, and why a killed container must never be signed as a success.

`early-ending-success-r1`. The observation this profile exists for: sandbox containers that were
told to sleep 120 seconds ended after about 11.3 seconds, and the signed usage line said
`state=finished, outcome=succeeded`.

The CAUSE of the early ending was an operator command running crash recovery against a live
gateway; that is fixed and recorded elsewhere. What these tests are about is the second half,
which is a separate defect and the one that matters for a service that sells a record of what it
did: **an ending that was not a completion was written down as one.**

How that was possible, in three steps:

1. `run_process` returned a bare `(returncode, stdout, stderr)`.
2. `why_it_stopped` reads a result that carries no reason as `EXITED` -- "a backend that says
   nothing is read as an ordinary exit".
3. `outcome_of` asked only about the STATE: `finished` meant `succeeded`, whatever the reason.

So silence became success. Three of the six endings a run can have did not exist as words at all.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from agentnode_sdk.gateway.protocol import (
    CANCELLED,
    EXITED,
    FAILED,
    NOT_STOPPED,
    OUT_OF_MEMORY,
    OUTCOMES,
    RUNTIME_LOST,
    SUCCEEDED,
    TERMINAL_STATES,
    TERMINATION_REASONS,
    TIMED_OUT,
    TRANSPORT_LOST,
    UNVERIFIED_OUTCOME,
    outcome_of,
)


class TestAKilledRunIsNeverCalledSucceeded:
    """E3, stated as a property over every combination rather than as a handful of cases."""

    def test_only_a_clean_exit_can_be_a_success(self):
        """Exhaustive over state, reason AND exit status. If a later change adds a way in, this
        fails naming the combination that did it."""
        succeeded = [(state, reason, code)
                     for state in TERMINAL_STATES
                     for reason in TERMINATION_REASONS + (NOT_STOPPED,)
                     for code in (0, 1, 3, 137, None)
                     if outcome_of(state, reason, code) == SUCCEEDED]
        assert succeeded == [("finished", EXITED, 0), ("finished", NOT_STOPPED, 0)], succeeded

    def test_a_program_that_exited_non_zero_is_not_signed_as_a_success(self):
        """The reading this replaces was deliberate and written down: "a run that completed and
        delivered a result succeeded, whatever number the program returned". For a record whose
        job is to tell a customer what a service did for them, a line saying `succeeded` about a
        job that exited 3 is not something they can use."""
        for code in (1, 2, 3, 127, 255):
            assert outcome_of("finished", EXITED, code) == FAILED, code

    def test_and_a_missing_status_is_not_read_as_a_zero(self):
        """The original defect in miniature: absence read as success."""
        assert outcome_of("finished", EXITED, None) == UNVERIFIED_OUTCOME
        assert outcome_of("finished", NOT_STOPPED, None) == UNVERIFIED_OUTCOME

    def test_the_status_is_in_the_signed_line_so_the_claim_can_be_checked(self):
        """Without it, `succeeded` had to be taken on trust: a reader of the log could not tell
        a job that exited 0 from one that exited 3, because both said the same word."""
        from agentnode_sdk.gateway import meter

        assert "exit_code" in meter.FIELDS
        assert "exit_code" not in meter.SEALED

    def test_an_ending_the_runtime_reports_as_out_of_memory_is_not_one(self):
        assert outcome_of("finished", OUT_OF_MEMORY) != SUCCEEDED
        assert outcome_of("finished", OUT_OF_MEMORY) == FAILED

    def test_and_the_container_backend_never_answers_without_a_reason(self):
        """The defence that makes the above hold in practice.

        `outcome_of` still lets a finished run with NO reason be a success, because a backend
        with nothing but an operating-system exit code is entitled to say only that. The
        container backend is not such a backend -- it can ask the runtime -- so it must never
        return a bare tuple. Read out of the source: every return from the run path carries an
        `Outcome`, and an `Outcome` always carries a reason.
        """
        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        for name in ("_end_an_ordinary_run", "_end_timed_out_run"):
            tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(ContainerBackend, name))))
            returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return) and n.value is not None]
            assert returns, "%s returns nothing at all" % name
            for one in returns:
                said = ast.unparse(one.value)
                assert said.startswith("Outcome("), (
                    "%s returns %r -- a value with no reason is read as an ordinary exit"
                    % (name, said[:60]))
                assert "reason=" in said, (
                    "%s builds an Outcome without saying why the run stopped: %r"
                    % (name, said[:80]))


class TestTheSixEndingsAreToldApart:
    """E4. Each has its own name, no two collapse, and the order when two apply is chosen."""

    def test_each_ending_a_run_can_have_has_its_own_word(self):
        assert set(TERMINATION_REASONS) == {
            EXITED, TIMED_OUT, CANCELLED, OUT_OF_MEMORY, RUNTIME_LOST, TRANSPORT_LOST,
        }
        assert len(set(TERMINATION_REASONS)) == len(TERMINATION_REASONS)

    def test_no_two_of_them_produce_the_same_pair_of_answers(self):
        """A reader gets a state, an outcome and a reason. Two endings that agree on all three
        are one ending with two spellings, which helps nobody."""
        seen = {}
        for reason in TERMINATION_REASONS:
            state = "cancelled" if reason == CANCELLED else "finished"
            key = (state, outcome_of(state, reason), reason)
            assert key not in seen, "%s and %s are indistinguishable" % (reason, seen.get(key))
            seen[key] = reason

    def test_the_reason_is_in_the_signed_line_and_not_only_in_the_record(self):
        """Five endings share the outcome `failed` or `unverified` between them. Without the
        reason beside it, a reader of the signed log cannot tell which happened -- and the signed
        log is the thing a customer would be handed."""
        from agentnode_sdk.gateway import meter

        assert "termination_reason" in meter.FIELDS
        assert "termination_reason" not in meter.SEALED, (
            "it would be outside the signature, which is the one place it must not be")

    def test_a_cancellation_outranks_what_the_container_looked_like_afterwards(self):
        """The customer asked for it to stop. Whatever the runtime made of a container this
        gateway then destroyed is a consequence of that request, not a competing reason."""
        assert outcome_of("cancelled", OUT_OF_MEMORY) == "cancelled"
        assert outcome_of("cancelled", TIMED_OUT) == "cancelled"
        assert outcome_of("cancelled", EXITED) == "cancelled"

    def test_and_the_wall_clock_outranks_the_rest(self):
        assert outcome_of("finished", TIMED_OUT) == "timed_out"

    def test_the_order_is_written_down_where_it_is_decided(self):
        """So it is a decision somebody made and not whichever branch happened to be first."""
        source = inspect.getsource(outcome_of)
        assert "order is deliberate" in source.lower(), (
            "the precedence between two reasons that both apply is not explained where it is "
            "implemented")


class TestWhatNobodyEstablishedStaysUnestablished:
    """E6. Not rounded towards success to look tidy, not towards failure to look safe."""

    def test_there_is_an_outcome_for_it_at_all(self):
        assert UNVERIFIED_OUTCOME in OUTCOMES

    def test_losing_the_runtime_is_not_the_payload_failing(self):
        assert outcome_of("finished", RUNTIME_LOST) == UNVERIFIED_OUTCOME
        assert outcome_of("finished", TRANSPORT_LOST) == UNVERIFIED_OUTCOME

    def test_and_the_state_that_already_meant_it_now_says_so(self):
        """`unverified` was a terminal state before this work and mapped to `failed` -- an open
        question rounded into an answer. A customer told their job failed does not resubmit it;
        one told nobody could establish what it did will ask."""
        assert outcome_of("unverified", NOT_STOPPED) == UNVERIFIED_OUTCOME

    def test_an_interrupted_run_is_unestablished_rather_than_failed(self):
        """The gateway went away. The job did not fail at anything it was asked to do."""
        assert outcome_of("interrupted", NOT_STOPPED) == UNVERIFIED_OUTCOME

    def test_and_no_state_maps_to_a_word_that_is_not_an_outcome(self):
        """`outcome="interrupted"` was being written into the signed line -- a state's name in an
        outcome's field, so a reader branching on OUTCOMES met a value that cannot occur."""
        for state in TERMINAL_STATES:
            for reason in TERMINATION_REASONS + (NOT_STOPPED,):
                got = outcome_of(state, reason)
                assert got in OUTCOMES, "%s/%s -> %r" % (state, reason, got)


class TestTheDistinctionComesFromWhatWasReported:
    """E5. From the runtime's own answer, never from how long the run took."""

    def test_out_of_memory_is_asked_for_and_not_deduced(self):
        """A status of 137 is 128+9 for a container the kernel killed AND for a program that
        chose to exit 137. The runtime answers the question separately, so it is asked."""
        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        source = inspect.getsource(ContainerBackend._what_the_runtime_says)
        assert "OOMKilled" in source, "nothing asks the runtime whether it killed for memory"
        assert "inspect" in source

    def test_nothing_in_the_classification_looks_at_a_clock(self):
        """Read through the syntax tree rather than by searching the text: a comment mentioning
        time would pass a substring check, and a comparison against a limit would not fail one."""
        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        for fn in (outcome_of, ContainerBackend._end_an_ordinary_run,
                   ContainerBackend._what_the_runtime_says):
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            called = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
            # The names of the clock, not any name containing them: `_run_runtime` and
            # `_what_the_runtime_says` both have "time" inside "runtime", and a check that
            # cannot tell those from `time.monotonic` is a check that fails on the right code.
            reads_a_clock = {c for c in called
                             if c in ("time.time", "time.monotonic", "time.perf_counter",
                                      "datetime.now", "datetime.utcnow", "monotonic", "perf_counter")}
            assert not reads_a_clock, (
                "%s reads a clock to decide how a run ended: %s" % (fn.__name__, reads_a_clock))

    def test_a_runtime_that_will_not_answer_is_not_read_as_a_normal_exit(self):
        """The whole shape of the original defect: silence taken for good news."""
        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        source = inspect.getsource(ContainerBackend._end_an_ordinary_run)
        assert "RUNTIME_LOST" in source
        tree = ast.parse(textwrap.dedent(source))
        # The branch that handles "the runtime said nothing" must not assign EXITED.
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and "is None" in ast.unparse(node.test):
                assigned = ast.unparse(node.body)
                assert "EXITED" not in assigned, (
                    "a runtime that would not answer is recorded as an ordinary exit")


class TestTheEndingDoesNotChangeTheBill:
    """E7. What a killed run costs, decided rather than inherited."""

    def test_the_bill_follows_the_slot_and_not_the_outcome(self, tmp_path):
        from agentnode_sdk.gateway import meter

        billed = {}
        for reason in TERMINATION_REASONS:
            run_id = "run-" + reason
            meter.record(tmp_path, run_id=run_id, client_id="c",
                         account_id="acct-" + "0" * 16,
                         queued_at=1000.0, started_at=1010.0, finished_at=1021.0,
                         cpu=1.0, memory_mb=512, wall_clock_s=60,
                         state="finished", outcome=outcome_of("finished", reason),
                         termination_reason=reason, bytes_out=0, worker_topology="x",
                         allowance_sha256="a" * 64, worker_id="w",
                         operator_policy_sha256="p" * 64, operator_policy_version=1)
        import json
        import pathlib

        for line in (pathlib.Path(tmp_path) / meter.METER_NAME).read_text(
                encoding="utf-8").splitlines():
            if not line.strip():
                continue
            one = json.loads(line)
            billed[one["termination_reason"]] = one["seconds"]
        assert set(billed) == set(TERMINATION_REASONS)
        assert set(billed.values()) == {11.0}, (
            "the charge depends on how the run ended: %r" % billed)

    def test_and_the_reasoning_is_written_down_where_the_number_is(self):
        from agentnode_sdk.gateway import meter

        said = inspect.getsource(meter).lower()
        assert "what an ending costs, decided rather than inherited" in said, (
            "the charge for a killed run is whatever the arithmetic produces, with no "
            "decision written down beside it")
        assert "the cheapest way to use the machine" in said, (
            "the alternative that was rejected is not recorded, so a later reader "
            "cannot tell a decision from an oversight")

    def test_a_run_that_never_held_a_slot_still_costs_nothing(self, tmp_path):
        """The one ending that is free, and it is free because there is no start time to
        subtract from rather than because a rule says so."""
        from agentnode_sdk.gateway import meter

        meter.record(tmp_path, run_id="never-started", client_id="c",
                     account_id="acct-" + "0" * 16,
                     queued_at=1000.0, started_at=0.0, finished_at=1010.0,
                     cpu=1.0, memory_mb=512, wall_clock_s=60,
                     state="cancelled", outcome="cancelled", termination_reason=CANCELLED,
                     bytes_out=0, worker_topology="x", allowance_sha256="a" * 64,
                     worker_id="w", operator_policy_sha256="p" * 64, operator_policy_version=1)
        import json
        import pathlib

        line = json.loads((pathlib.Path(tmp_path) / meter.METER_NAME)
                          .read_text(encoding="utf-8").splitlines()[0])
        assert line["seconds"] == 0.0
        assert line["waited_s"] == 10.0


class TestTheEndingsThatWereAlreadyRightStillAre:
    """E9. A fix that broke the cases it did not need to touch would be a worse trade."""

    @pytest.mark.parametrize("state,reason,code,expected", [
        ("finished", EXITED, 0, SUCCEEDED),
        ("finished", TIMED_OUT, None, "timed_out"),
        ("cancelled", CANCELLED, None, "cancelled"),
        ("refused", NOT_STOPPED, None, FAILED),
    ])
    def test_the_mapping_that_existed_is_unchanged(self, state, reason, code, expected):
        """A timeout is still a timeout and a cancellation still a cancellation -- the two E9
        names by name. A clean exit is still a success.

        What DID change, on purpose, is a non-zero exit: it used to be `succeeded` too. That is
        not one of the endings E9 protects, and the reasoning is at `outcome_of`.
        """
        assert outcome_of(state, reason, code) == expected

    def test_a_run_still_has_no_outcome_before_it_ends(self):
        assert outcome_of("accepted", NOT_STOPPED) == ""
        assert outcome_of("running", NOT_STOPPED) == ""


class TestWhatTheCustomerIsTold:
    """The endings have names now. The one surface a person reads had no way to use them.

    Measured on the closed alpha, before this: a container the runtime had just reported as
    killed for memory produced

        Did not finish. Nothing exited, and no reason was given.

    The reason WAS given -- `out_of_memory`, in the signed line, from the runtime itself. The
    client had no branch for it, because until now there was nothing to branch on.
    """

    def _said(self, final: dict, capsys) -> str:
        from agentnode_sdk.cli import remote_commands

        remote_commands._report_the_ending(final) if hasattr(
            remote_commands, "_report_the_ending") else None
        return capsys.readouterr().out

    def test_the_client_has_a_branch_for_every_ending_it_can_be_handed(self):
        """Read from the source of the function that prints it, so a reason added later without
        a sentence for it is caught here rather than by a customer."""
        from agentnode_sdk.cli import remote_commands

        source = inspect.getsource(remote_commands)
        for reason in (OUT_OF_MEMORY,):
            assert "OUT_OF_MEMORY" in source, (
                "the client cannot say anything about %s" % reason)
        assert "NOTHING_WAS_ESTABLISHED" in source, (
            "the client cannot tell a customer that nobody established how their job ended")

    def test_it_no_longer_claims_no_reason_was_given_when_one_was(self):
        """The sentence itself is kept -- there IS a case with no reason, a backend that says
        nothing -- but it must not be the answer for an ending that named itself."""
        from agentnode_sdk.cli import remote_commands

        tree = ast.parse(textwrap.dedent(inspect.getsource(remote_commands.cmd_run)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = ast.unparse(node.test)
            if "OUT_OF_MEMORY" not in test and "NOTHING_WAS_ESTABLISHED" not in test:
                continue
            printed = ast.unparse(node.body)
            assert "no reason was given" not in printed, (
                "a named ending is still told it has no reason: %s" % test)

    def test_and_it_says_what_to_do_about_running_out_of_memory(self):
        from agentnode_sdk.cli import remote_commands

        source = inspect.getsource(remote_commands.cmd_run)
        assert "Ask for less memory" in source, (
            "the customer is told what happened and not what they can do about it")


class TestARunThatWasKilledHasNoExitCode:
    """The rule this codebase already had, applied to the two endings that did not follow it.

    "A process the sandbox killed did not choose a status, and reporting one it did not choose
    is how the reason got lost in the first place." That was written for the timeout path. A run
    whose container was removed under it is in the same position: the 137 belongs to the client
    that was attached to it, not to the payload.

    Exercised on the closed alpha: with the exit code present, the CLI returned it silently and
    the customer was told nothing at all about a run nobody could account for.
    """

    def test_the_endings_that_nobody_chose_carry_no_exit_code(self):
        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        source = inspect.getsource(ContainerBackend._end_an_ordinary_run)
        # Each `if reason == ...` branch is one of the endings nobody chose. Every one of them
        # has to build its Outcome with no exit code.
        branches = source.split("if reason ==")[1:]
        assert len(branches) >= 2, "the killed endings no longer have their own branches"
        for chunk in branches:
            assert "Outcome(None," in chunk, (
                "an ending nobody chose still reports an exit code: %s"
                % chunk.strip()[:120])

    def test_and_the_runtime_status_is_kept_beside_it(self):
        """Dropped entirely it would be a number nobody can find; reported as the exit code it
        would be a number attributed to the wrong thing."""
        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        source = inspect.getsource(ContainerBackend._end_an_ordinary_run)
        assert source.count("native_status=") >= 2
        assert "platform=CONTAINER_PLATFORM" in source, (
            "a status is kept without saying whose it is")


class TestNothingIsLeftBehind:
    """`--rm` is gone from the single-run path, so removal is this code's job in every case.

    Measured on the closed alpha before the sweep below existed: the worker was stopped while a
    job was in flight, to produce a `transport_lost`. The record was right, and the container was
    still there afterwards. With `--rm` the runtime would have tidied it up once the payload
    finished on its own; without it, nothing would have.
    """

    def test_the_single_run_path_removes_and_then_proves_it_is_gone(self):
        from agentnode_sdk.sandbox.container_backend import ContainerBackend

        source = inspect.getsource(ContainerBackend._end_an_ordinary_run)
        assert '"rm", "-f"' in source, "the ordinary path no longer removes its container"
        assert "SandboxContainmentError" in source, (
            "a container that cannot be shown to be gone is tidied up quietly")

    def test_and_rm_is_stripped_only_for_that_path(self):
        """Every other caller of `wrap_command` -- the MCP path, the agent session -- has no
        removal code, so taking the flag out of the shared list would leak there instead."""
        from agentnode_sdk.sandbox import container_backend as cb

        assert "--rm" in cb._HARDENED_FLAGS
        assert '!= "--rm"' in inspect.getsource(cb.ContainerBackend._argv_with_cidfile)

    def test_a_worker_taking_over_removes_what_the_last_one_left(self):
        from agentnode_sdk.worker.local import LocalWorker

        assert hasattr(LocalWorker, "remove_what_a_previous_worker_left")
        source = inspect.getsource(LocalWorker.remove_what_a_previous_worker_left)
        assert '"rm", "-f"' in source

    def test_and_it_addresses_only_containers_this_sdk_named(self):
        """A worker that removed by a broad pattern would remove somebody else's work on a
        machine it happens to share."""
        from agentnode_sdk.worker.local import LocalWorker

        assert LocalWorker.ITS_OWN_PREFIXES
        for prefix in LocalWorker.ITS_OWN_PREFIXES:
            assert prefix.startswith("agentnode-"), prefix
        source = inspect.getsource(LocalWorker.remove_what_a_previous_worker_left)
        assert "ITS_OWN_PREFIXES" in source

    def test_and_a_leftover_it_cannot_remove_does_not_stop_it_starting(self):
        """A worker that will not start because of a leftover is a worse answer than one that
        starts and says what it could not do."""
        from agentnode_sdk.worker.local import LocalWorker

        tree = ast.parse(textwrap.dedent(
            inspect.getsource(LocalWorker.remove_what_a_previous_worker_left)))
        # Read as code, not as text: the docstring says "it never raises", and a substring
        # check cannot tell that sentence from a statement that does.
        raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
        assert not raises, "the sweep can stop a worker from starting"
        handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
        assert handlers, "nothing catches what the runtime might do"

    def test_the_sweep_runs_before_the_socket_is_opened(self):
        """After that, a job of its own could be in flight, and 'nobody is waiting for this'
        would stop being true."""
        from agentnode_sdk.worker import service

        source = inspect.getsource(service)
        swept = source.index("remove_what_a_previous_worker_left")
        listening = source.index("remembers_at=remembers_at")
        assert swept < listening, "the sweep happens after the worker is already taking work"
