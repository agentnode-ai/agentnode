# Changelog

## 0.4.1 — Security & Correctness

**Behavioral change:** `run_tool(mode="auto")` now always executes via
subprocess isolation, regardless of trust level. This makes the
documented isolation guarantee true by default. `mode="direct"` remains
available as an explicit opt-in for performance-critical workloads that
knowingly share in-process globals.

**Migration note:** Tools that rely on shared in-process state
(module-level globals, process-wide singletons) should explicitly pass
`mode="direct"` going forward.

### Fixes

- **AsyncAgentNode /v1 base URL** — the async client now appends `/v1` to
  `base_url` when missing, matching `AgentNode` (sync). Previously all
  `AsyncAgentNode` calls hit `/packages/...` and 404ed against
  production. (P0-04)
- **AgentNodeClient.install()** now POSTs
  `POST /v1/packages/{slug}/install` so the backend tracks the install
  event. Previously installs went untracked. (P0-05)
- **run_tool(mode="auto") always uses subprocess** — see behavioral
  change above. (P0-06)
- **Response parsing hardening** — `_handle`/`_request` no longer crash
  on non-dict JSON error bodies or HTML/plain-text 2xx responses; both
  are now surfaced as `AgentNodeError`. (P1-SDK3, P1-SDK4)
- **run_tool reserved kwargs** — passing the internal `entry` kwarg via
  `**kwargs` now raises `TypeError` instead of silently shadowing the
  dispatcher's forwarding path. (P1-SDK5)
- **Installer download ceiling** — `download_artifact` now enforces a
  500 MB hard ceiling (`MAX_DOWNLOAD_BYTES`). Declared
  `Content-Length` is checked up front; streamed bytes are checked per
  chunk. Oversized downloads are aborted and the partial file removed.
  (P1-SDK6)
- **run_tool dispatch logging** — `runner.run_tool` now emits an `INFO`
  log line with the resolved runtime and mode, so callers can confirm
  what mode `auto` actually picked without inspecting the
  `RunToolResult` after the fact. (P1-SDK10)
