<!--
DRAFT — not published. Written against AgentNode SDK 0.24.0.
Availability claims come from docs/sandbox/availability.md, which is generated from the source.
The availability statements are checked by docs/sandbox/_checks/check_docs.py.
-->

# From a phone to a Linux server: where your agent actually runs

**Who this is for:** anyone who wants to know what is running where, before they trust it.

"Runs on your machine" and "runs in the cloud" are marketing words. Here is the actual answer for
each kind of device, today.

## Laptop or desktop, with a container runtime installed

The agent runs **on your machine, in a sealed workspace**. Nothing leaves the computer. This is the
one that works today, and on Linux it is the one that is actually tested.

## Laptop or desktop, without one

Nothing runs. AgentNode refuses and tells you what is missing. That is not a bug report — it is the
design.

## A phone or tablet

Nothing runs on the phone, **ever**. Phones cannot create the sealed workspace this depends on, so
rather than shipping a weaker thing and calling it a sandbox, the plan is that the phone sends the
job somewhere that can do it properly.

**Today there is nowhere to send it.** No mobile client, no remote backend. A phone is not supported
at all, and saying so is more useful than a "coming soon" badge.

## A Linux server

The same path as a Linux desktop, and it is the one continuous integration exercises end to end — a
real MCP server started inside a real container, with the hardening flags checked on the actual
command line.

The table still marks the server row as untested, because a headless multi-user deployment has not
been exercised *as such*. That is the difference between "the code path is proven" and "your setup is
proven".

## Your own server, or ours

Both planned, neither built. The specifications are written and can be read; the connectors do not
exist.

## Why the tables are so pedantic

Because "supported" usually means "we think it works". [The availability
table](../../sandbox/availability.md) splits that into: the code path exists **and** something ran it
here; the code path exists and nobody has run it here; it exists but is unfinished; it does not
exist. Every row names the evidence.

**Look for yourself:** [What actually works today](../../sandbox/availability.md).
