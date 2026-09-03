<!--
DRAFT — not published. Written against AgentNode SDK 0.24.0.
Availability claims come from docs/sandbox/availability.md, which is generated from the source.
The availability statements are checked by docs/sandbox/_checks/check_docs.py.
-->

# Why your API keys do not belong in the sandbox

**Who this is for:** developers who have pasted a key into a config file and moved on.

The usual way to give a program an API key is an environment variable. Simple, universal, and it
means the program holds your key.

For a program you wrote, fine. For a program a stranger wrote, that is the entire attack: one line
that copies its own environment to a server, and your key is gone. No exploit, no clever trick. It
was handed over.

## The rule

**The process running someone else's code should never hold your key.**

Not "should hold it carefully". Should not hold it.

## What that looks like when it is built

The sandbox gets a meaningless placeholder. Your real key stays outside, held by a broker. When the
program makes a request to a destination you allowed, the broker swaps the placeholder for the real
value on the way out.

The program authenticates successfully and never sees the secret. If it copies its whole environment
somewhere, the attacker gets a placeholder.

## The trade-off, said out loud

For the broker to substitute a value inside a request, it has to be able to read that request — so
it terminates the encryption for those specific destinations. That is a real cost. It means the
broker sees that traffic, needs its own certificate authority, real key storage, and an audit trail.

Which is why the plan is to do it **only for destinations explicitly approved for a credential**,
never as a blanket interception of everything.

And where a request is signed with something derived from the secret rather than carrying the secret,
substitution cannot work. The correct behaviour there is to refuse, not to send something that will
fail confusingly.

## Where this stands today

**Planned.** Nothing in the current code substitutes a credential.

What does exist: an MCP server can be told the *name* of an environment variable rather than having
the value put on its command line — and even that path is marked inert in the source, with no live
caller. So today, in practice, nothing is passed.

The safe thing to do meanwhile is the boring thing: do not give keys to packages you have not read.

**Read the model:** [Secrets, and the limits](../../sandbox/security-model.md).
