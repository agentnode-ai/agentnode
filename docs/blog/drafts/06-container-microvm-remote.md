<!--
DRAFT — not published. Written against AgentNode SDK 0.24.0.
Availability claims come from docs/sandbox/availability.md, which is generated from the source.
The availability statements are checked by docs/sandbox/_checks/check_docs.py.
-->

# Container, microVM, remote sandbox: what the words mean

**Who this is for:** technical readers who want the distinctions without the vendor gloss.

## Container

One kernel, many isolated processes. The kernel keeps them apart using features it already has.

**Good at:** stopping a program from reading your files, opening your network, or becoming root.
Fast, cheap, no separate machine.

**Not good at:** stopping someone who has a working kernel exploit. They are in the same kernel as
you.

That is what AgentNode uses today: pinned by digest, read-only root filesystem, all capabilities
dropped, no new privileges, non-root user, limits on memory and processes, no network by
default, and never a mounted runtime socket — mounting one would hand over the host and end the
discussion.

## Virtual machine, and microVM

Its own kernel, on virtualized hardware. A microVM is the same idea stripped down to boot in
milliseconds instead of a minute.

**Good at:** the case containers are not good at. Two customers' jobs do not share a kernel.

**Costs:** more memory, more startup, more infrastructure.

This is what the managed service is planned around: one machine per job. Note the direction of the
argument — running *untrusted* code is exactly the case where the extra kernel earns its cost, and
untrusted code is AgentNode's normal case rather than an edge one.

## Remote sandbox

Orthogonal to both. It says *whose machine*, not *what kind of boundary*. A remote sandbox can be a
container on a server or a microVM in a data centre; the useful question is what it can prove.

Hence three words, used carefully:

* **self-reported** — the backend says so;
* **observed** — a conformance suite measured it, signed, bound to a backend identity and version;
* **attested** — hardware attestation or an independent audit backs it.

Those three are the whole list. There is no fourth, and in particular nothing asserting an outside
body has examined anything — because none has.

## What to take away

A container is a real boundary and not the strongest one. A microVM is stronger and more expensive.
Remote says where, not how strong. And a claim about any of them is worth exactly as much as the
evidence attached to it.

**The details:** [The security model, and where it stops](../../sandbox/security-model.md).
