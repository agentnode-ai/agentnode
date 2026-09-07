"""Which boot of this machine a measurement was taken during.

A conformance report says what the runtime did when it was measured. Almost everything that could
invalidate it between then and now is caught by binding the report to the gateway version, the
backend and the image digest -- but not a reboot. A kernel upgrade applied on restart, a cgroup
controller that is no longer mounted, a seccomp profile that changed, an apparmor policy that
loaded differently: none of those move the image digest, and all of them can change what the
container actually gets. The report would still describe the previous boot and would still look
current.

So the boot is part of the binding, and the only interesting question is how to name it.

* **Linux** has `/proc/sys/kernel/random/boot_id`: a UUID the kernel generates at boot. Exact,
  free, and unambiguous. It is what the deployment target uses.
* **Elsewhere** there is no equivalent that can be read without shelling out, so the boot time is
  *derived*: `time.time() - time.monotonic()` is approximately the moment the clock started
  counting, and on both Linux and Windows the monotonic clock counts from boot. It is rounded to
  a minute so that ordinary jitter does not make it look like a new boot.

The derived form is weaker and is labelled as such rather than being quietly presented as the same
thing. A wall-clock adjustment -- an NTP step, a manual change, a daylight-saving move on a machine
that keeps local time in the RTC -- shifts it and will read as a reboot that did not happen. That
direction is the safe one: it forces a re-measurement rather than accepting a stale report. The
method travels with the value so that a reader can tell which of the two they are looking at.
"""
from __future__ import annotations

import time
from pathlib import Path

#: How coarsely the derived boot time is rounded, in seconds. A minute is far longer than the
#: jitter between two readings and far shorter than any real uptime worth confusing with it.
DERIVED_ROUNDING_SECONDS = 60

LINUX_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


def boot_identity() -> tuple[str, str]:
    """`(value, method)` for this boot. Never raises; the weak method is named, not hidden."""
    try:
        if LINUX_BOOT_ID.is_file():
            value = LINUX_BOOT_ID.read_text(encoding="utf-8").strip()
            if value:
                return value, "kernel-boot-id"
    except OSError:
        pass

    approximate = time.time() - time.monotonic()
    rounded = int(approximate // DERIVED_ROUNDING_SECONDS) * DERIVED_ROUNDING_SECONDS
    return str(rounded), "derived-boot-time"


def describe(method: str) -> str:
    """What a person should understand about the value they are looking at."""
    if method == "kernel-boot-id":
        return "the kernel's own boot identifier"
    return (
        "an estimate of when this machine started, since it offers no boot identifier. A clock "
        "change can make this look like a restart, which forces a fresh measurement rather than "
        "trusting an old one"
    )
