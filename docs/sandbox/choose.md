# Which sandbox suits you

*For everyone. One decision. Takes about three minutes.*

There are three ways to give other people's code a safe place to run. **Today, only the first one
exists** — the other two are described here because they are being built and because you should know
what is coming before you invest time in a setup.

## The short version

| | On this device | Your own server | AgentNode Sandbox |
|---|---|---|---|
| **Available?** | ✅ yes, today | 🔭 planned | 🔭 planned |
| Setup effort | install one program, once | you run a server | none |
| What it costs | nothing | your server | not decided yet |
| Your files leave your computer? | no | yes, to your server | yes, to ours |
| Works offline | yes | only on your network | no |
| Works on a phone | no | that is the point of it | that is the point of it |
| Protection | a locked container on your machine | whatever your server proves | a separate machine per job |

**Right now the honest recommendation is: if you are on a computer, set up the local sandbox. If you
are on a phone, wait — there is nothing to point you at yet, and we are not going to pretend
otherwise.**

## On this device — available today

A program called a container runtime creates the sealed workspace on your own machine. You install
it once; after that AgentNode uses it automatically.

**Good when:** you work on a laptop or desktop, you want nothing to leave your computer, and you are
willing to install one thing once.

**Not good when:** you are on a phone or tablet, or you are not allowed to install software.

→ [Set up the local sandbox](setup-local.md)

## Your own server — planned

Instead of your laptop doing the work, a machine you control does it. Useful for a team, for
underpowered devices, and for anyone whose rules say the work must stay on their own infrastructure.

**Status:** there is no connector for this in AgentNode yet. What exists is
[the operator documentation](self-hosted.md), written so the people who will run such a server can
review the design before it is built. Nothing in AgentNode can connect to one today.

## AgentNode Sandbox — planned

We run the sealed machines; you click and go. Nothing to install, works from a phone.

**Status:** not built, not priced, not bookable. [What is planned](managed.md) describes the intended
flow so you can see whether it fits, and says plainly what has not been decided.

## Still unsure?

Ask AgentNode:

```
agentnode sandbox doctor
```

It looks at *your* machine and tells you what it finds, what is missing, and the one command that
would fix it. It changes nothing.

---

Next: [Set up the local sandbox](setup-local.md) · [What actually works today](availability.md)
