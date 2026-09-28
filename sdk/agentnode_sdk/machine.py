"""Facts about the machine this process is on, belonging to neither role.

This module is `gateway/boot.py`, moved. It was never about the gateway: it answers "which boot
of THIS machine is this", and the worker has to answer it about its own machine -- the binding
carries `worker_boot_id` and `gateway_boot_id` separately for exactly that reason. While it lived
under `gateway/`, the worker's own start path imported the control plane's package to learn its
own kernel's boot id, which is the sort of thing that makes "the worker does not need the control
plane" an intention rather than a fact. `roles.py` and
`tests/test_the_two_roles_are_separable.py` now hold that to be true, and this move is what made
it possible to.

`gateway/boot.py` still exists and re-exports these two names, because other code and other
people's imports point at it.

## Which boot, and how it is named

A conformance report says what the runtime did when it was measured. Almost everything that could
invalidate it between then and now is caught by binding the report to the version, the backend
and the image digest -- but not a reboot. A kernel upgrade applied on restart, a cgroup controller
that is no longer mounted, a seccomp profile that changed, an apparmor policy that loaded
differently: none of those move the image digest, and all of them can change what the container
actually gets. The report would still describe the previous boot and would still look current.

* **Linux** has `/proc/sys/kernel/random/boot_id`: a UUID the kernel generates at boot. Exact,
  free, and unambiguous. It is what the deployment target uses.
* **Elsewhere** there is no equivalent that can be read without shelling out, and the first
  attempt here derived one from `time.time() - time.monotonic()`, rounded to a minute.
  `EM3C-EXTERNAL-0001` rejected that, correctly: rounding lets two different boots share an
  identity, and a value derived from the wall clock can be reproduced deliberately by setting the
  clock back, so a stale report could be made to look current by the one party who would want to.

  What replaces it is not a better estimate. It is a value generated fresh in this process and
  never written down. Every restart therefore looks like a new boot, which on a machine with no
  boot identifier is exactly the truth: this build cannot tell whether the machine rebooted, so it
  assumes it did and re-measures. That costs a measurement per restart on those platforms and
  cannot be forged, which is the trade worth making -- both roles are deployed on Linux, where
  the kernel answers the question properly.

The method travels with the value so a reader can tell which of the two they have.
"""
from __future__ import annotations

import uuid
from pathlib import Path

LINUX_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")

#: Generated once, here, and never persisted. On a platform with no boot identifier this is what
#: stands in for one, so a restart is indistinguishable from a reboot -- which is the honest answer
#: when the machine cannot be asked.
_THIS_PROCESS = uuid.uuid4().hex


def boot_identity() -> tuple[str, str]:
    """`(value, method)` for this boot. Never raises; the weak method is named, not hidden."""
    try:
        if LINUX_BOOT_ID.is_file():
            value = LINUX_BOOT_ID.read_text(encoding="utf-8").strip()
            if value:
                return value, "kernel-boot-id"
    except OSError:
        pass

    return _THIS_PROCESS, "process-lifetime"


def describe(method: str) -> str:
    """What a person should understand about the value they are looking at."""
    if method == "kernel-boot-id":
        return "the kernel's own boot identifier"
    return (
        "this gateway process, because the machine offers no boot identifier. It cannot tell a "
        "restart from a reboot, so it treats every restart as one and measures again"
    )


__all__ = ["LINUX_BOOT_ID", "boot_identity", "describe"]
