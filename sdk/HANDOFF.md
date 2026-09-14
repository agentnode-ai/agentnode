# Where this is, and what comes next

Branch `managed/access-and-onboarding`. Nothing pushed, merged or deployed. 8099 closed.

**Frozen review profile:** `managed-console-final-r1`, sha256
`0068b9ac2346790717cf1b22848e54dc8d9fc3f22281212d993680604200ffe9`, copy in
`docs/review/`. Do not edit it. The final review runs against it, on the exact final commit.

## How to run the tests

On the test host (`root@116.203.32.193`, key `/c/Users/User/.ssh/a1e_spike`), tree synced to
`/root/rr/sdk`, venv `/opt/agentnode/venv`:

```
AGENTNODE_BROWSER_TESTS=required PYTHONPATH=/root/rr/sdk PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest -q -p no:randomly tests/ \
  --ignore=tests/test_verification_run.py --ignore=tests/test_verification_channels.py \
  --ignore=tests/test_async_client.py
```

`test_verification_run` is not a release gate. `test_async_client` needs `respx` and `httpx`
installed in the venv; it is declared in the `dev` extra and is unrelated to this work.

**Known-failing baseline — 7, identical at `f686861` and now.** Compare against this list before
treating anything as a regression; a clean baseline tree can be rebuilt with
`git archive <commit> sdk | tar -x -C /root/base`.

```
test_agent_m1_amendment.py::test_install_scheme_refuses_non_writable_site
test_agent_m1_transaction.py::test_cross_lockfile_entry_not_blocked
test_agent_m1_transaction.py::test_happy_path_commits_sealed_fields
test_agent_m1_transaction.py::test_same_version_different_bytes_reinstalled
test_agent_m1_transaction.py::test_transaction_build_receives_controlled_env
test_layer3_installer_concurrency.py::test_kill_mid_quarantine_recovers_under_same_lock
test_stopping.py::TestNothingGrowsWithoutLimit::test_the_hands_are_a_fixed_number...
```

Run the whole tree, not a named subset. Three real defects this cycle showed up **only** in a
full run: a kind no rendering knew, a handoff keyed by `id()`, and a second wording for the kill
switch. A named subset is how they stayed hidden.

## Settled — do not reopen without a reproducible defect

* **Bounded cancellation** (`access/stopping.py`): fixed pool, one stop per run, durable across
  restart, rate limited, operator stop applies.
* **Revocation**: `MANAGED-REVOCATION-0001` chose Option A. No `device_revoked`; the identity
  re-check stays and raises the generic refusal.
* **Mandatory classification**: `audience` / `risk` / `needs` / `confirms_with_a_person` have no
  defaults. Unclassified fails the contract, every generator, the gateway start, and the
  dispatcher. Nothing is derived from an operation's name.
* **Consent binding**: `submit` recomputes the disclosure for the job in hand.
  `dispatch.BOUND_BY_THE_DISCLOSURE` is the list; a test asserts the required set separately,
  because a test parametrised over that list cannot catch an entry being removed from it.
* **Approval vs execution channel**: `approved_by` is observed, `will_run_as` is chosen at
  prepare, shown, and bound. Browser-approve → MCP-execute works; nothing else does. A refused
  submission does not consume the approval.

## Track 1 — COMPLETE

Done in this commit:

* `gc.prepare()` added; `gc.submit()` takes `accepted_disclosure` and carries it **inside** the
  signed payload. `gc` still submits over the older door, and tells `prepare` so
  (`execution_channel="older_door"`).
* `/v1/jobs` is a translator: parses with `JobRequest.from_payload` (the parser owns the wire
  format's own checks), hands everything to the dispatcher as claims, renders the record the
  dispatcher produced. Refusals are refused *records*, signed, with `refused` / `what_to_do` /
  `needs_client` alongside.
* The contract can now carry `artifact_sha256`, `policy_sha256` and `issued_at` as claims that
  are **checked**, and `network` grew `unrestricted`. Vocabulary translation (`restricted` →
  `allowlist`) lives at both ends of the older door.
* Consent is the LAST gate: integrity and replay are refused first, so a tampered or replayed
  request is told what is actually wrong with it.
* CLI: `remote test` and `remote run` show the disclosure and ask. `--yes` says a person has
  read it. No terminal and no `--yes` → nothing runs.
* `tests/consent.py` is how tests submit. It lives in the tests, not the SDK, so the convenience
  cannot creep into the client.

All four older addresses are translators now: `/v1/jobs`, `/v1/jobs/<run>`,
`/v1/jobs/<run>/cancel` and `/v1/token/rotate`. `access/routes.py` records what each one's
clients read, and therefore what its translation must not lose. Nothing decides for itself.

The older cancel answers 202 at once. `gc.cancel(conn, run, settle=...)` and
`agentnode remote cancel --wait` do the waiting client-side; what `settled` means is unchanged.

`hello` and `pair` go through `dispatch.before_anyone()`, which refuses anything that is not one
of those two. Neither is a declared operation, so neither can reach any schema a model is handed
-- nothing to filter rather than a filter. Both are audited. `hello` says a written-down list
(`dispatch.WHAT_A_STRANGER_IS_TOLD`), so a field added to the gateway's own view of itself is not
published by being added.

`tests/test_one_way_in.py` reads the request handler's AST and asserts no route reaches anything
on the service except `sign_answer`, `stamp` and `state`. `docs/managed-access-migration.md` is
rewritten for protocol 2.

One design fact worth not relitigating: an invitation is good for ONE ATTEMPT. The claim is made
before the code is compared, so a wrong guess spends it. That is deliberate and makes guessing
structurally impossible; the cost is that anybody who can reach the port can burn an open
invitation, bounded by the throttle and the 15-minute window.

## Tracks 2–5, not started

* **B3** browser session: HttpOnly/Secure/SameSite cookie, CSRF token in memory only, invitation
  fragment wiped, no durable JS-readable token, server-side revoke, OS keyring for CLI/bridge.
  Note: the console is currently one inline `<script>`; a strict CSP with no inline exception
  means splitting it into `/console/app.js` served from the same handler.
* **B5** compatibility challenge: enrol a fresh device for the chosen way in, one-time challenge
  bound to account/device/channel/operation/nonce/expiry, COMPATIBLE only from an audited call.
  `principal.via` and the audit's `via` field already exist for this.
* **B7** the German UI end to end.
* **Integration, counter-checks, final Codex review.**
