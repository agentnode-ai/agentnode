# Customers, and what bounds them

Written for whoever operates this gateway. Everything here is a command that exists.

## The six things that are not each other

| | |
| --- | --- |
| **operator** | whoever runs this gateway. Not an account, and no account becomes one. |
| **account** | the customer. Owns devices, carries ceilings, can be suspended as a unit. |
| **person** | somebody who signs in. Today a person is represented by the device they paired; there is no separate sign-in, and this says so rather than implying otherwise. |
| **device** | one paired credential: a laptop, a server, a CLI. |
| **AI connection** | a device whose holder is a model. The same kind of record with a different label — **not** a different permission system. |
| **run** | one execution. Owned by the device that submitted it, inside that account. |

The interesting mistakes are conflations: an account that is really a device, an AI connection
that is really an account, an operator action attributed to a customer.

## How a customer comes to exist

Redeeming an invitation creates one:

```
agentnode gateway pair                       # a NEW customer
agentnode gateway pair --account acct-...    # a second machine for one that exists
```

The account an invitation lands in is decided **when the invitation is made**, by the operator.
Whoever redeems it cannot name one — that would be a way to walk into somebody else's account by
guessing its name.

A connection set up from inside the console joins the account of the session that set it up,
because that is what "my AI" means.

```
agentnode gateway accounts      # who is here, how many devices each, and their standing
agentnode gateway clients       # every device, with the customer it belongs to
```

## What a device paired before this existed belongs to

**Its own account**, named `solo:<device>`. That is the restrictive reading, chosen deliberately:
putting every existing device on a gateway into one shared account because they happened to be on
one machine would create cross-customer visibility during an upgrade, where nobody is looking.

Verified on the running alpha: 19 devices came back as 19 accounts.

## What one customer cannot see or reach

Enforced in the dispatcher, on every door, and tested by constructing two real accounts and
asserting the refusal rather than by asserting a helper returns a filtered list:

* another account's devices, sessions, runs, run output, usage figures, invitations, enrolments,
  metering lines or audit lines;
* withdrawing or rotating another account's device, ending its sessions, cancelling its runs,
  consuming its enrolment;
* anything of the operator's — the stop, the ceilings, the policy, suspension, retention, the
  metrics. None of those is a contract operation, so no capability a customer can hold reaches
  them over any door.

A run belonging to another account is answered as a run that does not exist, in the same words,
with the same remedy. Telling somebody that a run exists but is not theirs tells them it exists.

## Ceilings

Two scopes, **both applied**, and the tighter one decides. A per-device ceiling alone is one a
customer raises by pairing another machine, which is not a ceiling.

```
agentnode gateway limits                                  # what is set
agentnode gateway limits --runs-per-window 200            # one device
agentnode gateway limits --account-runs-per-window 500    # one customer, all their devices
agentnode gateway limits --requests-per-minute 120        # a burst is not the same as a quota
agentnode gateway limits --max-artifact-bytes 5242880     # the largest job accepted
```

| | bounds | enforced by |
| --- | --- | --- |
| `concurrent_runs`, `account_concurrent_runs` | how many at once | the gateway, under one lock with the record insertion |
| `runs_per_window`, `account_runs_per_window` | how many in a window | the gateway, claimed and judged in one transaction |
| `seconds_per_window`, `account_seconds_per_window` | sandbox time in a window | the same |
| `requests_per_minute`, `account_requests_per_minute` | rate, on **every** operation | the gateway |
| `max_artifact_bytes` | the size of a job | the gateway, before the bytes go anywhere |
| cpu, memory, processes, disk, wall clock | what a running job may use | the container runtime, and only as far as it was **measured** to |

Zero means no ceiling of that kind. A file naming a ceiling this build does not understand is
**refused**, not ignored: a setting that reads as configured and is not applied is worse than none.

An unreadable ceilings file, use record or accounts file means **this gateway is not taking work**,
and says which file. That is the third place in this gateway where the permissive fallback was the
bug, and it is not left as one here.

## Stopping a customer

```
agentnode gateway accounts --account <id> --suspend --reason "..."
agentnode gateway accounts --account <id> --restore
```

A suspension **must** say why; the customer is shown those words. Their work is refused from the
next request; they can still read what they already did, because a suspended customer who cannot
check the operator's account of what they did has no way to answer it.

Runs already going are not stopped by a suspension. Stopping those is what the stop is for.

## What none of this does

**It does not determine what a job is for.** A program that reads a network socket is a backup
client or an exfiltration tool depending on facts that are not in the program. What is implemented
is technical limits, operator rules, behavioural signals and the ability to suspend — between
them the damage a customer can do is bounded and a customer can be stopped, and that is the whole
of the claim.

A behavioural signal is evidence of a **pattern** and never of a purpose. A burst of refusals
looks like probing and also looks like a broken script.

## What is kept, and for how long

Most of what this gateway holds forgets by itself: sessions and enrolments expire, use and rate
counters are rolling windows, ledger nonces are pruned by age, and a job's output is held in
memory for the run and never written to disk.

Two things persist, and both have periods an operator sets:

```
audit.jsonl      90 days   every operation attempted, so a probe is visible afterwards
use-log.jsonl   400 days   what each run used, which is what a bill is eventually made from
```

A customer can be given everything held about them, and can be deleted. Deletion removes their
devices, sessions, enrolments, counters, ledger entries, audit lines and account record, and
**erases** their metering lines — replacing the contents with a signed tombstone that keeps the
link the next line needs. The chain still verifies, still says a line was erased and when, and an
*unauthorised* removal is still caught, because forging a tombstone needs the signing key.

## What this does not establish

The topology is `single-host-development`. Two customers' jobs run on one kernel. Everything
above is about what one customer can **read, reach or withdraw** through the service. It is not
about what one customer's running job could do to another's through the machine, and nothing here
should be read as though it were. That needs a separate worker host.
