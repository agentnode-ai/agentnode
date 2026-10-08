"""What this worker owns, removed before anything else -- and what a namespace rebuild has to wait for.

WHY THIS MODULE EXISTS. `frozen/cleanup.json` of the eg12-repair arc, CU1 to CU8. The ordering it fixes
is the one F37 walked into on the real worker, and an independent review failed EG12 of the previous
arc's frozen profile on it:

  * `worker/service.py` proved its ceilings first, and when nothing could be started at all it asked
    the rootless runtime to rebuild its user namespace -- `podman system migrate`;
  * only AFTER that did it sweep what a previous worker had left, and that sweep was "Never fatal";
  * a migration replaces the user namespace a live container's init belongs to, so after it the account
    cannot address its own containers: `podman rm -f` answers "could not be stopped: sending SIGKILL to
    container ...: operation not permitted";
  * so the worker served with four egress proxies it could no longer remove, each holding a network;
    removing those networks left one file per network in the resolver's directory, `aardvark-dns` then
    refused to start at all, nothing on any custom network could resolve a name, and the egress proxy --
    fail-closed on a resolve failure -- answered 403 for a host on its own allowlist. The machine looked
    like it was enforcing a policy while it was unable to resolve. A human with root had to signal each
    container's init to get it back.

So the order is inverted and the verdict is binding: reconcile first, read every removal back, and let
neither a migration nor a readiness proceed on anything but an empty answer that could be read.

Nothing here reaches for the runtime by hand. The worker's own
`remove_what_a_previous_worker_left()` is the one entry point -- it covers this SDK's job containers and
delegates the egress resources to `sandbox/egress.py`, which owns that label vocabulary -- and this
module is the decision around it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class LeftoversRemain(RuntimeError):
    """This worker owns something it could not remove, so it neither migrates nor serves.

    A separate exception rather than a message, for the same reason `CannotHoldItsLimits` is one: an
    operator has to do something different about it, and the thing that starts a worker has to be able
    to tell the two apart. It is NEVER shaped like a policy decision -- CU5 -- because in F37 the
    symptom reached an operator as a 403 from the egress proxy and was indistinguishable from an
    allowlist refusal.
    """

    def __init__(self, reason: str, report: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.report = dict(report or {})

    @property
    def resources(self) -> list:
        """Every resource this refusal is about, as `kind name state why` rows."""
        out = []
        for where in ("before", "after"):
            part = self.report.get(where) or {}
            out += _resources_of(part)
        if not out:
            out += _resources_of(self.report)
        return out


@dataclass(frozen=True)
class Reconciled:
    """The answer, and the whole answer: clean is not "the command did not fail"."""

    clean: bool
    report: dict = field(default_factory=dict)
    why: str = ""
    rebuilt: bool | None = None


def _resources_of(report: dict) -> list:
    out = []
    for failed in report.get("failed") or []:
        if isinstance(failed, dict):
            out.append({"kind": failed.get("kind", "container"), "name": failed.get("name", ""),
                        "state": failed.get("state", ""), "why": failed.get("why", "")})
        else:
            out.append({"kind": "container", "name": str(failed), "state": "", "why": ""})
    for failed in (report.get("egress") or {}).get("failed") or []:
        out.append({"kind": failed.get("kind", ""), "name": failed.get("name", ""),
                    "state": failed.get("state", ""), "why": failed.get("why", "")})
    return out


def _why_not(report: dict) -> str:
    """One sentence per thing that is wrong, naming the resource and the runtime's own words."""
    bits = []
    for row in _resources_of(report):
        said = (": " + row["why"]) if row["why"] else ""
        bits.append("%s %s is still %s%s" % (row["kind"], row["name"], row["state"] or "there", said))
    for unreadable in report.get("unreadable") or []:
        bits.append("a listing could not be read: %s" % unreadable)
    for unreadable in (report.get("egress") or {}).get("unreadable") or []:
        bits.append("an egress listing could not be read: %s" % unreadable)
    if report.get("why"):
        bits.append(str(report["why"]))
    if not (report.get("egress") or {}).get("asked", True):
        bits.append("the egress reconciliation could not be asked: %s"
                    % ((report.get("egress") or {}).get("reason") or "no reason given"))
    return "; ".join(bits) or "the reconciliation did not come back clean"


def _a_worker(worker=None):
    if worker is not None:
        return worker
    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    from agentnode_sdk.worker.local import LocalWorker

    return LocalWorker(ContainerBackend())


def reconcile(worker=None) -> Reconciled:
    """Remove everything of this worker's that no run is waiting for, and say whether that worked.

    Idempotent, which is CU12: against an already clean host it removes nothing and says clean; against
    the same dirty host twice it reaches the same verdict.
    """
    said = _a_worker(worker).remove_what_a_previous_worker_left()
    clean = bool(said.get("clean"))
    return Reconciled(clean=clean, report=said, why="" if clean else _why_not(said))


def may_the_namespace_be_rebuilt(worker=None) -> Reconciled:
    """Whether a rebuild is allowed, WITHOUT removing anything: the inventory alone decides.

    CU1 asked as a question rather than as a side effect, so a caller can check it and so a test can.
    """
    from agentnode_sdk.sandbox.egress import nothing_of_ours_is_left

    w = _a_worker(worker)
    runtime = ""
    try:
        runtime = str(w.backend.check_available().backend or "")
    except Exception:                                             # noqa: BLE001
        runtime = ""
    if not runtime or runtime == "none":
        return Reconciled(clean=False, report={}, why="there is no container runtime here to ask")
    clean, inventory = nothing_of_ours_is_left(runtime)
    # And this SDK's own job containers, which the egress inventory does not cover.
    mine, unreadable = _mine(w, runtime)
    if mine or unreadable:
        clean = False
    report = {"egress": inventory, "mine": mine, "unreadable": unreadable, "runtime": runtime}
    return Reconciled(clean=bool(clean), report=report,
                      why="" if clean else _why_not({"failed": [{"name": m} for m in mine],
                                                     "unreadable": unreadable,
                                                     "egress": inventory}))


def _mine(worker, runtime: str) -> tuple:
    """This SDK's own job containers, by prefix, strictly parsed."""
    import subprocess

    from agentnode_sdk.sandbox.egress import _rows_of

    try:
        listed = subprocess.run([runtime, "ps", "-a", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=60)
    except Exception as exc:                                      # noqa: BLE001
        return [], ["the runtime would not list its containers: %s" % str(exc)[:160]]
    if listed.returncode != 0:
        return [], ["the runtime would not list its containers: "
                    + (listed.stderr or "").strip()[:160]]
    rows, unreadable = _rows_of(listed.stdout or "")
    prefixes = tuple(getattr(worker, "ITS_OWN_PREFIXES", ()) or ())
    mine = [r.split()[0] for r in rows if r.split() and r.split()[0].startswith(prefixes)]
    return mine, unreadable


def reconcile_then_rebuild(worker=None, *, rebuild=None) -> Reconciled:
    """Clean first, rebuild only on an empty answer, then take the inventory again.

    CU1, CU2, CU6 and CU8 in one function, because they are one decision: a migration that happens
    while anything of ours exists is the defect, and a migration that happened is not a reason to stop
    asking.
    """
    first = reconcile(worker)
    if not first.clean:
        return Reconciled(clean=False, report={"before": first.report}, why=first.why, rebuilt=False)
    w = _a_worker(worker)
    runtime = str(first.report.get("runtime") or "")
    if rebuild is None:
        from agentnode_sdk.sandbox.container_backend import (
            recover_a_runtime_that_lost_its_namespace as rebuild,
        )
    rebuilt = bool(rebuild(runtime or "podman"))
    second = reconcile(w)
    why = ""
    if not rebuilt:
        why = "the runtime would not rebuild its namespace"
    elif not second.clean:
        why = "after the rebuild, " + second.why
    return Reconciled(clean=bool(rebuilt and second.clean),
                      report={"before": first.report, "after": second.report, "rebuilt": rebuilt},
                      why=why, rebuilt=rebuilt)
