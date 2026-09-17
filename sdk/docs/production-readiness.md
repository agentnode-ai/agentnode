# What stands between the closed alpha and a paid service

Read against the code on 2026-09-15, not from memory. Each row says what exists, what does not,
and who has to decide. "Exists" means there is code and a test; it does not mean it has been
operated.

The closed alpha on the development host is not a small version of the product. It is the product
with the operational half missing, and the missing half is what people pay for.

**Second reading, after Managed Alpha R2.** Eleven of the fourteen gaps below were closed in that
milestone. What remains is three, and all three of them are decisions with money attached rather
than code nobody has written.

## Already there

| Thing | Where | What it actually does |
|---|---|---|
| Kill switch | `gateway/allowance.py` | a file whose presence refuses all work, with the operator's own words; an unreadable one counts as stopped |
| Ceilings | `gateway/allowance.py` | concurrent runs, runs per window, seconds per window — **per device and per account**, both applied, tighter one decides |
| Rate limits | `gateway/admission.py` | requests per minute, per device and per account, asked of **every** operation rather than only of work |
| Accounts | `gateway/accounts.py` | a customer that owns devices, carries ceilings, and can be suspended as a unit, with the operator's words attached |
| Admission | `gateway/admission.py` | one pre-execution decision with a closed list of reasons, each rendering as exactly one declared refusal |
| Tamper-evident metering | `gateway/meter.py` | hash chain, Ed25519 signature, public half beside the log, `verify` reports the first broken link; erasure by signed tombstone |
| Ledger | `gateway/ledger.py` | what ran, for whom, in which account, with what outcome |
| Operator policy | `gateway/operator_policy.py` | what the operator allows, composed with what a job asks for, narrowing only |
| Policy version | `gateway/policy_version.py` | an ordinal over policy digests, so a record names *which* of this gateway's policies rather than only a hash |
| Audit | `dispatch._audit` | every operation with device, **account**, channel, outcome — the basis of the compatibility claim |
| Consent | `access/dispatch.py` | nothing runs that was not disclosed and agreed, bound to the job and the connection |
| Isolation, measured | `gateway doctor --measure` | container, no-network, memory ceiling and verified cleanup, each measured rather than assumed |
| Redaction | `gateway/redaction.py` | a second line under the structural rules, run where text becomes a file or an answer; planted-secret tests search the output |
| Retention & deletion | `gateway/retention.py` | periods per persisting class, a sweep, deletion per account and per run, export |
| Monitoring | `gateway/observability.py` | provider-neutral sink with a local default, states, capacity, refusals by reason and by account, probes, unconfirmed cleanups, five alert rules |
| Backup & restore | `deploy/backup-and-restore.sh` | one script, two verbs, and a check that asks the four questions that would actually be wrong |
| Incident process | `docs/incident-process.md` | written, ordered, every step a command that exists |
| Billing interface | `gateway/billing.py` | billable events bound to account, policy version, worker and run; refuses to produce a statement from a record that does not verify. **No provider, no price.** |

The metering chain is honest about its own limit, in its own words: the gateway holds the key, so
a gateway that wanted to lie could write a chain that verifies perfectly. Making that worth
something to a paying customer needs a counter-signature from somewhere that is not the gateway.

## Closed in Managed Alpha R2

| Gap | How it was closed |
|---|---|
| **Accounts** | `gateway/accounts.py`. An account owns devices, carries ceilings, is suspendable as a unit. A device that predates this becomes its own account — the restrictive reading, verified on the running alpha: 19 devices came back as 19 accounts. |
| **Abuse admission** | `gateway/admission.py`. One decision before execution: account and device quotas, rate limits, concurrency, resource ceilings, suspension, the stop — each with a stable reason code and a remedy. It does **not** claim to detect intent, and a test asserts no reader-facing surface says it does. |
| **Conformance bound to a policy version** | `ReportBinding` already carried the operator policy digest; it now also carries the **ordinal**, so two reports can be told apart by a person rather than only by a hash. |
| **Log redaction** | `gateway/redaction.py`, run at refusal construction and at the audit line. Tests plant every secret shape this gateway issues and search everything it wrote. Identifiers — run ids, device ids, digests — are asserted to survive, because a redaction pass that eats those is one somebody turns off. |
| **Deletion and retention** | `gateway/retention.py`. Two persisting classes with operator-set periods, an idempotent sweep, deletion per account and per run, and an export. |
| **Monitoring and alerting** | `gateway/observability.py` and `agentnode gateway watch`. Provider-neutral; the local sink means a gateway has alerting before anybody buys a monitoring product. |
| **Backup and restore** | `deploy/backup-and-restore.sh`, and **executed**: on the running alpha, a copy of the live state was destroyed and restored, and came back with the same identity digest, a metering chain that verifies, 19 devices in 19 accounts and mode 700. The live service was never touched. |
| **Incident process** | `docs/incident-process.md`. |
| **Billing interface** | `gateway/billing.py`. Provider-agnostic, priceless by construction. |
| **Metered use is attributable** | Every line now names the account, the operator policy by digest and ordinal, the worker and the run. |
| **A refusal always names itself** | `Refused` refuses to be constructed without a remedy, and a job refused before it ran now comes back as a named refusal rather than as a successful answer whose `state` was `"refused"`. |

## Still not there

| Gap | State | What it needs |
|---|---|---|
| **Tamper-evident metering, externally** | chain + signature exist; the signer is the gateway | a counter-signature from a second party. Needs somewhere that is not this host — which is the same second machine as the row below, so it is not a separate decision. |
| **A price and a provider** | the interface emits billable events and prices nothing | **founder decisions.** The interface was the part that could be built without them. |
| **Control plane / worker separation** | **single host.** Two accounts on one kernel. The installer says so in those words. | a separate worker host. The architecture already assumes it: the worker is reached over a socket address that can become a network address. **Mandatory before public paid operation**; needs infrastructure, which costs money. |

## What R2 did not change, and must not be read as having changed

**Two customers' jobs still run on one kernel.** Everything in the tenancy work is about what one
account can *read, reach or withdraw through the service*. It says nothing about what one
account's running job could do to another's through the machine, and no amount of further work in
this repository can make it say that. The second host is what changes it.

## Founder decisions in that list

These cannot be settled by building anything:

1. **A second host for the worker.** Required before public paid operation. Costs money. It is
   also what would make the metering counter-signature possible, so the two travel together.
2. **A billing provider and a price.** The interface is built and provider-agnostic; choosing is
   not mine.
3. **Somewhere to send metrics and alerts.** The local sink means this is no longer blocking —
   there is alerting today. Choosing a provider may cost money.
4. **Public reachability and a domain**, if ChatGPT-class clients are to be supported. See
   `compatibility-matrix.md`. Recommendation: not before the worker split, because a publicly
   reachable endpoint is also a publicly reachable target.

## Order that now makes sense

The software order is finished. What is left is the machine and the money, in that order: the
second host first, because it is the one thing that changes what can honestly be claimed, and
because the counter-signature and public exposure both wait behind it.
