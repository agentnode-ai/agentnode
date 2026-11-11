# Smoke suite

The smoke suite decides "done" (PLAN-2026-11.md, section 2): a small set of end-to-end tests,
one per core user path, that together run in under ten minutes.

## Start

    python scripts/smoke.py

Interpreters: `SMOKE_BACKEND_PYTHON` and `SMOKE_SDK_PYTHON` if set, otherwise `backend/.venv` and
`sdk/.venv` of the checkout, otherwise the interpreter running the script. The backend part needs
the local Postgres the backend tests use (`DATABASE_URL`). No network, no real credentials, no
container runtime. Exit 0 only if every test passed, none was skipped and the run stayed within
`SMOKE_BUDGET_SECONDS` (default 600).

## Tests

| # | Core path | Test | What is real | What stands in |
|---|---|---|---|---|
| 1 | Register/login on the website -> API key | `backend/tests/smoke/test_smoke_backend.py::test_smoke_1_register_login_api_key` | FastAPI app, auth, API-key auth, database | Redis (in-memory, from conftest) |
| 2 | Connect the remote MCP (Claude Code) + call a tool | `sdk/tests/smoke/test_smoke_sdk.py::test_smoke_2_remote_mcp_connect_and_call` | Remote-MCP transport, OAuth (consent, code, PKCE, token), gateway login, catalog, approval, legacy `initialize` | Worker to the remote sandbox host (`CountingWorker`, runs the job stub locally) |
| 3 | Local MCP: install a catalog tool + run it | `sdk/tests/smoke/test_smoke_sdk.py::test_smoke_3_local_mcp_install_and_run` | `AgentNodeClient.install`, lockfile, `run_tool` -> real stdio MCP server process | Registry HTTP (answers with the backend's install-info fields) |
| 4 | Cloud sandbox run up to the quota | `sdk/tests/smoke/test_smoke_sdk.py::test_smoke_4_cloud_sandbox_runs_until_the_quota_then_refuses` | Gateway service + HTTP server, pairing, consent, signed answers, allowance ceiling | Container backend (`StandInBackend`, answers "RAN") |
| 5 | Own server: start the gateway + run | `sdk/tests/smoke/test_smoke_sdk.py::test_smoke_5_own_server_gateway_start_and_run` | as 4 | as 4 |
| 6 | Publish a package + verify | `backend/tests/smoke/test_smoke_backend.py::test_smoke_6_publish_verify_install` | Publish, verification pipeline, security scan, auto-clear for a new publisher, install-info, install | Object storage (in memory), search index and mail (no-op) |

Two guards check that each part tests the code of this checkout, not an installed copy.

## Limits

- No container sandbox runs: paths 2, 4 and 5 stop at the worker/backend boundary. The container
  lanes (`AGENTNODE_SANDBOX_E2E=1`, Linux) cover what happens behind it.
- Path 2 uses a pre-registered OAuth client; real clients may use CIMD or dynamic registration.
- Path 3 runs a curated MCP on the host (the shipped `curated_only` policy); non-curated MCPs need
  the container sandbox.
- Path 4: there is no one-time trial quota in the product yet; the test uses the operator-set
  rolling allowance (`runs_per_window`).
- Path 5 drives `GatewayService` + `make_server` directly; `agentnode gateway start` always uses
  the container backend.
