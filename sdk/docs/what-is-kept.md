# Everything this gateway stores, field by field

Written so a customer can check it against the files on disk rather than take it. Every row names
the file, the field, why it exists, and what removes it. A field with no reason is a field to
delete, and a review was right that a list of *classes* cannot show that there is no such field.

Read `sdk/docs/accounts-and-limits.md` for what the periods are and how to change them, and
`sdk/agentnode_sdk/gateway/retention.py` for the code.

## The rule these rows are checked against

Each field is here because something the service **does** stops working without it. Not "it might
be useful later" and not "it was easy to record". Where a field is kept for a reason that is not
strictly operational — the audit is the main one — the row says so.

## `tokens.json` — who may reach this gateway

Owner-only, and the only file besides `pairing.json` that holds anything secret-adjacent.

| Field | Why | What removes it |
| --- | --- | --- |
| *the key* — sha256 of the token | recognising a credential without storing one. A leaked file must not be a working key. | `devices.revoke`, `gateway revoke`, account deletion |
| `client_id` | a device's identity, which must survive rotating its credential — otherwise replacing a secret orphans the runs that device submitted | the same |
| `account_id` | which customer. Everything a customer is — ceilings, suspension, billing, deletion — hangs off this rather than off any one credential | the same |
| `client_name` | what a person calls this device in their own list. Chosen by them, shown only to them | the same |
| `issued_at` | "paired on", in the device list. Also how the list is ordered | the same |
| `allowance` | what this one device may reach, when an operator has narrowed it below the account | the same |

**Not here:** the token, any password, anything about the person, any address.

## `accounts.json` — customers

Only accounts something has been *recorded about* appear. An account with no record is active;
see `gateway/accounts.py` for why absence is not a permissive fallback.

| Field | Why | What removes it |
| --- | --- | --- |
| `account_id` | the customer | account deletion |
| `name` | what the operator calls them | the same |
| `created_at` | when | the same |
| `state` | active or suspended. Admission reads it before every job | the same |
| `suspended_because` | the operator's own words, shown to the suspended customer. Without it a suspension is unactionable | cleared by `--restore` |
| `suspended_at`, `suspended_by` | when, and by whom. An operator action has to be attributable | the same |

## `use.json` — the rolling counters

| Field | Why | What removes it |
| --- | --- | --- |
| *the key* — a device id or an account id | which ceiling this counts against | the window passing, or deletion |
| `run_id` | so a run's seconds can be added when it finishes | the window passing (24h by default) |
| `at` | so use is forgotten by **time** and not by count — a counter that dropped its oldest when full is one an attacker empties by sending enough | the same |
| `seconds` | the wall clock ceiling counts seconds | the same |

## `rate.json` — the per-minute counters

Key and a list of timestamps. Nothing else. Entries outside the minute are dropped on every write.

## `ledger.json` — what was accepted, so a restart does not re-run it

| Field | Why | What removes it |
| --- | --- | --- |
| `runs[run_id].first_seen`, `.state` | a run that was accepted and may have executed must survive a restart, or the gateway re-runs it | age (pruned), run deletion, account deletion |
| `.request_sha256` | so the same run id cannot be re-claimed for different bytes | the same |
| `.owner_client_id`, `.owner_account_id` | so a run rebuilt after a restart is still attributable to somebody | the same |
| `nonces[nonce]` | replay protection. **Deliberately survives deleting a run**: a nonce identifies nobody, and dropping it would make the signed request replayable | age only |
| `challenges[run_id]` | the digest of the challenge issued for a run, written before the job starts | age, deletion |

## `use-log.jsonl` — the metering chain

Exactly the fields in `meter.FIELDS`, and there is no field that takes free-form content — a meter
with somewhere to put "anything else" is a meter that will one day hold a secret.

| Field | Why | What removes it |
| --- | --- | --- |
| `run_id`, `account_id`, `client_id` | who is charged for what. All three are **required**; `(unattributed)` is a value passed deliberately, not an empty string | erasure (retention period, run deletion, account deletion) |
| `started_at`, `finished_at`, `seconds` | what was used | the same |
| `cpu`, `memory_mb`, `wall_clock_s` | what it was granted | the same |
| `state`, `outcome` | how it ended. A refused run and a finished one are billed differently, whatever the price turns out to be | the same |
| `bytes_out` | how much it wrote. **Not what it wrote** | the same |
| `worker_topology`, `worker_topology_means`, `worker_id` | where it ran, and what that arrangement does not protect against — a reader months later has no other way to know | the same |
| `operator_policy_sha256`, `operator_policy_version` | under which rules. A customer disputing a charge asks this, and a configuration file that has since been edited is not an answer | the same |
| `allowance_sha256`, `allowance_admitted_under` | which ceilings were in force, by digest **and** by value, because a digest cannot be turned back into numbers | the same |
| `seq`, `prev`, `signature` | what makes editing, removing, reordering or truncating visible | never — an erased line keeps its link as a signed tombstone |

Erasure replaces the contents and keeps `seq`, `erased_at`, `erased_because` and `stood_for`.
Nothing about who or what survives it.

## `audit.jsonl` — every operation attempted

The one file kept for a reason that is not strictly operational: **a probe looks like refusals**,
and a log that only kept successes would not show one. Default period 90 days.

| Field | Why | What removes it |
| --- | --- | --- |
| `at` | when | the retention sweep, account deletion |
| `operation` | which one — and **only if it is one the contract declares**, never a caller's string | the same |
| `device`, `account` | who. Both established by the server from the credential | the same |
| `via` | which door. Named by the adapter; a value a caller could choose would let it claim to have arrived somewhere it never did | the same |
| `outcome` | from a closed list | the same |
| `about` | which *declared parameter names* a refusal concerned. **Never a value** | the same |

There is no run id here, and no job content. Deleting a run therefore has nothing to remove from
the audit, which is why `delete_run` reports `audit_lines: 0` rather than pretending.

## `sessions.json`, `enrolling.json`

Both expire on their own and are removed when they do. Sessions hold a fingerprint (never the
session identifier), the device, a label a person chose, and three timestamps. Enrolments hold the
account, the device that started it, the channel, a label, a nonce, a one-time ticket and two
expiries.

## `exports.jsonl` — who took a copy

| Field | Why |
| --- | --- |
| `at`, `account_id`, `by`, `bytes` | so "who has a copy of this, and since when" is answerable. It is the first question asked when a copy turns up somewhere it should not be |

## What is NOT stored anywhere

* **A job's code.** Held in memory for the run and never written to disk.
* **A job's output.** The same. It is handed to the caller who submitted it and then it is gone
  with the record.
* **Any token, pairing code or session identifier**, in any file. Hashes and fingerprints only.
* **Anything about a person** beyond a label they chose for their own device. No name, no email,
  no address, no payment detail — none of those exist in this product.
* **Any IP address.** Deliberately: see `throttle.py` for why nothing here keys on one.

## What survives things that are supposed to remove data

Stated because it is the part people assume wrongly:

* **A backup taken before a deletion still contains the deleted customer.** Deletion cannot reach
  a file it does not have. The schedule on which backups are retired *is* the retention policy
  for backups.
* **An export already handed over is a copy somebody else holds.** `exports.jsonl` records that
  one was taken; it cannot un-take it.
* **A tombstone remains** where a metered line was, saying a line existed, when it went and why —
  and nothing about who. Removing tombstones outright would mean anybody who can delete can
  remove a line invisibly, which is the property the chain exists to have.
