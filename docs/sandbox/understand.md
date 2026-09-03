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
* **no network at all** — unless the package declared, when you installed it, exactly which sites it
  needs and you agreed to them; then it can reach those and nothing else;
* **none of your files**, unless you picked them;
* **none of your passwords or keys** — with one exception, below.

It does not get your home folder, your browser profile, your login credentials, or the rest of your
disk — apart from the one exception below, which only happens if you agree to it.

**The one exception, and it matters.** If a program asks for one of your keys and *you agree to give
it that key*, the key goes into the sealed workspace with it, and the program can read it. Nothing
hides it from a program you handed it to. What the workspace still does is limit where that program
can send it: only the sites it declared when you installed it. Nothing here happens without you
agreeing first, and the honest way to think about it is the ordinary one — **a key you give to a
program is a key that program has**. Give it its own key rather than one that opens everything.
[The detail is in the security model](security-model.md).

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
