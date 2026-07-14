"""Child process for the G2 Docker fault-test (test_g2_fault_docker.py).

Runs the REAL ``run_and_store_smoke`` path for one submission: it sets the
``smoke_running`` marker in the DB and starts the sandbox install container. The
parent SIGKILLs this process mid-install to simulate a crash where the Python
``finally`` (volume cleanup) never runs — leaving a real orphaned volume + marker.

Not a pytest module (leading underscore); invoked as a script with the submission
id as argv[1]. MCP_SMOKE_MODE=container is set process-locally BEFORE any app
import so pydantic settings pick it up; the recovery gate is forced ready so the
smoke actually runs.
"""

import asyncio
import os
import sys

os.environ["MCP_SMOKE_MODE"] = "container"


def _log(*a):
    print("child:", *a, file=sys.stderr, flush=True)


_log("start; importing app.main")
import app.main  # noqa: E402,F401 — register ALL ORM models (FK mapper config)
from app.mcp import smoke_executor as ex  # noqa: E402

ex._set_recovery_status("ready")
_log(
    "MCP_SMOKE_MODE=",
    ex.settings.MCP_SMOKE_MODE,
    "CONTAINER_RUNTIME=",
    ex.CONTAINER_RUNTIME,
    "availability=",
    ex.smoke_availability(),
)


async def _main(submission_id: str) -> None:
    result = await ex.run_and_store_smoke(submission_id)
    _log("run_and_store_smoke returned", result)


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1]))
    _log("done")
