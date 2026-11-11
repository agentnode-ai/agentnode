# Test strategy, slice 3: what the obsolete SDK lanes and the old CLI tests cover, and the exact change list

Prepared for #31, without removing anything (#34). Measured at `3fb6da18` on `work/fundament-2026-11`.

> **Status in this branch (draft PR #194 integration candidate):** step A.1 and A.3 are done. The
> `container-present` job is in `.github/workflows/full-checks.yml`, held by `sdk/tests/test_ci_full_lanes.py`;
> run 42841967896 (byte-identical job files) passed all 13 tests. It runs on nightly and release, not
> on pull requests. G1 below is closed by #31 step 1 and #41. Step A.4 (re-running
> `gated_e2e_coverage.py` for a zero result) and the deletion steps are still open. The measurement
> below is kept as it was at `3fb6da18`.
The plan (`PLAN-2026-11.md`, section 2) says: drop the `sdk-lanes` (they test a frozen state) and the
tests of the deprecated CLI. This file says what that would lose, where each piece is covered at the
current head, and what has to be added BEFORE anything is deleted.

## 1. `sdk-lanes.yml` — what it is

Runs on every push and PR to `main`, but checks out `FROZEN_REV: 2de83e798f3cb71f5e91dae2148dd88004cd3d09`
(2026-09-07, "EM-3A split: the frozen inventory, collected from b5b0246"), **814 commits behind
`3fb6da18`**. It therefore reports on that commit, never on the code of the PR it runs for — which is why
the plan calls it obsolete, and why `sdk/HANDOFF.md` (item 4) and `managed-access.yml` (line 13) already
say it was left alone "by design".

| job | runner, Python | what it does |
|---|---|---|
| `lane-ordinary` | ubuntu-24.04; 3.10.15 / 3.11.10 / 3.12.7 | the whole SDK suite at the frozen rev, deps pinned by `tests/lanes/constraints.txt`, E2E flags UNSET, optional providers installed; verifies the collection against `tests/lanes/FROZEN_INVENTORY.json` (4,242 node ids), that the mandatory routing/refusal tests ran, and that optional-provider skips have the R5 reason |
| `lane-runtime-present` | ubuntu-24.04; 3.12.7 | the whole suite at the frozen rev with `AGENTNODE_SANDBOX_E2E`, `AGENTNODE_AGENT_SANDBOX_E2E`, `AGENTNODE_EGRESS_E2E` = 1, Docker server pinned to 28.0.4 and verified, sandbox image pinned by digest |
| `lane-runtime-absent` | ubuntu-24.04; 3.12.7 | physically removes docker/podman from the runner, then runs `tests/test_shipped_default_live_paths.py` and verifies the mandatory runtime-absent class |

## 2. Coverage at the current head, measured

The SDK suite at `3fb6da18` collects 9,717 tests (the frozen inventory has 4,242).

**Ordinary suite.** The whole SDK suite runs at the head in `full-checks.yml` (nightly + release, Python
3.10/3.11/3.12) and on PRs in `sdk.yml` (3.12). Both mandatory "ordinary" tests
(`test_shipped_default_routing.py`, `TestMcpBoundaryUnderShippedDefault`) are ordinary tests there.
Optional-provider skips are checked by `tests/test_optional_providers_skip_cleanly.py` (#17), which runs in
those lanes. → covered, except G3 and G4 below.

**Runtime-absent.** `TestLiveGateRefusesWhenNoRuntimeExists` empties `PATH` itself
(`monkeypatch`), so it runs and asserts in every ordinary lane. → covered, except G2.

**Runtime-present (the container boundary).** The E2E-gated files were run at the head with the flags
unset, and every gated skip was mapped to the workflows that run it at the head with the flag set
(`gated_e2e_coverage.py`; raw log `gated-files-without-flags-at-3fb6da18.txt`; result
`gated-e2e-coverage-at-3fb6da18.txt`; pv T-0208):

- **14 gated tests run at the head elsewhere**: `em3c-gateway.yml` (the EM-3C vertical flow, restricted
  egress, controlled destination, the CLI journey, cancel consistency) and `sdk-conformance.yml` (the
  resistant-payload timeout).
- **13 gated tests run NOWHERE at the head.** Their only runner is `sdk-lanes`, at the frozen commit:

| file | tests |
|---|---|
| `test_sandbox_e2e.py` | `test_toolpack_build_and_run_real`, `test_run_failclosed_when_volume_removed`, `test_mcp_starts_in_container_real`, `test_mcp_preinstall_cache_lands_only_in_private_tmp`, `test_mcp_preinstall_fails_closed_on_an_unusable_cache_path`, `test_npm_mcp_starts_in_container_real`, `test_npm_preinstall_cache_lands_only_in_private_tmp`, `test_npm_preinstall_fails_closed_on_an_unusable_cache_path` |
| `test_agent_sandbox_e2e.py` | `test_agent_sandbox_e2e` |
| `test_egress_e2e.py` | `test_egress_e2e_matrix` |
| `test_agent_session_container.py` | `test_container_isolation_real` |
| `test_r1_runtime_remediation.py` | `TestMemoryCeiling::test_real_container_cannot_exceed_the_ceiling`, `TestMemoryCeiling::test_real_container_below_the_ceiling_still_runs` |

So deleting `sdk-lanes` loses **no** coverage of the current code — it never had any — but it does
delete the last place these 13 container-boundary tests run at all. That gap exists today.

How the raw log was made (Windows, SDK venv, from `sdk/`, flags unset; POSIX-only skips are ignored by
the tool):

    python -m pytest -q -p no:cacheprovider -p no:randomly -rs --tb=no tests/test_sandbox_e2e.py \
      tests/test_agent_sandbox_e2e.py tests/test_egress_e2e.py tests/test_agent_session_container.py \
      tests/test_runtime_pin.py tests/test_r1_runtime_remediation.py tests/test_cancel_consistency.py \
      tests/test_em3c_cli.py tests/test_em3c_gateway.py > log.txt
    python ../docs/testing/slice3/gated_e2e_coverage.py log.txt

Counter-check of the tool (pv T-0208): with the E2E flag lines removed from `em3c-gateway.yml` it must
report those tests as run nowhere; with the file restored, the numbers above again.

### Gaps, each with what closes it

- **G1 (blocking for the deletion): the 13 tests above run nowhere at the head.** Close by adding a
  `container-present` job to `full-checks.yml` (so nightly and release run it) at the head: ubuntu-24.04,
  the Docker pin and the sandbox-image digest exactly as `sdk-lanes` verifies them, the three flags set,
  the five files above, and a guard that fails the job if any of them SKIPS (pytest exits 0 on a skip;
  `em3c-gateway.yml` already has that guard pattern). Re-run `gated_e2e_coverage.py` afterwards: it must
  report `run nowhere at head: 0`.
- **G2: runtime-absent is simulated, not measured.** Optional: a `runtime-absent` job in `full-checks.yml`
  copying `sdk-lanes`'s removal step and running `test_shipped_default_live_paths.py` at the head.
- **G3: no collection-loss check at the head.** `FROZEN_INVENTORY.json` describes the frozen commit only.
  Recommendation: accept the loss for nightly (a vanished test file shows in review), or later freeze a new
  inventory per release. Not blocking.
- **G4: floating dependencies.** `sdk-lanes` installs with `-c tests/lanes/constraints.txt`; `full-checks`
  and `sdk.yml` do not, which is how mcp 2.3.0 turned PR #194 red (#38). Recommendation: decide
  separately whether nightly stays floating on purpose (it found a real incompatibility) and release uses
  the constraints. Not blocking.

### What must NOT be deleted with `sdk-lanes.yml`

`sdk/tests/lanes/` is used by seven other workflows and four tests (counted again in the integration
candidate):

- `constraints.txt` — `em3c-two-roles.yml`, `em3c-windows.yml`, `sdk-conformance.yml`, `a9-podman.yml`,
  `em3c-gateway.yml`, `full-checks.yml` (the `container-present` job), held by `tests/test_ci_full_lanes.py`;
- `eg8_counter_check.py` — `eg8-rebinding.yml`; `npm_e2e_pins.json` — `tests/test_sandbox_e2e.py`;
- `freeze_inventory.py`, `FROZEN_INVENTORY.json`, `conftest_report.py` — `tests/test_frozen_inventory_controls.py`
  (and named in `tests/test_optional_providers_skip_cleanly.py` and `docs/sandbox/_facts/test-evidence.json`).

## 3. The deprecated TypeScript CLI (`cli/`, npm `agentnode-cli` 0.4.0)

Deprecated and frozen (its README; `publish-cli.yml` header): no sandbox, no signature verification, no
Guard, no MCP. The maintained CLI is the `agentnode` command of the Python SDK, tested in the SDK suite.

| item | content | runs |
|---|---|---|
| `cli/tests/api.test.ts`, `commands.test.ts`, `sprint_c.test.ts` | 61 vitest cases (5 + 44 + 12) | `cli.yml`, only on changes under `cli/**`, Node 18 and 20 |
| `.github/workflows/cli.yml` | lint, build, test | as above |
| `.github/workflows/publish-cli.yml` | npm publish | manual dispatch only |

None of this is run by `sdk.yml`, `full-checks.yml`, `nightly.yml` or `release.yml`, and nothing in those
needs it: removing it loses only the tests of a frozen, deprecated package. No test or workflow outside
`cli/` reads the directory.

**One dependent outside the repo's tests:** `action/action.yml` runs `npm install -g agentnode-cli` — the
published npm package, not this directory. Deleting `cli/` does not break the action, but the action
still installs the deprecated CLI. That is a separate follow-up (migrate the action to `pip install
agentnode-sdk`), not part of the deletion. Blog/article texts in `backend/scripts/*.json` mention the npm
package; content, not tests.

## 4. The exact change list for #31

**Step A — additive, before anything is deleted (no Chef approval needed, normal review):**

1. `full-checks.yml`: add the `container-present` job described under G1 (five files, three flags, Docker
   and image pins and their verification copied from `sdk-lanes.yml`, a no-skip guard).
2. Optional: the `runtime-absent` job (G2).
3. Extend `sdk/tests/test_ci_full_lanes.py` so the new job(s), the flags and the no-skip guard are pinned.
4. Run nightly once by `workflow_dispatch`; re-run `gated_e2e_coverage.py` → `run nowhere at head: 0`.

**Step B — deletions (Chef approval, #31):**

| delete | why it is safe after step A |
|---|---|
| `.github/workflows/sdk-lanes.yml` | tests a frozen commit; its runtime-present tests run at the head in step A |
| `.github/workflows/cli.yml` | tests only the deprecated npm CLI |
| `.github/workflows/publish-cli.yml` | publishes only the deprecated npm CLI; already manual-only |
| `cli/` (source, `tests/`, package files) | the deprecated CLI itself; nothing in the repo reads it |

**Step B — edits that go with the deletions:**

- `.github/workflows/managed-access.yml` line 13 and `sdk/HANDOFF.md` item 4: the sentence about
  `sdk-lanes.yml` being left alone becomes history ("removed in #31").
- `sdk/tests/test_optional_providers_skip_cleanly.py` lines 10 and 26: the comments name `sdk-lanes.yml`
  as a reader of the skip reason; point them at `tests/lanes/freeze_inventory.py` only.

**Kept on purpose:** `sdk/tests/lanes/` entirely (section 2), the frozen inventory and its test.

**Not part of #31:** migrating `action/action.yml` off the npm CLI; deprecating/unpublishing the npm
package on npmjs.com (an outward action, Chef decision).
