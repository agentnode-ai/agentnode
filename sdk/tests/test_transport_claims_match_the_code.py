"""Task #25: every place that describes the worker transport says what the code has.

#24 corrected deploy/RISKS.md. The same stale claim stood in three more places: the worker package
docstring ("This build speaks unix sockets and nothing else"), deploy/README.md ("a transport this
build does not have") and one test in test_socket_worker.py that REQUIRED the docstring to say the
transport is missing. The code has had one for a while -- `separate-worker-host` permits mutual TLS
only (worker/topology.py) and `TlsWorker` reaches it (worker/remote.py). What has never happened
is a passing two-machine measurement, and every one of these places has to keep saying that.

The premise is read from the code, and forbidden sentences are assembled from halves so that this
file does not itself make them.
"""
from __future__ import annotations

import inspect
from pathlib import Path

SDK = Path(__file__).resolve().parents[1]

STALE = [a + b for a, b in (
    ("speaks unix sockets and ", "nothing else"),
    ("refuses every other scheme in ", "as many words"),
    ("a transport this build ", "does not have"),
    ("needs a transport that ", "does not exist yet"),
    ("there is no transport here that ", "crosses a network"),
    ("the move needs one ", "to be written"),
    ("not something this build can be ", "configured into"),
)]


def _premise() -> bool:
    from agentnode_sdk.worker import SEPARATE_WORKER_HOST
    from agentnode_sdk.worker import remote, topology

    return (topology.REMOTE_TCPS in topology.PERMITTED.get(SEPARATE_WORKER_HOST, ())
            and hasattr(remote, "TlsWorker"))


def _flow(text: str) -> str:
    return " ".join(text.split()).lower()


def _docstring() -> str:
    from agentnode_sdk import worker

    return _flow(inspect.getdoc(worker) or "")


def _readme() -> str:
    return _flow((SDK / "deploy" / "README.md").read_text(encoding="utf-8"))


def test_the_code_has_a_transport_to_another_machine():
    assert _premise()


def test_the_worker_docstring_does_not_say_the_transport_is_missing():
    said = _docstring()
    for claim in STALE:
        assert claim not in said, claim


def test_the_deployment_readme_does_not_say_it_either():
    said = _readme()
    for claim in STALE:
        assert claim not in said, claim


def test_both_name_the_transport_and_what_has_not_been_shown():
    for where, said in (("worker/__init__.py", _docstring()), ("deploy/README.md", _readme())):
        assert "mutual tls" in said and "separate-worker-host" in said, where
        assert "tlsworker" in said, where
        from tests import two_machine_claim as claim

        assert not claim.problems(said), (where, claim.problems(said))   # r3 vs the current build
        assert "change to the product" in said, where     # what the move is, as everywhere else


def test_no_test_requires_the_transport_to_be_missing():
    """The third place: a test that demanded the stale sentence."""
    from tests import test_socket_worker as sw

    src = _flow(inspect.getsource(
        sw.TestNothingClaimsTheWorkerCanAlreadyBeMoved.test_and_what_is_missing_is_named))
    for demand in ('"does not exist"', '"needs a transport"', '"does not have"'):
        assert demand not in src, demand
