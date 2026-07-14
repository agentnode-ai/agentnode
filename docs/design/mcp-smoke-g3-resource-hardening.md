# MCP sandbox-smoke — G3 host-resource & backlog hardening

Prerequisites closed before any **permanent** production smoke activation. Built as
a small backend slice; **no migration, no new worker/queue, no volume-storage
change**. `MCP_SMOKE_MODE` stays `disabled` by default — none of this activates a
smoke.

## Thresholds (conservative defaults, env-overridable)

| Setting | Default | Meaning |
| --- | --- | --- |
| `MCP_SMOKE_MIN_AVAILABLE_MEMORY_MB` | `1024` | Min `/proc/meminfo` MemAvailable to start a smoke (512 MB container + install overhead, no-swap host). |
| `MCP_SMOKE_MIN_FREE_DISK_GB` | `5` | Min free space on the Docker filesystem (`/var/lib/docker`, which is on `/`). |
| `MCP_SMOKE_MAX_PENDING` | `1` | Waiting tasks allowed; max in-flight = 1 active + pending. |
| `MCP_SMOKE_MAX_OUTPUT_BYTES` | `1 MiB` | Hard cap on runtime-container stdout buffered in the API process. |
| `MCP_SMOKE_DOCKER_ROOT` | `/var/lib/docker` | statvfs target for the disk preflight. |

**Load is observability only** — `os.getloadavg()[0]` is measured and logged in the
resource evidence, but it never blocks (spurious-skip risk on a 2-core host; RAM/disk
are the real guards).

## Resource preflight

Measured with stdlib only (`/proc/meminfo`, `os.statvfs`, `os.getloadavg`) — no
psutil, no shell pipes, no `docker info` per smoke. The check runs in
`run_and_store_smoke` **after** the G1 freshness recheck and **before** `smoke_running`
is set or any container starts. A shortfall — or any measurement error — is
**fail-closed** (no container) and produces a `status=unavailable`,
`failure_reason=resource_unavailable` SmokeResult → transient/review, never a hard
package fault, no auto-retry. Admin reverify (or a later submit) retries.

**Overwrite safety:** a `resource_unavailable` ("could not test right now") result
never downgrades authoritative evidence — it does not replace a fresh key-matching
PASS (G1 already guards this) nor an objective HARD failure. It does replace absent /
expired / key-mismatch / transient / unavailable / skipped evidence.

## Backlog bounding

The semaphore caps **active** docker runs at 1; a process-local in-flight counter
caps **waiting** tasks (`active=1 + MCP_SMOKE_MAX_PENDING`). `maybe_schedule_smoke`
claims a slot (non-blocking, no-await → atomic in the single-threaded event loop) and
schedules a `_run_scheduled_smoke` wrapper that always releases the slot (success,
G1-skip, resource_unavailable, or error); a failed `add_task` frees it immediately.
Excess → **busy**: no task, no `smoke_running`, no docker, gate stays `not_run/future`;
admin can reverify later. `run_and_store_smoke` called **directly** (canary/tests)
does not touch the counter. A restart resets it to 0. Combined with the existing
`submit` rate-limit (5/60s) + admin-only `reverify` (10/60s), no separate MCP smoke
rate-limit is needed.

## Output bounding

- **Runtime stdout** is read **byte-chunked** (not `readline`) into a bounded queue,
  capping total bytes at `MCP_SMOKE_MAX_OUTPUT_BYTES` — so neither a chatty server
  nor a **single giant line without a newline** can grow the API process's memory.
  Exceeding the cap → `excessive_output` (transient/review); the process is
  terminated and reaped in the existing `finally`; the reader thread is joined (no
  leak). stderr goes straight to `DEVNULL` (no pipe → no drain thread, no deadlock).
- **Install output** is not captured: the install + volume-rm phases run with
  `capture=False` (stdout/stderr → `DEVNULL`), since only the return code is used.
  The reaper/list/inspect calls keep `capture=True`.

## Volume quota — deferred (infrastructure follow-up)

Production runs **overlay2 on ext4**; the `local` volume driver's `size` option is
not enforced on ext4 (needs xfs project-quota or a storage change). A bind-mount
(host-mount risk), a separate loopback fs, or a tmpfs rebuild are all out of scope. So
there is **no hard per-smoke volume quota**; the MVP protection is the disk preflight
(5 GB), one active + one pending smoke, the 120 s install timeout, and the G2 reaper.
A hard volume quota remains a later infrastructure topic.

## Persisted passed-smoke while `disabled` — unchanged (by design)

A fresh, key-matching persisted PASS stays valid evidence even when
`MCP_SMOKE_MODE=disabled`. `disabled` means "run no **new** smokes" — it does not
invalidate existing evidence. The invalidation mechanisms are the freshness binding
(TTL, `command_hash`, `image_digest`, package/version) and **`MCP_SMOKE_SCHEMA_VERSION`**
— the emergency lever: bump it on a security-relevant executor change to invalidate
all older smokes (key_mismatch → future/recheck). `auto_publish_eligible` stays
advisory; `publish_submission` stays admin-only; **no auto-publish** under any of this.
