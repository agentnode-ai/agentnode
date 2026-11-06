# Cloud sandbox: what stands between the alpha and test users

Task #11 of PLAN-2026-11.md ("Lücken zur Produktionsreife auflisten und schließen (Single-Host-Alpha),
Limits je Konto"). Status as of 2026-11-06, read from the code and the documents named in each row,
on branch `work/fundament-2026-11`. Nothing here was measured on a server for this list; where a row
rests on an earlier server run, it says so and says where that record lives.

"Closed" means closed in the code on this branch and tested locally. Nothing on this branch is
deployed.

## Closed in task #11

| gap | what changed | evidence |
|---|---|---|
| **No limits per account.** The account ceilings were one value for every customer, so a trial could not be held to less than anyone else. | Each account can carry its own account ceilings (`account-ceilings.json`, `agentnode gateway limits --account <id> ...`); the check before the start, the binding booking and the run's record read them through one function; the file is read strictly. | `sdk/tests/test_account_ceilings.py`; T-0082/T-0083 red, T-0088/T-0089 green (commits `b4562a8a`, `ae595c92`) |

Not part of it: the per-account *rate* (`account_requests_per_minute`) is still one value for every
account, and no ceiling sizes are chosen -- those are task #15.

## Still open

| # | gap | why it blocks test users | what closing it needs | tracked as |
|---|---|---|---|---|
| 1 | **Control plane and worker share one kernel** (`single-host-development`). A sandbox escape reaches the host holding every client's token, the signing identity and the ledger. | `deploy/RISKS.md` (HIGH): nothing on this topology may be called production-ready, multi-tenant or isolated. | The `separate-worker-host` topology exists in code (`deploy/separate-worker-host/`, mTLS transport, role manifest) but its two-machine measurement has **never passed**: the acceptance run of 2026-09-30 on rebuilt machines blocked on five of ten criteria (records outside the repository). A passing run on the Hetzner cross-host stand, then an independent review. Server use is a founder gate. | task #23 (blocked: founder gate) |
| 2 | **The worker may execute setuid binaries** (rootless podman needs `newuidmap`; `NoNewPrivileges` cannot be set). | `deploy/RISKS.md` (MEDIUM), retained deliberately. | Re-assessed on the dedicated worker host as part of row 1, not carried across. | with row 1 |
| 3 | **What was measured was measured on one host** (Fedora 44, systemd 259, podman 5.8.4). | `deploy/RISKS.md` (LOW). The ceiling measurement re-runs itself on any new host; the privilege questions do not. | Part of row 1's acceptance on the worker host. | with row 1 |
| 4 | **The free allowance is not one-off.** -- CLOSED in task #12 on this branch: `account_lifetime_runs` per account, never refilled by a window or a restart, booked in the same transaction as the window ceilings, refused with the way on (a sandbox of your own), reset only per named account. | -- | What is left is a decision, below. | task #12 (done), task #15 |
| 5 | **No prices, no payment provider, no billing.** The disclosure says so (`sdk/docs/managed-access-migration.md`). | Nothing to offer beyond a free trial. | Founder decision on prices, trial size and provider. | task #15 (Chef) |
| 6 | **Offering it publicly is not decided** (liability, cost); port 8099 stays closed (`sdk/docs/review/FREEZE-R2.md`). | Test users cannot reach it. | Founder decision. | PLAN-2026-11.md section 4 (Chef) |
| 7 | **One account per website login does not exist**: website account and gateway account are separate. | A test user would need two identities. | Task #8 (waits for the live remote-MCP test, #6). | task #8 |
| 8 | **The risk page contradicts the code.** `deploy/RISKS.md` says the transport to a remote worker "is not written" and that relocating the worker "is not something the shipped code can do"; `deploy/separate-worker-host/` and its mTLS transport exist. | An operator or reviewer reads the wrong state. | Bring `deploy/RISKS.md` in line with `deploy/separate-worker-host/README.md` (written, not yet measured). | task #24 |
| 9 | **The availability page is stale** (generator refuses since 2026-09-04; the gateway shown as planned). | Readers are told the wrong state. | Task #20 (parked on T-0081). | task #20 |
| 10 | **What the deployed alpha runs is not known from here.** The DevelopServer alpha was last recorded in September; this branch is not deployed. | Gaps closed on this branch are not closed for anyone using the alpha. | A deployment, which is a founder decision; not part of this list's closing. | -- (deployment gate) |

## Left to task #15 (the founder's decisions)

Task #12 built the mechanism and decided no numbers. Still open, and not to be assumed:

* **the size of a trial** -- `PROVISIONAL_TRIAL_LIFETIME_RUNS` in `agentnode_sdk/gateway/allowance.py`
  is a placeholder (10 runs) and every place that shows it says so;
* **whether a trial is counted in runs only**, or also in sandbox seconds (only runs are counted
  one-off today; seconds are counted per window);
* **who gets a trial and when** -- today an operator sets it per account; nothing grants one
  automatically when an account is created;
* **what "pay" means after the trial** -- no price, no payment provider and no billing exist, so the
  refusal names the one alternative that does exist: a sandbox of your own.

## Not claimed, by design

Per-job data classification, regions and retention; external attestation of cleanup; protection
against someone who can write to the gateway's directory. The documents say so
(`sdk/docs/managed-access-migration.md`, `sdk/docs/self-hosted-sandbox.md`), and this list does not
reopen them.
