<!--
DRAFT — not published. Written against AgentNode SDK 0.24.0.
Availability claims come from docs/sandbox/availability.md, which is generated from the source.
The availability statements are checked by docs/sandbox/_checks/check_docs.py.
-->

# Local, your own, or managed: which sandbox is yours?

**Who this is for:** anyone deciding how to run AI agents safely, for themselves or for a team.

Three ways to give someone else's code a safe place to run. Today **only the first exists**, and the
honest way to write this article is to say that up front rather than at the bottom.

## On your own computer — available today

You install one program once. After that AgentNode creates a sealed workspace on your machine
whenever it needs one.

**Choose this if** you work on a laptop or desktop, you want nothing to leave your machine, and you
can install software.

**Do not choose it if** you are on a phone, or your machine is locked down by an employer.

Cost: nothing. Offline: yes. Setup: one download.

## Your own server — planned

The sealed workspace lives on a machine you control. Your laptop stays a screen.

**This would suit** teams, regulated environments, and anyone with underpowered devices.

**Status: there is no connector for it in AgentNode.** What exists is a specification of what such a
server would have to prove — not root inside the workspace, read-only filesystem, no path to the
host, enforced limits, network off by default — so that the people who will operate one can argue
with the design before it is built. [Read it](../../sandbox/self-hosted.md).

## The AgentNode Sandbox — planned

We run the machines. You click, pick files, go. Works from a phone.

**Status: not built, not priced, not bookable.** The [intended flow](../../sandbox/managed.md) is
written down; the prices are not, because we would have to invent them.

## The one that is real

If you are on a computer and you want this today, it is the first one.

**Try it:** [Set up the local sandbox](../../sandbox/setup-local.md).
**Planned instead?** [See the architecture](../../sandbox/self-hosted.md) — worth reading before you
build around it.
