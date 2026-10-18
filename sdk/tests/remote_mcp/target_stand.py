"""A catalog stand with execution targets: the real register, trust acks, router, transport and
adapter; one test-double worker per target (journal-deduplicating, identity-proving)."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp.server import build_catalog_adapter
from agentnode_sdk.remote_mcp.target_router import TargetRouter
from agentnode_sdk.worker import Isolation, Outcome, WorkerUnreachable

from .catalog_fixtures import NS, Stand, raw_pub
from .target_fixtures import attestation, customer_spec, managed_spec

REGEX = "regex_builder_pack__data_cleaning"
REDACT = "document_redaction_pack__document_parsing"
CITE = "citation_manager_pack__citation_extraction"
REGEX_ARGS = {"pattern": r"\d+", "test_string": "a1 b22", "operation": "findall"}
CAP_REGEX = NS + "/regex-builder-pack"


class _Peer(Exception):
    """Stands in for pki.identity.PeerRefused in the cause chain (it carries `check`)."""

    def __init__(self, check):
        super().__init__(check)
        self.check = check


@dataclass
class Recovered:
    known: bool
    keeps_a_record: bool = True
    state: str = ""
    outcome: object = None
    never_ran: bool = False
    unknown_outcome: bool = False


class TargetWorker:
    """One target's worker. Runs the job's program locally (a TEST DOUBLE, not a sandbox), keeps a
    journal by run id like the real worker, proves its identity per run."""

    def __init__(self, uri: str, instance: str):
        self.uri, self.instance = uri, instance
        self.address = "tcps://test/" + instance
        self.starts = 0                      # programs actually started
        self.journal: dict[str, dict] = {}
        self.offline = False
        self.wrong_identity = False
        self.lose_after_start = False        # the connection drops after the job ran
        self.gate: threading.Event | None = None
        self.started = threading.Event()
        self._ran_on: dict[str, str] = {}

    def can_it_isolate(self):
        return Isolation(available=True, backend="self-reported-podman")

    def confirm_reachable(self, budget=None):
        if self.offline:
            raise WorkerUnreachable("could not be reached: connection refused")
        if self.wrong_identity:
            try:
                raise _Peer("instance")
            except _Peer as peer:
                raise WorkerUnreachable("identity refused") from peer

    def run(self, job):
        if self.offline:
            raise WorkerUnreachable("could not be reached")
        self._ran_on[job.run_id] = "agentnode://other/worker/x" if self.wrong_identity else self.uri
        if job.run_id in self.journal:
            rec = self.journal[job.run_id]
            return Outcome(exit_code=rec["exit_code"], stdout=rec["stdout"], stderr="")
        self.starts += 1
        self.started.set()
        if self.gate is not None:
            self.gate.wait(30)
        stub = job.command[2]
        proc = subprocess.run([sys.executable, "-c", stub], input=job.stdin, text=True, capture_output=True,
                              timeout=60)
        self.journal[job.run_id] = {"exit_code": proc.returncode, "stdout": proc.stdout}
        if self.lose_after_start:
            raise WorkerUnreachable("the connection carrying the run was lost")
        return Outcome(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def who_ran(self, run_id):
        uri = self._ran_on.get(run_id, "")
        return "mtls", uri, self.instance if uri else ""

    def result(self, run_id, wait=10.0):
        rec = self.journal.get(run_id)
        if rec is None:
            return Recovered(known=True, never_ran=True)
        return Recovered(known=True, state="finished", outcome={"stdout": rec["stdout"], "exit_code": rec["exit_code"]})


class TargetStand(Stand):
    """Stand + register (managed `managed-w1` + whatever the test adds) + router over test workers."""

    #: Rate budgets are exercised by their own tests; here they must not decide the outcome.
    GENEROUS = dict(calls_per_minute_per_principal=1000, calls_per_minute_per_token=1000, call_burst=200,
                    call_burst_per_token=200, list_per_minute_per_principal=1000, list_burst=200)

    def __init__(self, root, *, limits=None, **kw):
        from agentnode_sdk.remote_mcp.limits import Limits
        import dataclasses
        limits = dataclasses.replace(limits or Limits(), **self.GENEROUS)
        super().__init__(root, limits=limits, **kw)
        self.now = time.time()
        self.register = T.Register(self.stand, raw_pub(self.op), operator_private=self.op)
        self.register.init(managed_spec("w1", now=self.now), actor="t")
        self.acks = T.TrustAcks(self.stand, raw_pub(self.op), operator_private=self.op)
        self.list_number = 1
        self.workers: dict[str, TargetWorker] = {}
        self.authority_says: dict[str, str] = {}     # account -> refusal reason
        self.stopped = ""
        self.router = TargetRouter(register=self.register, acks=self.acks, worker_for=self._worker_for,
                                   deployment_list_number=lambda: self.list_number)
        self.ack("managed-w1")
        self.adapter = build_catalog_adapter(str(self.stand), self.worker, router=self.router,
                                             authority=lambda p: self.authority_says.get(p.principal_id),
                                             kill_switch=lambda: self.stopped)
        self.app.adapter = self.adapter

    def _worker_for(self, target):
        w = self.workers.get(target.target_id)
        if w is None:
            w = self.workers[target.target_id] = TargetWorker(target.identity_uri, target.worker["instance"])
        return w

    def w(self, target_id) -> TargetWorker:
        return self._worker_for(self.register.load().target(target_id))

    def ack(self, target_id, *, number=None, next_update=None):
        self.acks.record(target_id, {"revocation_list_number": self.list_number if number is None else number,
                                     "revocation_list_next_update": next_update or time.time() + 86400,
                                     "tombstones_published_at": time.time(),
                                     "tombstones_not_after": time.time() + 86400, "acked_at": time.time()})

    def customer(self, target_id, owner, instance="c1", *, port=8444):
        """invited -> ... -> active customer target owned by `owner`, attested and acknowledged."""
        self.register.add_customer(customer_spec(target_id, owner, instance, port=port), actor="t")
        for st in (T.ENROLLED, T.PROBING):
            self.register.set_status(target_id, st, actor="t")
        t = self.register.load().target(target_id)
        self.register.set_attestation(target_id, attestation(T.CUSTOMER, t.worker), actor="t")
        self.register.set_status(target_id, T.ACTIVE, actor="t")
        self.ack(target_id)
        return target_id

    def publish_starters(self, *, customer_ok=True):
        for imp in self.imports.values():
            self.catalog.publish(imp.manifest["capability_id"], "1.0.0", imp.manifest["tools"][0]["name"], actor="t")
        self.catalog.set_default([REGEX, REDACT, CITE], actor="t")
        if customer_ok:
            for tool in (REGEX, REDACT, CITE):
                self.catalog.set_assurance(tool, [T.OPERATOR_MANAGED, T.CUSTOMER_OBSERVED], actor="t")
