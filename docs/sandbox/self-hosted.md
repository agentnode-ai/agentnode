# Running your own sandbox server

> **Status: design documentation, not instructions.**
> AgentNode cannot connect to a self-hosted sandbox today — no connector exists in the code. This
> page describes what such a server would have to guarantee, so the people who will operate one can
> review the design before it is built. **There are deliberately no installation commands here**,
> because inventing commands for software that does not exist is how documentation starts lying.

*For operators and platform teams.*

## What the machine has to be

A host that can create a sealed workspace per job, destroy it afterwards, and prove the properties
below to the client that sent the work.

## The properties a backend must demonstrate

These are the checks the conformance suite will run. It does not exist yet either; this is the list
it will implement.

| | what is checked |
|---|---|
| identity | not root inside the workspace |
| filesystem | root filesystem read-only; only the declared input mounted, read-only |
| privileges | all capabilities dropped; no new privileges; no host process namespace, no devices |
| the obvious hole | no container-runtime socket, and no other socket that reaches the host, is reachable |
| home | a clean, small, per-job home directory |
| limits | processor, memory, process count, disk and wall-clock actually enforced, not merely declared |
| network | off by default; only declared destinations reachable |
| workspace | the job cannot read or write outside what it was given |
| lifecycle | cancel and timeout work, and nothing survives afterwards |

Checks run from **inside and outside** the workspace wherever both views mean something. Inside
proves what the job sees; outside proves what the host actually configured.

## What a report may claim

Three words, and no others:

* **self-reported** — the backend asserts a property.
* **observed** — the suite measured it, in a signed report bound to a backend identity and version.
* **attested** — hardware attestation or an independent audit backs it.

**"Certified" is not available.** Without an independent certification the word would be a claim
nobody made, so it is not in the vocabulary at all.

A suite cannot prove an operator does not read data out of band, nor that a hypervisor boundary is
operated correctly. `observed` means what it says: these properties were measured. It does not mean
the operator is trustworthy — that stays a judgement you make about who runs the machine.

## Identity, versions, updates

A backend has an identity and a version, and a conformance report is bound to both. Changing either
invalidates the report: the client re-checks rather than trusting a result from a build that no
longer runs.

Consent is bound to the operator and backend identity too, so pointing AgentNode at a different
server asks again instead of silently reusing an agreement made about someone else's machine.

## Network and egress

Off by default. Where a job declares destinations, the gateway enforces them — and enforcement means
more than matching a hostname the client supplied. The resolved address, the name presented during
the encrypted handshake and the certificate all have to agree, and private, link-local and cloud
metadata addresses are refused outright.

Without that binding, a hostname allowlist can be walked around; see
[the security model](security-model.md). A gateway that only compares hostnames should be described
as convenience, not containment.

## Credentials

The intended shape: the job receives a reference or a meaningless placeholder, never a key. A broker
outside the workspace substitutes the real value only for destinations that were explicitly allowed,
and only where the traffic can actually be inspected in order to do the substitution.

Consequences an operator has to accept before switching that on: the broker terminates encryption for
those specific destinations, so it can read that traffic. It needs its own certificate authority per
tenant or per job, real key storage, and an audit trail. Where a request is authenticated by a
signature derived from the secret rather than by the secret itself, substitution is impossible and
the correct behaviour is to refuse rather than send something that cannot work.

## Limits, logs, retention, kill switch

* per-job ceilings on processor, memory, processes, disk and wall-clock, enforced by the backend;
* logs that carry content: kept as briefly as policy allows — the intended default is at most 24
  hours;
* audit metadata without content: 30 days;
* the workspace and any temporary credentials destroyed as soon as the result has been handed over;
* a global ceiling and a **kill switch**, so a runaway or abusive workload stops without needing a
  deployment.

## What is missing before this page can become instructions

1. the remote backend and its connector in the SDK;
2. the conformance suite;
3. the egress gateway with the binding described above;
4. the credential broker;
5. a real deployment that has run the suite and produced a signed `observed` report.

Until then this is a specification you can review and argue with, which is more useful than
instructions that would not work.

---

Next: [The security model](security-model.md) · [What actually works today](availability.md)
