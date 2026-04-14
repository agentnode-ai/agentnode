"""Structured run log for agent executions.

Append-only JSONL logs per run stored in ~/.agentnode/runs/{run_id}.jsonl.
No secrets, no tool inputs/outputs — only metadata.

Events: run_start, tool_call, tool_result, iteration, step_start,
        step_result, run_end, truncated
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentnode.run_log")

MAX_ENTRIES_PER_RUN = 1000


def _runs_dir() -> Path:
    """Return ~/.agentnode/runs/, creating it if needed."""
    from agentnode_sdk.config import config_dir
    d = config_dir() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_path(run_id: str) -> Path:
    return _runs_dir() / f"{run_id}.jsonl"


class RunLog:
    """Append-only JSONL writer for a single agent run."""

    __slots__ = ("_run_id", "_path", "_count", "_truncated")

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._path = _run_path(run_id)
        self._count = 0
        self._truncated = False

    @property
    def run_id(self) -> str:
        return self._run_id

    def _write(self, event: str, **fields: Any) -> None:
        """Write a single event line. Silently drops on error."""
        if self._truncated:
            return

        if self._count >= MAX_ENTRIES_PER_RUN:
            self._truncated = True
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "run_id": self._run_id,
                "event": "truncated",
                "message": f"Max entries ({MAX_ENTRIES_PER_RUN}) reached",
            }
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception:
                logger.debug("Failed to write truncated event", exc_info=True)
            return

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self._run_id,
            "event": event,
            **fields,
        }
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._count += 1
        except Exception:
            logger.debug("Failed to write run log event: %s", event, exc_info=True)

    # --- Public event writers ---

    def run_start(self, slug: str, goal: str, **extra: Any) -> None:
        self._write("run_start", slug=slug, goal=goal[:200], **extra)

    def tool_call(
        self, slug: str, tool_name: str | None = None, **extra: Any,
    ) -> None:
        self._write("tool_call", slug=slug, tool_name=tool_name, **extra)

    def tool_result(
        self,
        slug: str,
        tool_name: str | None = None,
        *,
        success: bool,
        duration_ms: float = 0.0,
        error: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "slug": slug,
            "tool_name": tool_name,
            "success": success,
            "duration_ms": round(duration_ms, 1),
        }
        if error:
            fields["error"] = error[:500]
        self._write("tool_result", **fields)

    def iteration(self, iteration_num: int) -> None:
        self._write("iteration", iteration=iteration_num)

    def step_start(self, step_name: str, tool: str) -> None:
        self._write("step_start", step_name=step_name, tool=tool)

    def step_result(
        self,
        step_name: str,
        *,
        success: bool,
        duration_ms: float = 0.0,
        skipped: bool = False,
    ) -> None:
        self._write(
            "step_result",
            step_name=step_name,
            success=success,
            duration_ms=round(duration_ms, 1),
            skipped=skipped,
        )

    def run_end(self, *, success: bool, duration_ms: float = 0.0, error: str | None = None) -> None:
        fields: dict[str, Any] = {
            "success": success,
            "duration_ms": round(duration_ms, 1),
        }
        if error:
            fields["error"] = error[:500]
        self._write("run_end", **fields)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def read_run(run_id: str) -> list[dict]:
    """Read all events from a run log. Returns empty list if not found."""
    path = _run_path(run_id)
    if not path.exists():
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except Exception:
        logger.debug("Failed to read run log: %s", run_id, exc_info=True)
    return events


def list_runs(limit: int = 20) -> list[str]:
    """List run IDs sorted by modification time (newest first)."""
    try:
        d = _runs_dir()
    except Exception:
        return []

    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [f.stem for f in files[:limit]]


# ---------------------------------------------------------------------------
# Retention / cleanup
# ---------------------------------------------------------------------------

def _load_retention_config() -> tuple[int, int]:
    """Load retention config from ~/.agentnode/config.json.

    Returns (max_age_days, max_count). Reads the raw JSON to access
    the 'run_log' section which is not part of the SDK config defaults.
    """
    import json as _json
    from agentnode_sdk.config import config_path

    try:
        raw = _json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:
        return 30, 500

    run_log_cfg = raw.get("run_log", {})
    if not isinstance(run_log_cfg, dict):
        run_log_cfg = {}
    max_age_days = run_log_cfg.get("max_age_days", 30)
    max_count = run_log_cfg.get("max_count", 500)
    return int(max_age_days), int(max_count)


def cleanup_old_runs(
    max_age_days: int | None = None,
    max_count: int | None = None,
) -> int:
    """Delete old run logs based on age and count limits.

    Returns the number of files deleted.
    """
    cfg_age, cfg_count = _load_retention_config()
    effective_age = max_age_days if max_age_days is not None else cfg_age
    effective_count = max_count if max_count is not None else cfg_count

    try:
        d = _runs_dir()
    except Exception:
        return 0

    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not files:
        return 0

    deleted = 0
    now = time.time()
    cutoff = now - (effective_age * 86400)

    # Keep at most effective_count files; among those, delete files older than cutoff
    for i, f in enumerate(files):
        should_delete = False

        # Over count limit — delete (oldest first, so index >= effective_count)
        if i >= effective_count:
            should_delete = True
        # Over age limit
        elif f.stat().st_mtime < cutoff:
            should_delete = True

        if should_delete:
            try:
                f.unlink()
                deleted += 1
            except Exception:
                logger.debug("Failed to delete run log: %s", f, exc_info=True)

    if deleted:
        logger.info("run_log_cleanup: deleted %d old run logs", deleted)

    return deleted
