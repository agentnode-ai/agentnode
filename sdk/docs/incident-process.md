# When something goes wrong

Paper, not code, and it exists before there are customers rather than after the first incident.

This is written for whoever is holding the machine at the time, which on a small service is one
person and is often the person who wrote the thing that broke. So it is short, it is ordered, and
every step is a command that exists.

## The first decision is always the same

**Stop, or do not stop.** Nothing else comes first.

```
agentnode gateway stop --reason "what you would tell a customer"
```

That refuses all new work AND ends every run that is going. Use it when the problem is the code
running right now — an image being replaced underneath, a job doing something that must not
continue, a host that has to be freed. What you type as the reason is what every client is shown,
so write it for them and not for yourself.

It does **not** stop a customer from reading what already happened, and that is deliberate: the
moment you reach for the stop is the moment somebody needs to see why.

If the problem is **one customer** rather than the machine, suspend them instead and leave
everybody else working:

```
agentnode gateway accounts --account <id> --suspend --reason "..."
```

Neither is reversible by accident. `agentnode gateway resume` and `--restore` are both deliberate
acts.

## Then look

```
agentnode gateway watch      # states, capacity, what is being refused, what is worth looking at
agentnode gateway status     # is it serving, and what has it been measured to enforce
agentnode gateway used --verify   # has the record of use been altered
```

`watch` prints and also appends to `events.jsonl` beside the gateway's other state, so what you
saw is still there tomorrow.

## What each alert means, and what to do about it

| Alert | What it means | First move |
| --- | --- | --- |
| **a sandbox was not confirmed gone** | `critical`. A run finished and its container was not confirmed removed. This is the property everything else rests on. | Look at the worker now. `docker ps` / `podman ps` on the worker host. If something is still up, stop the gateway before anything else runs. |
| **somebody is probing** | Names that do not exist are being tried. Bounded by the rate limit. | Read `events.jsonl` for which account. Suspend if it is one account and it does not stop. |
| **one account is being refused a lot** | A pattern, **not** a finding about what they were trying to do. It looks the same as a broken script. | Contact them before suspending. A customer whose retry loop is misconfigured is not an attacker. |
| **capacity is nearly gone** | Jobs are about to be refused for want of room. | `agentnode gateway limits` to see what is set. Raising a ceiling is a decision, not a fix. |
| **the gateway is stopped** | `info`. Deliberate, if somebody did it deliberately. | If nobody did, find out who. |

## If a credential may have leaked

1. **Withdraw it.** `agentnode gateway revoke --client <id>`. This ends its sessions, drops its
   unspent enrolments, stops its runs in flight, and removes the credential last — in that order,
   so everything is done on behalf of a device the gateway still recognises.
2. **Tell the customer** what was withdrawn and why, and issue a fresh invitation:
   `agentnode gateway pair --account <their account>`.
3. **Read the audit** for what that device did: `audit.jsonl` names the operation, the device, the
   account, the channel and the outcome, and nothing a caller supplied.

## If the gateway's own key may have leaked

The meter key signs the record of use. If it is gone, the record from that moment on is no longer
evidence against anyone, and **saying so is the job** — a new key does not repair the old chain.

1. Stop the gateway.
2. Take a backup before touching anything: `deploy/backup-and-restore.sh backup`.
3. Record the last sequence number that is still trustworthy, from `use-log.head`.
4. Decide with whoever is accountable whether use from that point is billable. It is not a
   technical decision.

## If state is damaged

The gateway refuses work rather than guessing — an unreadable ceiling, use record, accounts file
or kill switch all mean "not taking work". That is by design and the refusal says which file.

```
deploy/backup-and-restore.sh check --from <latest backup>    # does the backup still restore
deploy/backup-and-restore.sh restore --from <latest backup>  # put it back
```

`restore` moves the existing state aside rather than deleting it, and refuses to report success
unless the metering chain verifies, the identity is intact, the devices came back attached to
their accounts, and the permissions are still owner-only.

## What a customer is told, and when

* **A refusal** always names itself and always names one thing they can do. That is enforced at
  the point a refusal is constructed, not by convention.
* **A suspension** shows them the operator's own words. If those words are not something you
  would be willing to say to them directly, do not type them.
* **An outage** is not something this software tells anybody about. There is no status page and
  no notification path, and until there is, telling people is a person's job.

## What is NOT in this process

* **Paging, rotas and escalation.** One person operates this. When that stops being true, this
  section is the one to write.
* **An external status page.** Nothing here is publicly reachable.
* **A forensic hold.** Retention sweeps by age; there is no "freeze everything" switch. If an
  incident needs the record preserved, take a backup — that is what it is for.

## Afterwards

Write down what happened while it is still annoying. The three questions worth answering:

1. What did the first person to notice actually see? If the answer is "a customer told us", the
   gap is in `watch`, not in the fix.
2. Which refusal did the customer get, and could they act on it?
3. Would the counter-check for the fix have failed before the fix? If not, the fix is not
   evidenced — it is just a change.
