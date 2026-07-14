"""Fresh-process startup recovery for the G2 Docker fault-test.

Runs the exact ``startup_smoke_recovery()`` the FastAPI lifespan runs (reaper +
smoke_running recovery + recovery-gate flip), then prints a single JSON line with
the summary, the resulting recovery status, and smoke_availability(). Kept as a
separate process so it mirrors a genuine restart (fresh module state, recovery
status starts not_started). MCP_SMOKE_MODE stays disabled so no new smoke starts.

Not a pytest module (leading underscore); invoked as a script.
"""

import asyncio
import json
import os

os.environ.setdefault("MCP_SMOKE_MODE", "disabled")

import app.main  # noqa: E402,F401 — register ALL ORM models (FK mapper config)
from app.mcp import smoke_executor as ex  # noqa: E402


async def _main() -> None:
    summary = await ex.startup_smoke_recovery()
    print(
        "G2_RECOVERY_JSON="
        + json.dumps(
            {
                "summary": summary,
                "recovery_status": ex.get_recovery_status(),
                "smoke_availability": list(ex.smoke_availability()),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(_main())
