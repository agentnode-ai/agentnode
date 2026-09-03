# The sandbox in two minutes

*For everyone. No prior knowledge assumed. Takes about two minutes to read.*

## What is being protected

Your computer, and the files on it.

AgentNode installs and runs programs that **other people wrote** — tool packs, MCP servers, agents.
Most of them are fine. You cannot check that yourself, and neither can we, so AgentNode assumes the
opposite and puts that code somewhere it can do its job without being able to reach your things.

## Where other people's code runs

In a separate, sealed workspace — not next to your documents.

Inside that workspace the program gets only what it needs:

* a copy of its own files, which it **cannot change**;
* a small scratch area that is **thrown away** when it finishes;
* **no network at all**, unless the package declared where it needs to connect and you allowed it;
* **none of your files**, unless you picked them;
* **none of your passwords or keys**.

It does not get your home folder, your browser profile, your SSH keys, or the rest of your disk.

## "Sandbox or refusal"

If that sealed workspace cannot be created, AgentNode **does not run the program**.

It does not fall back to running it normally "just this once". It stops and tells you what is
missing. That is the whole rule, and it is the reason the rest of this documentation exists: an
agent that quietly runs anyway is not safer than no agent at all, it is only quieter about it.

You will see this as a message, not a crash — and it always comes with something you can do next.

## Why there is no quiet fallback

Because a fallback that happens silently is the one that happens when it matters.

The moment a product decides "this is important enough to run anyway", the protection is worth
whatever the least careful moment decides. So AgentNode has no such moment. When it cannot run
something safely, it says so and offers you a way forward that is still safe.

There **is** an old setting, from before this rule existed, that lets certain code run directly on
your computer. It is not part of the protected path, nothing switches it on for you, and no screen
calls it safe. See [the security model](security-model.md#the-old-setting) if you have it on.

## What this costs you

Time, mostly: something has to create that sealed workspace, and on your own computer that means
installing one extra program once. [Which sandbox suits you](choose.md) walks through the options,
including the ones that need nothing installed.

---

Next: [Which sandbox suits you](choose.md) · [What actually works today](availability.md)
