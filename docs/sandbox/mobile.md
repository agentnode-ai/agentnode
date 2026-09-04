# AgentNode on a phone or tablet

*For everyone. One minute.*

## The short answer

**Other people's code will never run on your phone.** Not today, and not later.

Phones cannot create the kind of sealed workspace this protection depends on. So instead of building
a weaker version and calling it a sandbox, the plan is that a phone sends the work somewhere that
can do it properly and shows you the result.

## Why there is no sealed workspace on this device

A phone or tablet cannot run the kind of sealed workspace this protection depends on, and there is
no remote one to send the work to yet. So AgentNode refuses, says so, and leaves you with something
you can do: nothing was started, nothing was changed, and the same command works on a computer where
a container runtime can be installed. If you need it here, saying so is what decides when the remote
option gets built.

## What works today

**Nothing yet.** There is no AgentNode mobile app, and no remote sandbox for one to talk to. A phone
has nowhere to send the work.

That is a plain statement of the current code, not a roadmap tease — see
[what actually works today](availability.md), where the phone row is marked as planned for exactly
this reason.

## What is planned

Two routes, both of which run the code somewhere else:

* **your own sandbox server** — you or your company run it, the phone connects to it;
* **the AgentNode Sandbox** — we run it.

Either way the phone is a screen and a keyboard. It picks the job, shows you what will be sent, and
displays the result. The code runs on the other machine.

Before the first time anything leaves the device you will be told, once and in plain language, what
is sent, where it goes, how long it is kept and what it may cost — and you have to agree. After that
it is remembered until something material changes.

Neither route exists yet. [What is planned for the managed sandbox](managed.md) ·
[Running your own](self-hosted.md)

---

Next: [Which sandbox suits you](choose.md)
