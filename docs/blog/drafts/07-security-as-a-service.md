<!--
DRAFT — not published. Written against AgentNode SDK 0.24.0.
Availability claims come from docs/sandbox/availability.md, which is generated from the source.
Do not publish without re-running docs/sandbox/_checks/check_docs.py.
-->

# The AgentNode Sandbox: what we are planning, and what we have not decided

**Who this is for:** anyone weighing whether to wait for this or build their own.

This is a plan. It is not a product, it is not bookable, and it has no price. Everything below is
written in the future tense on purpose.

## The problem it solves

The local sandbox works and it asks something of you: install a container runtime, keep it running,
and be on a machine where you are allowed to. That excludes phones, locked-down work laptops, and
everyone who would rather not.

The managed sandbox moves the complicated part to us.

## The intended shape

**One sealed machine per job**, with its own kernel — not a shared box with walls drawn on it.
Destroyed when the job ends, never reused.

**Your keys never enter it.** The sandbox gets a placeholder; a broker outside substitutes the real
value only for destinations you allowed. A malicious package that copies its environment gets
nothing worth having.

**Your data has a stated life.** Workspace and temporary access destroyed as soon as you have the
result. Logs with content: at most 24 hours. Audit records without content: 30 days.

**Consent once, not constantly.** Before the first run: what is sent, where, how long it is kept,
what it may cost — in plain language, agreed once. Then it stops asking until something material
changes: a different region, a different operator, longer retention, changed terms.

**A free start with no card.** A small number of runs so the first thing you meet is not a payment
form.

**A spending ceiling you cannot accidentally exceed**, and a kill switch on our side.

## What we have not decided

Prices. Dates. Which regions exist at launch. Where free ends and paid begins.

We could write a number. It would be made up.

## What you can do now

If you want the protection today and you are on a computer, the local sandbox is free and works:
[set it up](../../sandbox/setup-local.md).

If this article describes what you actually want, the useful thing is to say which part matters —
the region, the key handling, the retention, or simply not installing anything. That is what decides
what gets built first.

**Look at the plan:** [The AgentNode Sandbox](../../sandbox/managed.md).
