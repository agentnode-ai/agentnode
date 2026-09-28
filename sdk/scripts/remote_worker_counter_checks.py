"""Counter-checks for the remote-worker arc (profile `remote-worker-r1`, criterion R15).

Every test written for this arc is new, so all of them are trivially red against the parent --
the modules they import do not exist there. That establishes nothing about whether they would
NOTICE the defect coming back, which is the only thing a test is for.

So each check here takes ONE load-bearing property away from the code that has it, and requires:
the control is green first; the mutation LANDS (the file's digest moves and the new text is
present); a NAMED test fails, non-zero, with the reason the mutation predicts in its output;
named tests that must not be affected stay green; and the file is restored byte for byte,
verified by digest.

A check that goes red for a DIFFERENT reason establishes nothing and is reported as
DID NOT DISCRIMINATE. A check that could not be carried out is reported as NOT RUN with the
reason, never as passed.

Run from `sdk/`:  python scripts/remote_worker_counter_checks.py [--only ID ...] [--out DIR]

NOT A HOST-ISOLATION MEASUREMENT. Every property below is exercised in one process on one
kernel. R16 is measured on two real machines or not at all, and it has not been measured.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

HOST = "tests/test_the_worker_may_be_on_another_host.py::"
KEYS = "tests/test_one_key_per_pair.py::"
RAN = "tests/test_the_worker_remembers_what_it_ran.py::"
LOST = "tests/test_an_answer_that_was_lost_can_be_recovered.py::"
LEASE = "tests/test_only_one_control_plane_owns_a_worker.py::"
GONE = "tests/test_a_withdrawn_identity_stays_withdrawn.py::"
MACHINE = "tests/test_the_binding_describes_the_right_machine.py::"
STOP = "tests/test_a_stop_reaches_work_that_has_not_started.py::"
ROLES = "tests/test_the_two_roles_are_separable.py::"

CHECKS = [
    dict(id="remote-permits-only-mutual-tls",
         area="R1 -- with a unix socket back in the permitted set there is something to fall "
              "back to, which is the whole property",
         file="agentnode_sdk/worker/topology.py",
         edits=[("    SEPARATE_WORKER_HOST: (REMOTE_TCPS,),\n",
                 "    SEPARATE_WORKER_HOST: (REMOTE_TCPS, UNIX, LOOPBACK_TCPS),\n")],
         test=HOST + "TestThereIsNothingToFallBackTo::"
                     "test_and_asking_for_one_under_a_remote_declaration_is_refused",
         expect="DID NOT RAISE",
         green=[HOST + "TestTheAddressItself::"
                       "test_every_interface_is_refused_in_both_arrangements"]),

    dict(id="every-interface-is-not-an-address",
         area="R1 -- the one bind that would undo the arrangement: reachable from wherever the "
              "machine is, which on a cloud host is the internet",
         file="agentnode_sdk/worker/topology.py",
         # BOTH edits, and the second one is the point. Removing only the raise moves the
         # refusal to the topology rule -- 0.0.0.0 is then "not loopback", a single-host
         # declaration refuses it as a DISAGREEMENT, and the test goes red for the wrong reason.
         # The defect being modelled is a build that treats every interface as a local address.
         edits=[("    if ipaddress.ip_address(host).is_unspecified:\n", "    if False:\n"),
                ("        return ipaddress.ip_address(h).is_loopback\n",
                 "        return (ipaddress.ip_address(h).is_loopback\n"
                 "                or ipaddress.ip_address(h).is_unspecified)\n")],
         test=HOST + "TestTheAddressItself::"
                     "test_every_interface_is_refused_in_both_arrangements",
         expect="DID NOT RAISE",
         green=[HOST + "TestTheDeclarationAndTheAddressMustAgree::"
                       "test_a_loopback_address_declared_remote_is_refused"]),

    dict(id="a-key-belongs-to-one-pair",
         area="R2/R5 -- a keyring that answers with SOME key is a shared key with extra steps",
         file="agentnode_sdk/worker/pairkeys.py",
         edits=[("        found = self._pairs.get((str(gateway), str(worker)))\n",
                 "        found = self._pairs.get((str(gateway), str(worker))) or next(\n"
                 "            iter(self._pairs.values()), None)\n")],
         test=KEYS + "TestTheKeyBelongsToAPair::test_a_record_needs_both_names_to_be_found",
         expect="DID NOT RAISE",
         green=[KEYS + "TestRotationKeepsWorkInFlightReadable::"
                       "test_the_previous_key_is_accepted_during_the_overlap"]),

    dict(id="no-shared-key-across-the-boundary",
         area="R2/R5 -- the worker refuses to serve its own machine without a per-pair keyring",
         file="agentnode_sdk/worker/service.py",
         edits=[('    if topology == _topology.SEPARATE_WORKER_HOST and not keyring_path:\n',
                 '    if False:\n')],
         test=KEYS + "TestTheGlobalKeyDoesNotCrossTheBoundary::"
                     "test_a_remote_worker_without_a_keyring_refuses_to_serve",
         # Not "DID NOT RAISE": with the guard gone it raises something else, further on, and
         # WHERE is the property. The refusal is supposed to come before a runtime is touched,
         # so reaching `prove_its_ceilings` is exactly the defect this models.
         expect="prove_its_ceilings",
         green=[KEYS + "TestTheGlobalKeyDoesNotCrossTheBoundary::"
                       "test_and_locally_it_is_still_allowed"]),

    dict(id="a-caller-with-no-key-learns-nothing",
         area="R11 -- the refusal handed back the index of every pair the worker DOES hold",
         file="agentnode_sdk/worker/tls.py",
         # What is SAID, not the control flow: putting `if True:` in front of an `except` is a
         # syntax error, and a file that will not parse makes every test in the run red for a
         # reason that has nothing to do with the property. This restores the refusal text that
         # was there -- the exception's own message, which names every pair the worker holds.
         edits=[('for was closed: " + refused.cause)\n', 'for was closed: " + str(refused))\n')],
         test=KEYS + "TestOverTheRealTransport::"
                     "test_and_the_refusal_does_not_hand_back_the_keyrings_index",
         expect="and nothing else this worker holds is",
         green=[KEYS + "TestOverTheRealTransport::"
                       "test_a_job_crosses_authenticated_by_the_pair_key"]),

    dict(id="exactly-one-delivery-may-run",
         area="R6 -- the link() IS the irreversible decision; a rename silently overwrites",
         file="agentnode_sdk/worker/journal.py",
         edits=[("                try:\n"
                 "                    os.link(temporary, path)\n"
                 "                except FileExistsError:\n"
                 "                    os.unlink(temporary)\n"
                 "                    raise\n",
                 "                try:\n"
                 "                    os.replace(temporary, path)\n"
                 "                except FileExistsError:\n"
                 "                    os.unlink(temporary)\n"
                 "                    raise\n")],
         test=RAN + "TestTwoDeliveriesAtOnce::"
                    "test_only_one_of_many_simultaneous_identical_deliveries_may_run",
         expect="verdicts.count(J.FRESH) == 1",
         green=[RAN + "TestTheOrdinaryRun::test_a_fresh_run_is_claimed_and_settles"]),

    dict(id="the-same-name-for-different-work-is-refused",
         area="R6 -- resolving a conflict either way runs work nobody asked for under a name "
              "somebody else's answer will be read from",
         file="agentnode_sdk/worker/journal.py",
         edits=[("        if recorded != str(request_digest):\n", "        if False:\n")],
         test=RAN + "TestTheSameNameForDifferentWork::test_it_is_refused_rather_than_resolved",
         expect="DID NOT RAISE",
         green=[RAN + "TestTheSameJobTwice::test_an_identical_retry_does_not_run_again"]),

    dict(id="an-unknown-outcome-is-never-a-success",
         area="R6 -- a run that began and cannot be accounted for must not settle anything",
         file="agentnode_sdk/worker/__init__.py",
         edits=[("        return (not self.known) or bool(self.outcome) or self.never_ran\n",
                 "        return True\n")],
         test=LOST + "TestWhatTheRecoveredAnswerMeans::"
                     "test_an_unknown_outcome_does_not_settle_anything",
         expect="assert not True",
         green=[LOST + "TestTheWorkerCanBeAsked::test_a_run_it_finished"]),

    dict(id="an-old-epoch-stops-counting",
         area="R6/R9 -- without the fence two control planes both hold a heartbeat and both "
              "are obeyed",
         file="agentnode_sdk/worker/lease.py",
         edits=[("        if epoch is None or int(epoch) != held.epoch:\n", "        if False:\n")],
         # NOT `test_two_holders_are_never_valid_at_once`, which the first run used: with two
         # DIFFERENT gateways the holder check refuses the old one anyway, so that test stayed
         # green and the check measured nothing. The same gateway reconnecting is the case where
         # only the epoch can tell the two apart.
         test=LEASE + "TestFencing::test_an_old_epoch_from_the_same_gateway_is_refused",
         expect="DID NOT RAISE",
         green=[LEASE + "TestTime::test_a_lease_lapses_without_a_renewal"]),

    dict(id="a-restarted-worker-never-reissues-an-epoch",
         area="R6 -- reissuing a number makes a control plane from before the restart valid "
              "again",
         file="agentnode_sdk/worker/lease.py",
         edits=[("        self._write_counter(epoch)\n", "        pass\n")],
         test=LEASE + "TestARestartedWorker::test_and_never_issues_an_epoch_twice",
         expect="re-issuing a number",
         green=[LEASE + "TestTakingIt::test_the_worker_assigns_the_epoch_and_it_goes_up"]),

    dict(id="a-withdrawn-identity-stays-withdrawn",
         area="R4 -- revocation is by SERIAL, so a new certificate for the same name comes "
              "straight back and every other check passes",
         file="agentnode_sdk/pki/identity.py",
         edits=[("    withdrawn = getattr(trust, \"withdrawn\", None)\n", "    withdrawn = None\n")],
         # The source-reading test stayed green: the word is still in the file. What had to
         # exist first was a test that puts a real, valid, unrevoked certificate of a withdrawn
         # name through `check_peer` -- written for this check.
         test=GONE + "TestWhatAVerifierDoesWithIt::"
                     "test_a_real_certificate_of_a_withdrawn_identity_is_refused",
         expect="DID NOT RAISE",
         green=[GONE + "TestTheListIsAuthenticated::"
                       "test_a_list_this_deployments_ca_signed_is_believed"]),

    dict(id="an-unreadable-list-is-a-refusal-not-an-empty-one",
         area="R4 -- \"I could not read who is banned\" is not \"nobody is banned\"",
         file="agentnode_sdk/pki/trust.py",
         edits=[("        if self._tombstones_required:\n", "        if False:\n")],
         test=GONE + "TestWhatAVerifierDoesWithIt::"
                     "test_a_missing_list_where_one_is_required_is_a_refusal",
         expect="DID NOT RAISE",
         green=[GONE + "TestWhatAVerifierDoesWithIt::"
                       "test_and_where_none_is_configured_nothing_is_withdrawn"]),

    dict(id="the-binding-names-the-workers-own-machine",
         area="R3/R10 -- a boot id filled in from the asker describes the wrong kernel",
         file="agentnode_sdk/gateway/readiness.py",
         edits=[('    worker_boot_id: str = ""\n', '    worker_boot_id_renamed: str = ""\n')],
         test=MACHINE + "TestBothMachinesAreNamed::test_there_is_no_unqualified_boot_field_left",
         expect="worker_boot_id",
         green=[MACHINE + "TestTheWorkersBootComesFromTheWorker::test_the_interface_can_be_asked"]),

    dict(id="a-suspension-reaches-work-that-has-not-started",
         area="R9 -- the hole the gateway had documented against itself",
         file="agentnode_sdk/gateway/server.py",
         edits=[("    def drop_queued_work_that_is_no_longer_permitted(self) -> list:\n",
                 "    def drop_queued_work_that_is_no_longer_permitted(self) -> list:\n"
                 # `return []` rather than `return 0`: the control that must stay green asserts
                 # the result equals [], and a 0 would fail that too -- so the check would have
                 # reported DID NOT DISCRIMINATE for a reason of its own making.
                 "        return []\n")],
         test=STOP + "TestASuspensionReachesTheQueue::"
                     "test_a_waiting_job_for_a_suspended_account_is_dropped",
         expect="assert [] == ['run-0']",
         green=[STOP + "TestASuspensionReachesTheQueue::"
                       "test_a_waiting_job_for_an_account_in_good_standing_is_left_alone"]),

    dict(id="the-worker-does-not-need-the-control-plane",
         area="R14 -- the import-graph ratchet itself: it must notice a crossing coming back",
         file="agentnode_sdk/worker/service.py",
         edits=[("        from agentnode_sdk.machine import boot_identity\n",
                 "        from agentnode_sdk.gateway.boot import boot_identity\n")],
         test=ROLES + "TestTheImportGraph::test_the_boot_identity_is_nobodys_role",
         expect="assert 'agentnode_sdk.gateway.boot' not in",
         green=[ROLES + "TestTheImportGraph::test_and_the_old_name_still_works"]),

    # ---------------------------------------------------------------- added after round 1
    # An independent review reported four properties as having no counter-check -- R7, R8, R12
    # and the R14 artefacts -- and was right that naming a gap is not closing it. These six
    # close those four and cover the two R11 fixes that round found.

    dict(id="a-handshake-a-job-triggered-is-the-jobs-wait",
         area="R1/R6 -- a worker that accepts and then says nothing held the gateway for a flat "
              "minute before a job allowed one second was given up on. CI found it; the local "
              "suite could not, because the socket lane is skipped where there are no unix "
              "sockets",
         file="agentnode_sdk/worker/remote.py",
         edits=[("            self._describe(wait=min(wait, QUICK_SECONDS))\n",
                 "            self._describe()\n")],
         test=HOST + "TestTheTwoSidesAgreeOnAWireVersionFirst::"
                     "test_the_handshake_a_job_triggers_is_bounded_by_that_job",
         expect="the handshake was given",
         green=[HOST + "TestTheTwoSidesAgreeOnAWireVersionFirst::"
                       "test_and_a_handshake_nobody_asked_for_keeps_the_ordinary_allowance"]),

    dict(id="a-watch-with-nothing-to-watch-stops",
         area="R14 hygiene, found by CI: the client's watch thread re-read the trust files "
              "forever, because nothing on that side ever called stop()",
         file="agentnode_sdk/worker/tls.py",
         edits=[("            with self._lock:\n"
                 "                if not self._open:\n"
                 "                    self._thread = None\n"
                 "                    return\n", "            pass\n")],
         test=HOST + "TestAWatchWithNothingToWatch::"
                     "test_it_ends_once_the_last_connection_is_gone",
         expect="went on re-reading the trust files",
         green=[HOST + "TestAWatchWithNothingToWatch::"
                       "test_a_client_can_be_closed_and_says_nothing_afterwards"]),

    dict(id="a-duration-crosses-and-not-two-timestamps",
         area="R7 -- two machines do not share a clock, so their timestamps are not "
              "subtractable; what crosses is how long it ran",
         file="agentnode_sdk/worker/service.py",
         edits=[('        return {"known": True, "keeps_a_record": True, "state": known.state,\n',
                 '        return {"known": True, "keeps_a_record": True, "state": known.state,\n'
                 '                "started_at": 1.0, "finished_at": 2.0,\n')],
         test=LOST + "TestWhatIsBilledForARecoveredRun::"
                     "test_a_duration_crosses_and_not_two_timestamps",
         expect="'started_at' not in",
         green=[LOST + "TestWhatIsBilledForARecoveredRun::"
                       "test_the_worker_reports_how_long_it_actually_ran"]),

    dict(id="a-recovered-run-is-billed-for-what-it-ran",
         area="R7 -- otherwise an outage is charged to the customer as compute",
         file="agentnode_sdk/gateway/server.py",
         edits=[("                    record.finished_at = float(record.started_at) + float(settled.ran_for)\n",
                 "                    record.finished_at = _now()\n")],
         test=LOST + "TestWhatIsBilledForARecoveredRun::"
                     "test_the_end_time_of_a_recovered_run_is_not_when_the_gateway_gave_up",
         expect="if that line moved, this test has to follow it",
         green=[LOST + "TestWhatIsBilledForARecoveredRun::"
                       "test_the_record_says_which_clock_decided"]),

    dict(id="a-run-that-never-reached-the-worker-is-billed-nothing",
         area="R7 -- the defect producing the records found: an hour of outage on somebody's "
              "invoice for a run that never reached the machine",
         file="agentnode_sdk/gateway/server.py",
         edits=[("        if it_can_say and (getattr(settled, \"never_ran\", False) "
                 "or not settled.known):\n",
                 "        if it_can_say and getattr(settled, \"never_ran\", False):\n")],
         test=LOST + "TestARunThatNeverReachedTheWorkerIsNotBilledForTheOutage::"
                     "test_a_run_the_worker_has_no_record_of_is_billed_nothing",
         expect="for a run that never reached the worker",
         green=[LOST + "TestARunThatNeverReachedTheWorkerIsNotBilledForTheOutage::"
                       "test_and_one_that_did_run_is_billed_what_it_ran"]),

    dict(id="cleanup-that-cannot-be-proven-says-so",
         area="R8 -- \"nobody could establish it\" collapsed into \"it is gone\" is the one "
              "answer that must not be given",
         file="agentnode_sdk/worker/journal.py",
         edits=[("        state = (CLEANED if verified is True\n"
                 "                 else CLEANUP_UNPROVEN if verified is None else CLEANUP_PENDING)\n",
                 "        state = CLEANED\n")],
         test=LEASE + "TestWhenTheControlPlaneGoes::"
                      "test_cleanup_that_cannot_be_proven_is_recorded_as_that",
         expect="assert 'cleaned' == 'cleanup_unproven'",
         green=[LEASE + "TestWhenTheControlPlaneGoes::test_and_cleanup_that_is_proven_says_so"]),

    dict(id="a-refusal-carries-a-step",
         area="R12 -- a cause with nothing to do about it is an obstacle, not a refusal",
         file="agentnode_sdk/worker/topology.py",
         edits=[("            _what_to_do(declared, kind))\n", '            "")\n')],
         test=HOST + "TestTheGateStaysShutUnlessItIsOpened::"
                     "test_the_refusal_carries_its_cause_and_a_step",
         expect="assert ''",
         green=[HOST + "TestTheDeclarationAndTheAddressMustAgree::"
                       "test_a_remote_address_declared_local_is_refused"]),

    dict(id="the-worker-unit-declares-its-topology",
         area="R14 -- the artefacts had no mutation of their own; a unit that stopped declaring "
              "the arrangement would bind loopback on a machine of its own",
         file="deploy/separate-worker-host/worker-host.service",
         edits=[("ExecStart=/opt/agentnode/venv/bin/agentnode worker serve \\\n"
                 "    --topology separate-worker-host \\\n",
                 "ExecStart=/opt/agentnode/venv/bin/agentnode worker serve \\\n")],
         test=ROLES + "TestTheArtefacts::"
                      "test_the_worker_unit_declares_its_topology_and_opens_no_socket",
         expect="the command that SERVES must declare it",
         green=[ROLES + "TestTheArtefacts::test_the_control_plane_unit_has_no_worker_on_it"]),

    dict(id="the-enrolment-secret-does-not-outlive-it",
         area="R11 -- 0400 is a permission, not a lifetime",
         file="agentnode_sdk/pki/enrolment.py",
         edits=[("    gone = []\n", "    return []\n    gone = []\n")],
         test="tests/test_a_secret_does_not_outlive_its_use.py::TestTheIssuerDoesItOnDelivery::"
              "test_enrolling_a_service_leaves_nothing_behind",
         expect="the one-shot secret did not survive it",
         green=["tests/test_a_secret_does_not_outlive_its_use.py::TestTheResiduesGo::"
                "test_and_nothing_is_touched_before_it_is"]),

    dict(id="an-enrolment-secret-can-be-told-from-an-identifier",
         area="R11 -- 64 bare hex characters are a digest, a run id, a device id AND, until "
              "this, an enrolment secret; the scrubber cannot tell them apart",
         file="agentnode_sdk/pki/enrolment.py",
         edits=[('SECRET_PREFIX = "agentnode-enrol-1."\n', 'SECRET_PREFIX = ""\n')],
         test="tests/test_a_secret_does_not_outlive_its_use.py::"
              "TestAnEnrolmentSecretCanBeToldFromAnIdentifier::"
              "test_and_the_scrubber_takes_it_out_of_a_log_line",
         expect="assert",
         green=["tests/test_a_secret_does_not_outlive_its_use.py::"
                "TestAnEnrolmentSecretCanBeToldFromAnIdentifier::"
                "test_while_a_digest_a_run_id_and_a_device_id_all_survive"]),

    dict(id="cleanup-is-not-promised-unconditionally",
         area="R8 -- the records keep three cleanup states apart and the message a human reads "
              "promised the best one",
         file="agentnode_sdk/cli/remote_commands.py",
         edits=[('        print("  privileges. The gateway has measured this. The sandbox is '
                 'removed afterwards,")\n'
                 '        print("  and each run\'s record says whether that was confirmed -- '
                 'where it could not be,")\n'
                 '        print("  the record says so rather than assuming it.")\n',
                 '        print("  privileges, and will be cleaned up afterwards. The gateway '
                 'has measured this.")\n')],
         test="tests/test_a_secret_does_not_outlive_its_use.py::TestWhatIsPromisedAboutCleanup::"
              "test_the_client_message_does_not_promise_more_than_the_record",
         expect="cleaned up afterwards\" not in said",
         green=["tests/test_a_secret_does_not_outlive_its_use.py::"
                "TestWhatIsPromisedAboutCleanup::test_and_neither_does_the_operator_message"]),

    dict(id="the-worker-does-not-put-its-exception-on-the-wire",
         area="R11 -- an arbitrary string from the worker's process, crossing to a client, "
              "past a scrubber the worker cannot reach",
         file="agentnode_sdk/worker/service.py",
         edits=[("            self._refuse(connection, asked, wire.INTERNAL, "
                 "type(exc).__name__, key=key)\n",
                 "            self._refuse(connection, asked, wire.INTERNAL,\n"
                 "                         type(exc).__name__ + \": \" + str(exc), key=key)\n")],
         test="tests/test_a_secret_does_not_outlive_its_use.py::"
              "TestTheWorkerSaysWhatBrokeWithoutSayingWhatItHeld::test_the_refusal_a_caller_sees",
         expect="hunter2",
         green=["tests/test_a_secret_does_not_outlive_its_use.py::"
                "TestTheWorkerSaysWhatBrokeWithoutSayingWhatItHeld::"
                "test_the_operator_of_this_machine_still_gets_the_whole_thing"]),
]

#: Run pytest in a subprocess whose sys.path does not contain the OTHER checkout.
#:
#: The venv that carries the dependencies belongs to the main checkout, and its editable .pth
#: puts that checkout's `sdk` on sys.path. The main checkout has a `tests/__init__.py` and this
#: tree does not, so `tests` resolves there -- a regular package always beats a namespace one,
#: whatever the order -- and a counter-check would have mutated THIS tree and measured that one,
#: which is the worst possible failure for a harness whose whole job is to discriminate.
#: On CI there is one checkout and this is a no-op.
_BOOT = """
import os, sys
here = %r
sys.path = [p for p in sys.path
            if os.path.normcase(os.path.abspath(p or '.')) != os.path.normcase(here)
            or os.path.normcase(here) == os.path.normcase(%r)]
sys.path.insert(0, %r)
import agentnode_sdk
assert os.path.normcase(os.path.dirname(os.path.dirname(agentnode_sdk.__file__))) \
    == os.path.normcase(%r), agentnode_sdk.__file__
import pytest
sys.exit(pytest.main(sys.argv[1:]))
"""


def _boot(foreign: str) -> str:
    me = str(HERE)
    return _BOOT % (foreign, me, me, me)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pytest(nodeids: list[str]) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    foreign = os.environ.get("AGENTNODE_OTHER_CHECKOUT", "")
    argv = [sys.executable]
    argv += (["-c", _boot(foreign)] if foreign else ["-m", "pytest"])
    done = subprocess.run([*argv, "-q", "-p", "no:cacheprovider", "-o", "addopts=", *nodeids],
                          cwd=HERE, env=env, capture_output=True, text=True, timeout=900)
    return done.returncode, done.stdout + done.stderr


def run_one(check: dict) -> dict:
    path = HERE / check["file"]
    result = {"id": check["id"], "area": check["area"], "file": check["file"],
              "test": check["test"], "expect": check["expect"], "green": check["green"]}
    original = path.read_bytes()
    result["before_sha256"] = sha(original)

    code, out = pytest([check["test"], *check["green"]])
    result["control_exit"] = code
    if code != 0:
        result.update(verdict="NOT RUN", why="the control was not green", control_tail=out[-1500:])
        return result

    text = original.decode("utf-8")
    for old, new in check["edits"]:
        if text.count(old) != 1:
            result.update(verdict="NOT RUN",
                          why="the mutation's anchor occurs %d times" % text.count(old),
                          anchor=old[:200])
            return result
        text = text.replace(old, new)
    try:
        path.write_bytes(text.encode("utf-8"))
        mutated = path.read_bytes()
        result["mutated_sha256"] = sha(mutated)
        result["landed"] = (result["mutated_sha256"] != result["before_sha256"]
                            and all(new in mutated.decode("utf-8") for _, new in check["edits"]))
        code, out = pytest([check["test"]])
        result["mutated_exit"] = code
        result["mutated_tail"] = out[-2500:]
        result["failed_for_the_predicted_reason"] = code != 0 and check["expect"] in out
        green_code, green_out = pytest(check["green"])
        result["others_stayed_green"] = green_code == 0
        if green_code != 0:
            result["green_tail"] = green_out[-1500:]
    finally:
        path.write_bytes(original)
    result["restored_sha256"] = sha(path.read_bytes())
    result["restored_byte_exactly"] = result["restored_sha256"] == result["before_sha256"]
    ok = (result["landed"] and result["mutated_exit"] != 0
          and result["failed_for_the_predicted_reason"] and result["others_stayed_green"]
          and result["restored_byte_exactly"])
    result["verdict"] = "RED AS PREDICTED" if ok else "DID NOT DISCRIMINATE"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--out", default=str(HERE / "remote-worker-counter-check-results"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chosen = [c for c in CHECKS if not args.only or c["id"] in args.only]
    results = []
    for check in chosen:
        started = time.time()
        r = run_one(check)
        r["seconds"] = round(time.time() - started, 1)
        results.append(r)
        print("%-46s %s" % (r["id"], r["verdict"]), flush=True)
    (out / "counter-checks.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    lines = ["Counter-checks for `remote-worker-r1`, criterion R15.",
             "One process, one kernel: this establishes nothing about host isolation.", ""]
    for r in results:
        lines.append("%s  [%s]  %s" % (r["verdict"], r["id"], r["area"]))
        lines.append("    file %s  before %s" % (r["file"], r.get("before_sha256", "")[:16]))
        if "mutated_sha256" in r:
            lines.append("    mutated %s  landed=%s" % (r["mutated_sha256"][:16], r.get("landed")))
            lines.append("    named test %s" % r["test"])
            lines.append("    exit=%s  predicted reason %r present=%s"
                         % (r.get("mutated_exit"), r["expect"],
                            r.get("failed_for_the_predicted_reason")))
            lines.append("    others stayed green=%s" % r.get("others_stayed_green"))
            lines.append("    restored %s  byte-exact=%s"
                         % (r["restored_sha256"][:16], r.get("restored_byte_exactly")))
        else:
            lines.append("    " + r.get("why", ""))
    (out / "counter-checks.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = [r for r in results if r["verdict"] != "RED AS PREDICTED"]
    print("%d of %d red as predicted" % (len(results) - len(bad), len(results)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
