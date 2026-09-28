"""Which boot of this machine a measurement was taken during -- now `agentnode_sdk.machine`.

This module was never about the gateway. It answers "which boot of THIS machine is this", and
the worker has to answer it about its own machine: the binding carries `worker_boot_id` and
`gateway_boot_id` separately for exactly that reason. While the answer lived here, the worker's
start path imported the control plane's package to learn its own kernel's boot id, which is how
"the worker does not need the control plane" stays an intention. It moved to `machine.py`, and
`roles.py` with `tests/test_the_two_roles_are_separable.py` now hold the separation to be true.

The names stay here because other code and other people's imports point at them. The reasoning
about the two methods -- the kernel's boot id, and a per-process value where there is none --
went with the implementation and is in `machine.py`.
"""
from __future__ import annotations

from agentnode_sdk.machine import LINUX_BOOT_ID, boot_identity, describe

__all__ = ["LINUX_BOOT_ID", "boot_identity", "describe"]
