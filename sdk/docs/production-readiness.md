# What stands between the closed alpha and a paid service

Read against the code on 2026-09-15, not from memory. Each row says what exists, what does not,
and who has to decide. "Exists" means there is code and a test; it does not mean it has been
operated.

The closed alpha on the development host is not a small version of the product. It is the product
with the operational half missing, and the missing half is what people pay for.

## Already there

| Thing | Where | What it actually does |
|---|---|---|
| Kill switch | `gateway/allowance.py` | a file whose presence refuses all work, with the operator's own words; an unreadable one counts as stopped |
| Ceilings | `gateway/allowance.py` | concurrent runs, runs per window, seconds per window, per client |
| Tamper-evident metering | `gateway/meter.py` | hash chain, Ed25519 signature, public half beside the log, `verify` reports the first broken link |
| Ledger | `gateway/ledger.py` | what ran, for whom, with what outcome |
| Operator policy | `gateway/operator_policy.py` | what the operator allows, composed with what a job asks for |
| Audit | `dispatch._audit` | every operation with device, channel, outcome — the basis of the compatibility claim |
| Consent | `access/dispatch.py` | nothing runs that was not disclosed and agreed, bound to the job and the connection |
| Isolation, measured | `gateway doctor --measure` | container, no-network, memory ceiling and verified cleanup, each measured rather than assumed |

The metering chain is honest about its own limit, in its own words: the gateway holds the key, so
a gateway that wanted to lie could write a chain that verifies perfectly. Making that worth
something to a paying customer needs a counter-signature from somewhere that is not the gateway.

## Not there

| Gap | State | What it needs |
|---|---|---|
| **Conformance bound to a policy version** | conformance records a backend and runtime version, **not** the operator policy it was measured under | make the measurement carry `operator_policy_sha256`, and refuse to present a measurement taken under a different policy as current. In scope, no gate. |
| **Abuse admission** | ceilings exist per client; there is no admission decision about *what* is being run, no reputation, no burst detection, no per-account cap | design, then build. In scope, no gate. |
| **Accounts** | there are devices and tokens; there is **no account**. Everything is per device. | an account that owns devices, carries ceilings and can be suspended as a unit. This is the prerequisite for billing and for abuse handling, and it is the largest single piece. |
| **Tamper-evident metering, externally** | chain + signature exist; the signer is the gateway | a counter-signature from a second party. Needs somewhere that is not this host. |
| **Billing interface** | nothing. No hooks, no invoice shape, no price anywhere | an interface that emits billable events, with **no provider chosen**. Choosing a provider and setting a price are founder decisions; the interface is not. |
| **Monitoring and alerting** | no health endpoint, no metrics, no alerting | a health/readiness endpoint that does not leak, metrics, and somewhere to send them. The "somewhere" may be a cost decision. |
| **Backup and restore** | ad-hoc tarballs made by hand this session | a documented, tested procedure with a restore that has been executed. The rollback rehearsal on the alpha is the pattern to follow. |
| **Deletion and retention** | nothing. `audit.jsonl` and the ledger grow without bound and nothing is ever deleted | a retention policy per record class, a deletion path, and a statement of what is kept and for how long. Legally relevant once there are customers. |
| **Log redaction** | the audit records device ids and operations, and the design already avoids payloads — but there is **no redaction pass and no test** that says a token or artifact can never reach a log | a redaction rule plus a test that plants credential-shaped values and asserts they never appear. A partial version of this already exists for refusals; it needs to cover the logs. |
| **Incident process** | nothing written | who is called, how the kill switch is used, how a customer is told, how a key is rotated. Paper, not code, and it should exist before customers. |
| **Control plane / worker separation** | **single host.** Two accounts on one kernel. The installer says so in those words. | a separate worker host. The architecture already assumes it: the worker is reached over a socket address that can become a network address. **Mandatory before public paid operation**; needs infrastructure, which is a founder decision. |

## Founder decisions in that list

These cannot be settled by building anything:

1. **A second host for the worker.** Required before public paid operation. Costs money.
2. **A billing provider and a price.** The interface can be built provider-agnostic; choosing is not mine.
3. **Somewhere to send metrics and alerts.** May be free, may not.
4. **Public reachability and a domain**, if ChatGPT-class clients are to be supported.

## Order that makes sense

Accounts first: abuse handling, billing and per-customer ceilings all hang off it, and building
them against devices would mean building them twice. Then conformance-to-policy binding and log
redaction, which are small and close a claim that is currently looser than it sounds. Then
retention and deletion, before there is customer data to be wrong about. Monitoring, backup and
the incident process alongside. The worker split last, because it is the one that needs a machine.
