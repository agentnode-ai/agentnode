<!--
DRAFT — not published. Written against AgentNode SDK 0.24.0.
Availability claims come from docs/sandbox/availability.md, which is generated from the source.
The availability statements are checked by docs/sandbox/_checks/check_docs.py.
-->

# Why AgentNode never quietly runs a stranger's code on your computer

**Who this is for:** anyone who has installed something an AI agent suggested, and wondered
afterwards what it could actually reach.

A package manager for AI capabilities has an uncomfortable job. You ask for something that reads
PDFs; somebody else's program arrives and runs. On your machine. With your files, your keys, your
network.

Most tools solve this with a prompt. "Allow this?" You say yes, because you asked for it, and the
prompt has told you nothing you did not already know.

AgentNode does something less convenient and more useful: it puts that program somewhere it cannot
reach your things, and **if it cannot do that, it does not run the program at all.**

## What "somewhere else" means

A sealed workspace on your own computer. Inside it, the program gets its own files read-only, a
scratch area that is deleted afterwards, no network at all unless its install sealed the sites it
needs and you agreed to them, and
none of your documents unless you picked them. It runs as a non-privileged user, on a filesystem it
cannot write to, with limits on memory and processes, and a call that will not finish is killed
after two minutes.

What it does not get: your home folder, your browser profile, the rest of your disk — apart from
one exception, which only happens if you agree to it.

With one exception worth stating plainly, because the alternative is a comforting sentence that is
not true: if you agree to give a program one of your API keys, that key goes into the workspace with
it and the program can read it. The workspace limits where it can then send the key — only the sites
the package declared at install time — and nothing is handed over without you agreeing first. But a
key you give to a program is a key that program has.

## The part people argue about

If the sealed workspace cannot be created, nothing runs.

Not "runs with a warning". Not "runs because you already said yes". Nothing.

That is the part that costs us. A tool that always works is easier to like than one that sometimes
says no. But a fallback that happens silently is the one that happens on the day it matters, and a
protection you lose exactly when it is inconvenient was never a protection.

So there is no such setting. There is no flag, no environment variable, no "advanced" checkbox that
turns a refusal into an execution. When AgentNode cannot run something safely it says so, in plain
words, with something you can actually do next.

## What that looks like in practice

```
agentnode sandbox doctor
```

changes nothing and tells you what is missing on your machine, with the one command that fixes it.
If a run is refused, the message says what was stopped and offers a way forward — not an error code
and a shrug.

## Where this is honest about itself

The protection you get today is a container: a genuine boundary against ordinary mistakes and
ordinary malice, not against someone with a kernel exploit. We do not call it a virtual machine,
because it is not one.

And it has only been tested on Linux. It is built not to care which operating system it is on, and
that expectation is not a result — so the table says "not tested here" for Windows and macOS rather
than claiming a green tick nobody earned.

**Try it:** [Set up the local sandbox](../../sandbox/setup-local.md) — free, six steps, one of which is a
download. How long that takes we have not measured on anyone but ourselves.
