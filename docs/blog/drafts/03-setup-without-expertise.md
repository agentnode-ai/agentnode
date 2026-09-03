<!--
DRAFT — not published. Written against AgentNode SDK 0.24.0.
Availability claims come from docs/sandbox/availability.md, which is generated from the source.
Do not publish without re-running docs/sandbox/_checks/check_docs.py.
-->

# Setting up a local sandbox without knowing what a container is

**Who this is for:** people who use a computer, not people who administer them.

You do not need to understand any of this to be protected by it. You need one program, once.

## What you are installing, in one sentence

A program that can create a sealed workspace — somewhere another program can run without seeing your
files.

That is the whole concept. The industry calls it a container runtime. You can call it "the thing
that makes the sealed workspace" and be exactly as correct.

## Before you start

```
agentnode sandbox doctor
```

It changes nothing. If it already says **Sandbox ready**, stop reading.

## Windows and macOS

Install **Docker Desktop** from docker.com. Start it. Wait for it to say its engine is running — the
first start is slow, that is normal.

Then, in AgentNode:

```
agentnode sandbox pull
agentnode sandbox doctor
```

You should see **Sandbox ready**. That is the whole setup.

## Linux

Install Docker or Podman from your distribution's own package manager, following your
distribution's instructions — they are better than anything an article could copy, and pasting
installation commands from the internet into a terminal is a habit worth not having.

Then the same two commands.

## When it does not work

Three things go wrong, and the doctor names which:

* **nothing installed** — install it;
* **installed but not running** — start it, and on Windows check that virtualization is enabled in
  the BIOS, because that failure looks identical from the outside;
* **image not downloaded** — `agentnode sandbox pull`.

Every message carries its own fix. If one does not, that is a bug in AgentNode, not in you.

## One honest note

Nobody outside the project has tried this yet. A ten-person test with people who have never used a
terminal is prepared and has not been run. So this article does not tell you it is easy — it tells
you what the steps are, and you can judge.

**Try it:** [The full setup page](../../sandbox/setup-local.md), including the Windows and WSL2 case
that catches people out.
