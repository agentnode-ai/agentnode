"""Task #24: deploy/RISKS.md must say what the worker transport is, as the code has it.

RISKS.md said `from_address` "speaks unix sockets and refuses every other scheme" and that the
transport to a worker on another machine "is not written". The code has had one for a while:
`worker/topology.py` permits a `separate-worker-host` declaration over mutual TLS
(`remote-tcps`), `worker/remote.py` reaches it with `TlsWorker`, and
`deploy/separate-worker-host/` installs the two roles. What has NOT happened is a passing
two-machine measurement -- that README says so in as many words -- and the page has to keep
saying that, not swap one overclaim for another.

These checks are DERIVED FROM THE CODE: if the separate-worker-host topology ever stopped
permitting mutual TLS, the "must name it" half would stop applying instead of going stale the
other way. Forbidden sentences are assembled from halves so this file does not itself make them.
"""
from __future__ import annotations

import re
from pathlib import Path

SDK = Path(__file__).resolve().parents[1]
RISKS = SDK / "deploy" / "RISKS.md"


def _flowed() -> str:
    return " ".join(RISKS.read_text(encoding="utf-8").split()).lower()


def _the_code_has_a_network_transport() -> bool:
    from agentnode_sdk.worker import SEPARATE_WORKER_HOST
    from agentnode_sdk.worker import remote, topology

    return (topology.REMOTE_TCPS in topology.PERMITTED.get(SEPARATE_WORKER_HOST, ())
            and hasattr(remote, "TlsWorker"))


def test_the_code_does_have_a_transport_to_another_machine():
    """The premise, checked against the code rather than assumed."""
    assert _the_code_has_a_network_transport()


def test_the_page_does_not_say_the_transport_is_missing():
    if not _the_code_has_a_network_transport():
        return
    flowed = _flowed()
    for a, b in (("speaks unix sockets and refuses ", "every other scheme"),
                 ("the transport that would ", "is not written"),
                 ("is not something the shipped code ", "can do"),
                 ("cannot be configured into", "")):
        assert (a + b) not in flowed, "RISKS.md still says: " + a + b


def test_the_page_names_what_is_implemented_and_where():
    flowed = _flowed()
    for needle in ("mutual tls", "separate-worker-host", "tlsworker",
                   "worker/topology.py", "worker/remote.py",
                   "tests/test_the_worker_may_be_on_another_host.py",
                   "tests/test_mtls_transport.py", "tests/test_the_two_roles_are_separable.py",
                   "separate-worker-host/readme.md"):
        assert needle in flowed, needle


def test_the_page_keeps_saying_what_has_not_been_shown():
    """Naming the transport must not turn into a claim that the move is done."""
    flowed = _flowed()
    assert "has never passed" in flowed            # the two-machine measurement
    assert "not production-ready" in flowed and "not multi-tenant" in flowed
    for a, b in (("the worker ", "has been moved"), ("is now ", "production-ready"),
                 ("is now ", "isolated"), ("two-machine measurement ", "passed")):
        assert (a + b) not in flowed, a + b


def test_every_link_on_the_page_resolves():
    text = RISKS.read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text)
    assert links, "the page links nothing it relies on"
    for target in links:
        if target.startswith(("http://", "https://")):
            continue
        assert (RISKS.parent / target).resolve().exists(), target
