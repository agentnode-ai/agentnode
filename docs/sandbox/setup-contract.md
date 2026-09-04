# The setup flow, as a product contract

> **Status: specification.** This describes what the setup experience must do when it is built. The
> pieces marked *planned* have no button and no connector today. It is written down here so it is a
> commitment that can be reviewed, rather than an intention that gets lost.

*For whoever implements the interface, and for anyone who wants to hold us to it.*

## The one principle

> **No unsafe execution — but for every solvable problem, an immediate simple safe way out.**

"Sandbox or refusal" must never reach a person as *"Docker is missing. The end."* It must reach them
as *"Secure execution is not available on this device. Shall AgentNode use the AgentNode Sandbox
instead?"*

## The flow

1. **AgentNode checks the device itself.** No question is asked that the software can answer by
   looking. Today: probe for a container runtime, its daemon, and the pinned image — exactly what
   `agentnode sandbox doctor` already does.

2. **One safe available option is recommended.** The recommendation is *computed for this device*,
   not fixed: on a machine with a working runtime the recommendation is local, because it is free
   and the work stays on the device. Recommending a paid remote service to someone who already has a
   working local sandbox would be selling, not helping.

3. **Local prerequisites can be set up or checked from one clearly named action.** *(Planned as a
   button; today this is the doctor plus the [setup page](setup-local.md).)* AgentNode never
   installs system software silently — the action explains what will be installed and links to the
   vendor.

4. **If nothing local is safely available, a remote option is offered.** *(Planned — no connector
   exists.)* Offered, not chosen.

5. **Before the first remote run**, region, data transfer, retention and possible cost are explained
   once, in plain language, and agreed once. *(Planned.)*

6. **After that, ordinary runs do not ask again** — until something material changes, when the
   agreement is asked for again rather than assumed.

7. **Every error leads to a safe solution.** Never to host execution.

## Rules the interface must not break

* **Automatic selection only ever chooses among *protected* places, and never sends anything away
  by itself.** The software may pick between protected options on the device without asking, and it
  may recommend a remote one — but work only leaves the device after the person has agreed to that,
  once, knowing where it goes. Unprotected execution on your own machine is not one of the options
  it can pick: it is not in the selection at all, so no automatic path can arrive at it.
* **There is no "run it unprotected anyway" control** on the normal path. The old compatibility
  setting exists in advanced settings, is never offered as a solution to a problem, and is never
  described as safe.
* **Every refusal carries at least one thing the person can actually do.** A link that only explains
  is not a way out. If nothing can be done, the screen still offers a clean stop and a way to reach a
  human.
* **Technical words stay out of the ordinary path.** Container, runtime, image digest, egress,
  attestation, microVM — these belong in advanced settings and in this documentation, not in the
  first three screens.
* **A screen that reports success must say where the work ran**, so nobody has to guess whether they
  were protected.

## What counts as a step

A step is a screen where the person must **decide something or provide something**. Progress screens
and success screens are not steps. The managed setup must need **at most three** steps under that
definition.

## How this gets checked

By ten people who did not build it, against a fixed protocol: one read-aloud sentence, five tasks,
no help, and two questions at the end — where did the code run, and did they believe a stranger's
code ran unprotected on their computer. Eight of ten must finish unaided, and **a single "yes" to
the second question fails the whole run**.

The pack is ready — `docs/em3/usability/` on the EM-3A branch, which is still under review and not
merged, so it is deliberately not linked from here. **The sessions have not happened.** Until they do, no page in this documentation claims the setup is easy — the pages say
what the steps are and let the reader judge.

---

Next: [Which sandbox suits you](choose.md) · [What actually works today](availability.md)
