"""Which modules belong to which host, and a way to hold that to be true.

There is ONE WHEEL and TWO ROLES. A worker host installs the same distribution the control plane
does, and the control plane's modules sit on its disk with nothing starting them. That is not the
end state -- two artefacts would make the boundary a packaging fact -- but the property worth
having first is the one underneath it: **the worker's start path must not need the control
plane's code at all.** While that is true, splitting the wheel is packaging work. The moment it
stops being true, splitting the wheel becomes a rewrite, and nobody finds out until they try.

So the manifest is here and `tests/test_the_two_roles_are_separable.py` walks the import graph
from each role's entry points and fails on a crossing. Statically, over the source, including
imports written inside functions -- which is where the last one was: `worker/service.py` reached
`gateway.boot` from inside `_own_boot_id`, so nothing at import time would have caught it and the
worker needed the control plane's package to learn its own kernel's boot id. `machine.py` exists
because of that.

## What each role is

`CONTROL_PLANE` holds the CA, this deployment's signing identity, every client's token material,
the accounts, the queue, the allowances and the ledger. It drives no container runtime.

`WORKER` runs foreign code and holds: its own certificate and key, the public trust anchor, the
two signed lists, the key for one pair, its journal and its lease counter. It holds nothing that
belongs to a customer and nothing it could sign with.

`SHARED` is what both need and neither owns: the wire protocol, the identity checks, the trust
view, facts about the machine, and the CLI's own plumbing.

## What this is not

It is not a permission system and it enforces nothing at runtime. A process that imports across
the line still works. What it does is make a crossing **visible in a test** at the moment it is
written, rather than at the moment somebody tries to install one role without the other.
"""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = "agentnode_sdk"
HERE = Path(__file__).resolve().parent

#: What a worker host STARTS. Everything reachable from here must be in the worker's half.
WORKER_ENTRY = (
    "agentnode_sdk.worker.service",
    "agentnode_sdk.worker.tls",
    "agentnode_sdk.worker.journal",
    "agentnode_sdk.worker.lease",
    "agentnode_sdk.worker.pairkeys",
    "agentnode_sdk.worker.topology",
)

#: Prefixes a worker host must never reach from those entry points. `gateway` is the control
#: plane; `registry` and `publish` are the public-facing SDK and have no business on a machine
#: that runs other people's code.
NOT_ON_THE_WORKER = (
    "agentnode_sdk.gateway",
    "agentnode_sdk.registry",
    "agentnode_sdk.publish",
)

#: And the other direction. The control plane may speak to a worker -- `worker.remote` is its
#: client -- but it must not reach the part that RUNS things, because on a two-host deployment
#: there is nothing on that machine for it to run them with.
CONTROL_PLANE_ENTRY = (
    "agentnode_sdk.gateway.server",
)
NOT_ON_THE_CONTROL_PLANE = (
    "agentnode_sdk.worker.service",
    "agentnode_sdk.worker.local",
    "agentnode_sdk.sandbox.container_backend",
)


#: HOW A WORKER HOST ACTUALLY STARTS: through the command line, not by importing the service.
#: Naming it here rather than leaving it out is the difference between a manifest that describes
#: the deployment and one that flatters it -- `worker_commands` does reach the control plane's
#: package, and a reader is entitled to know that before believing anything about two wheels.
THE_WORKERS_COMMAND_LINE = ("agentnode_sdk.cli.worker_commands",)

_ONE_CLI = ("there is ONE command line for both roles. `agentnode worker serve` imports "
            "`gateway.runtime_pin` for the interpreter-pin rule -- deliberately shared, because "
            "two copies of that rule had already started to disagree -- and importing anything "
            "from the `gateway` package runs its `__init__`, which pulls in the rest of this "
            "list. Splitting the command line is packaging work and has not been done.")

#: What the worker's command line reaches, exactly, with the one reason behind all of it.
KNOWN_CLI_CROSSINGS = {name: _ONE_CLI for name in (
    "agentnode_sdk.gateway",
    "agentnode_sdk.gateway.accounts",
    "agentnode_sdk.gateway.filelock",
    "agentnode_sdk.gateway.identity",
    "agentnode_sdk.gateway.policy_paths",
    "agentnode_sdk.gateway.protocol",
    "agentnode_sdk.gateway.runtime_pin",
    "agentnode_sdk.gateway.securedir",
    "agentnode_sdk.gateway.statedir",
    "agentnode_sdk.gateway.throttle",
)}


#: The crossings that are still there, each with the reason it is. This is a RATCHET, not a
#: waiver: `tests/test_the_two_roles_are_separable.py` asserts the crossings are EXACTLY this set,
#: so a new one fails the suite and a fixed one has to be taken out of this list before the suite
#: goes green again. Nothing is silently permitted and nothing silently stays.
KNOWN_CROSSINGS = {
    # `gateway/protocol.py` is the product's whole wire vocabulary -- job requests, termination
    # reasons, outcomes, signatures -- and is imported by the CLI, the dispatcher, conformance
    # and both roles. It is under `gateway/` for historical reasons and is not the gateway's.
    # Moving it is a rename across a large part of the package and is not this arc's work; what
    # is recorded here is that the worker reaches it for the stop-reason vocabulary alone.
    "agentnode_sdk.gateway.protocol":
        "the shared wire vocabulary, misfiled under gateway/. The worker reads two termination "
        "reasons from it (TIMED_OUT, OUT_OF_MEMORY) through conformance.runner.",
    "agentnode_sdk.gateway.policy_paths":
        "reached only through gateway.protocol, above. It has no import of its own.",
    # The other direction. One build serves both topologies, and under
    # `single-host-development` the control plane starts a worker in its own process. On a
    # separate worker host nothing calls these, and the modules simply sit there.
    "agentnode_sdk.worker.service":
        "single-host-development starts an in-process worker, and one build serves both "
        "topologies. Unused on a control plane whose worker is elsewhere.",
    "agentnode_sdk.worker.local":
        "as above: the in-process worker's implementation.",
    "agentnode_sdk.sandbox.container_backend":
        "as above. On a separate control-plane host there is no container runtime for it to "
        "drive, and install-control-plane.sh refuses to run where one is installed.",
}


def _path_of(module: str) -> Path | None:
    """Where a first-party module's source is, without importing anything."""
    if not module.startswith(PACKAGE):
        return None
    tail = module[len(PACKAGE):].lstrip(".")
    base = HERE / Path(*tail.split(".")) if tail else HERE
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imports_of(module: str) -> set:
    """Every first-party module this one imports, including inside functions.

    Function-level imports count. They are how a crossing gets written without anybody noticing:
    nothing fails at import time, the tests pass, and the dependency is real.
    """
    path = _path_of(module)
    if path is None:
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith(PACKAGE))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[:len(parts) - node.level + 1])
                where = base + ("." + node.module if node.module else "")
            else:
                where = node.module or ""
            if not where.startswith(PACKAGE):
                continue
            found.add(where)
            # `from x import y` where y is itself a module, which is how most of this package
            # imports its siblings.
            found.update(where + "." + a.name for a in node.names
                         if _path_of(where + "." + a.name) is not None)
    return {m for m in found if _path_of(m) is not None}


def reached_from(entries) -> dict:
    """Every first-party module reachable from these, and the shortest path to each."""
    seen: dict = {}
    frontier = []
    for entry in entries:
        seen[entry] = (entry,)
        frontier.append(entry)
    while frontier:
        module = frontier.pop(0)
        for target in sorted(_imports_of(module)):
            if target in seen:
                continue
            seen[target] = seen[module] + (target,)
            frontier.append(target)
    return seen


def crossings(entries, forbidden) -> dict:
    """The forbidden modules these entry points reach, each with the path that got there.

    A path rather than a name, because "the worker imports the gateway" is not actionable and
    "worker.service -> worker.local -> gateway.something" is.
    """
    out = {}
    for module, how in reached_from(entries).items():
        if any(module == f or module.startswith(f + ".") for f in forbidden):
            out[module] = how
    return out


__all__ = ["CONTROL_PLANE_ENTRY", "KNOWN_CLI_CROSSINGS", "KNOWN_CROSSINGS",
           "NOT_ON_THE_CONTROL_PLANE", "NOT_ON_THE_WORKER", "PACKAGE",
           "THE_WORKERS_COMMAND_LINE", "WORKER_ENTRY", "crossings", "reached_from"]
