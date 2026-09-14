# Where this is

Branch `managed/access-and-onboarding`, final commit `7e616b8`. Nothing pushed, merged or
deployed. 8099 closed.

**Frozen review profile:** `managed-console-final-r1`, sha256
`0068b9ac2346790717cf1b22848e54dc8d9fc3f22281212d993680604200ffe9`. Frozen in `acecc0f`, which
contains no code; fifteen commits of implementation follow it. Do not edit it. The branch copy in
`docs/review/` must stay byte-identical to the one the reviewer loads.

## How to run the tests

On the test host (`root@116.203.32.193`, key `/c/Users/User/.ssh/a1e_spike`), tree synced to
`/root/rr/sdk`, venv `/opt/agentnode/venv`:

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

## What remains

1. The final Codex review under `managed-console-final-r1`. Bundle assembled at
   `C:\Users\User\Desktop\a1e_review\managed-console\` (42 inputs); runner at
   `scratchpad/final_review.sh`, which verifies the profile digest and every input digest before
   each attempt and retries only on capacity errors.
2. Whatever that verdict asks for.
3. The official CI lanes have not been run against this branch — the founder asked for them as
   part of the final assessment, and every run above is on the test host.
4. Human validation remains DEFERRED_EXTERNAL_VALIDATION. No person has used this unaided, and no
   automated result establishes that one could.
