# Where this is

Branch `managed/access-and-onboarding`, head `a5ec1cc`. Nothing pushed, merged or deployed.
8099 closed.

**The review is PASSED.** `MANAGED-CONSOLE-FINAL-0006`, all fourteen criteria of the frozen
profile, at commit `4ca44a1`. `sdk/` is byte-identical between `4ca44a1` and the head above --
the one commit since is the restoration of 23 files outside `sdk/` (see below), so the reviewed
object has not moved.

**Frozen review profile:** `managed-console-final-r1`, sha256
`0068b9ac2346790717cf1b22848e54dc8d9fc3f22281212d993680604200ffe9`. Frozen in `acecc0f`, which
contains no code; fifteen commits of implementation follow it. Do not edit it. The branch copy in
`docs/review/` must stay byte-identical to the one the reviewer loads.

## How to run the tests

On a Linux host with Chromium and the dev extra installed. The machine this was measured on
is not named here: an address and a key path in a public repository are an invitation to
try them, and they are of no use to anybody reading this anyway.

```
AGENTNODE_BROWSER_TESTS=required PYTHONPATH=/root/rr/sdk PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest -q -p no:randomly tests/ \
  --ignore=tests/test_verification_run.py --ignore=tests/test_verification_channels.py \
  --ignore=tests/test_async_client.py
```

`test_verification_run` is not a release gate. `test_async_client` needs `respx`, declared in the
dev extra and unrelated to this work.

**Known-failing baseline — 7, identical at `f686861` and at `7e616b8`.** Rebuild the baseline
with `git archive <commit> sdk | tar -x -C /root/base` and run the same command; do not treat
anything on this list as a regression.

```
test_agent_m1_amendment.py::test_install_scheme_refuses_non_writable_site
test_agent_m1_transaction.py::test_cross_lockfile_entry_not_blocked
test_agent_m1_transaction.py::test_happy_path_commits_sealed_fields
test_agent_m1_transaction.py::test_same_version_different_bytes_reinstalled
test_agent_m1_transaction.py::test_transaction_build_receives_controlled_env
test_layer3_installer_concurrency.py::test_kill_mid_quarantine_recovers_under_same_lock
test_stopping.py::TestNothingGrowsWithoutLimit::test_the_hands_are_a_fixed_number...
```

**Run the whole tree, not a named subset.** Four real defects this arc showed up only in a full
run: a field kind no rendering knew, a handoff keyed by `id()`, a second wording for the kill
switch, and an intermittent teardown from a test that raised out of a cancellation.

The suite runs headless, so `tests/conftest.py` sets `AGENTNODE_CREDENTIALS=file` once. The
refusal when there is nowhere safe has its own tests in `test_credentials.py`.

## What is settled — do not reopen without a reproducible defect

* **Bounded cancellation** (`access/stopping.py`): fixed pool, one stop per run, durable across
  restart, rate limited, the operator's stop applies. `status` says `stopping` until cleanup is
  confirmed.
* **Revocation**: `MANAGED-REVOCATION-0001` chose Option A. No `device_revoked`; the identity
  re-check stays and raises the generic refusal.
* **Mandatory classification**: `audience`/`risk`/`needs`/`confirms_with_a_person` have no
  defaults. Unclassified fails the contract, every generator, the gateway start and the
  dispatcher. Nothing is derived from a name.
* **Consent**: `submit` recomputes the disclosure for the job in hand.
  `dispatch.BOUND_BY_THE_DISCLOSURE` is the list, and the required set is asserted separately —
  a test parametrised over that list cannot catch an entry being removed from it.
* **Approval vs execution channel**: `approved_by` observed, `will_run_as` chosen and bound.
  Browser-approve → MCP-execute works; nothing else does. A refused submission does not consume
  the approval.
* **Every route**: contract, translator, bootstrap, or a file. `test_one_way_in.py` reads the
  handler's AST and requires no route to reach anything on the service but `sign_answer`,
  `stamp`, `state`.
* **An invitation is good for ONE ATTEMPT.** The claim is made before the code is compared, so a
  wrong guess spends it. Deliberate: it makes guessing structurally impossible. The cost — anyone
  who can reach the port can burn an open invitation — is bounded by the throttle and the window.
* **Browser sessions**: no token in the browser, `__Host-` cookie, CSRF on state changes, only
  hashes stored, sessions listable and endable, device revocation ends them.
* **Compatibility**: only an audited call by the freshly enrolled connection, over the enrolled
  channel, after the challenge was issued.
* **CLI credentials**: keyring, or a refusal that says what to do.
* **A journal that cannot be kept is said out loud.** `unfinished()` raises
  `JournalUnavailable` rather than answering the empty list; a write that fails marks the stop
  undurable, records the reason on the pool, and stops the gateway taking NEW work through the
  operator's own kill switch. The cancellation itself still goes ahead. Absent stays harmless.
* **The service owns the threads it starts.** Run threads are held in `_running`, named
  `agentnode-run-<id8>`, discarded by the thread itself on the way out, and joined by `close()`
  under `CLOSE_SECONDS`; what could not be got back is returned and kept in `left_running`.
  Daemon status is not lifecycle ownership. The page owns its timers the same way: `S.epoch` and
  `S.timers`, `later()` instead of a bare `setTimeout`, and `stopEverythingScheduled()` on
  sign-out, so a poller already in flight cannot act when it returns. `Stopping.close()` and
  `let_the_watchers_go()` both return what they could not get back, and `GatewayService.close()`
  folds the pool's answer into its own instead of discarding it.

## What remains

1. The final Codex review under `managed-console-final-r1`. Bundle assembled at
   `C:\Users\User\Desktop\a1e_review\managed-console\` (42 inputs); runner at
   `scratchpad/final_review.sh`, which verifies the profile digest and every input digest before
   each attempt and retries only on capacity errors.
2. Whatever that verdict asks for.
3. **The lane COMMANDS were run off-CI, and they pass.** The lanes themselves still need a
   founder decision, because reaching them means publishing this branch (below). What could be
   done without that was done -- each lane's own command, verbatim:
   * `sdk` (`pip install -e ".[dev]"`, then `pytest -v` with NO ignores) on Linux: **6 failed,
     5490 passed, 337 skipped**. The six are the installer set. The three files the working runs
     excluded were included here and cost nothing: 70 more passes, no new failures. Python 3.14,
     not one of the matrix's 3.10/3.11/3.12 -- right OS, wrong version, so this is the lane's
     command and not the lane.
   * `adapter-langchain` (`pip install ../sdk -e ".[dev]"`, `pytest -v`) on 3.12: **4 passed**.
   * `web-lint-build` (`npm ci`, `npm run lint`, `npm run build`): all three pass, lint with 0
     errors. THIS ONE WOULD HAVE FAILED an hour ago, and finding that is why it was worth
     running -- see below.
4. **The official CI lanes cannot be run against this branch without a founder decision, and
   one of them could not test it even then.** Two facts, both read from
   `.github/workflows/`:
   * Every lane triggers only on `push`/`pull_request` to `main` (`sdk.yml`, `sdk-lanes.yml`,
     `backend.yml`, `cli.yml`). Reaching any of them means pushing this branch to
     `agentnode-ai/agentnode`, and that repository is **public** — so it is a publication of the
     work, which is a founder gate, not an ordinary step. Nothing was pushed.
   * `sdk-lanes.yml` checks out `ref: ${{ env.FROZEN_REV }}` (`3873170`) in all three jobs, so
     even on a pull request it exercises that pinned commit and not the branch head. It cannot
     report on this work at all. `sdk.yml` (`pytest -v` over `sdk/`) is the lane that would.
   * `deploy.yml` is `workflow_dispatch` only and disabled at the repository level, so a pull
     request would not deploy anything.
5. Human validation remains DEFERRED_EXTERNAL_VALIDATION. No person has used this unaided, and no
   automated result establishes that one could.


## The 23 files this branch deleted and put back

`git add -A` from the repository root staged deletions of files that were absent from the working
tree and had nothing to do with this work: `web/` and `cli/` lockfiles and tsconfigs, and
nineteen `backend/` data and script files. Restored byte for byte in `a5ec1cc`; the branch now
touches nothing outside `sdk/`, which is checkable with
`git diff --stat acecc0f HEAD -- backend cli web` (empty).

`web/package-lock.json` is the one with teeth. The `web-lint-build` job runs `npm ci`, which
refuses to install without a lockfile, so that job -- and with it `sdk-web-required`, the only
required check for this track -- could not have passed. It would not have failed in a way that
looked like a lockfile problem either; it would have failed in CI, later, at somebody else.

The lesson is narrow and worth keeping: `git add -A` from a repository root commits the working
tree's absences as well as its edits. Stage paths, or check `git status` for deletions first.
