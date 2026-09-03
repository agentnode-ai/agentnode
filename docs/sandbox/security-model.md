# The security model, and where it stops

*For technical readers. The [two-minute version](understand.md) is the one to send to everyone else.*

## Two layers, and only one of them is a boundary

**The policy layer** decides *what should happen*: which trust tier may run where, whether a network
is allowed, whether a credential may be referenced. It is ordinary Python inside your process.

**The enforcement layer** makes it so: a container today, a separate machine later.

> **The Python layer is not a security boundary.** Anything running in the same process can reach
> module state, mutate it, or call the same functions. It orchestrates and it decides; it does not
> contain. Only the container — and, later, a separate process or machine — actually contains
> anything.

This is stated here rather than discovered later, because the distinction changes what a green test
means. A test proving the policy layer refuses something proves the *decision*, not the
*containment*.

It is also why the newer selection contract has **no host placement at all**: a capability check in
the same process cannot make host execution safe, so instead there is nothing to select.

## What the container actually does

Every sandboxed process is started with, at minimum:

`--rm` · `--read-only` · `--cap-drop=ALL` · `--security-opt=no-new-privileges` · `--user` (not root)
· `--pids-limit` · `--memory` · `--cpus` · `--network` · `--tmpfs` for a clean, small `HOME`

The image is pinned **by digest**, so it is one exact image and not whatever a tag points at today.
The exact digest and flag list are in [availability](availability.md), generated from the source.

**No container-runtime socket is ever mounted into a sandbox.** Mounting one would hand the
contained process control of the host, which defeats the entire exercise. There is no setting that
turns this on.

## Container or virtual machine

A container shares the host kernel. It is a real boundary against ordinary mistakes and ordinary
malice, and it is not a boundary against a kernel exploit.

A virtual machine brings its own kernel, which is why serious isolation of untrusted code usually
means a VM or a microVM. The managed service is planned around one machine per job for exactly that
reason. Today, on your own computer, what you get is a container — and the honest summary is: much
better than nothing, not equivalent to a separate machine.

## The network

**The default is no network at all.** A tool pack or an MCP server that declared nothing runs with
`--network none`: no name resolution, no connections, nothing.

**One exception exists and it is real.** Where an install sealed a list of destinations and consent
was recorded, the run joins an *internal* network — one with no route to your machine or to the
internet — and the only way out is a proxy that accepts exactly the sealed names. That is a
topological boundary rather than a setting the program could ignore: there is no route to walk out
of. Two run paths use it, the MCP runner and the tool-pack runner, and
[the availability table](availability.md) records the end-to-end test that runs the bypass matrix
from inside such a container.

If the machinery cannot be created, the run **raises rather than falling back to an open network**.

*A naming trap worth knowing:* the mode called `restricted` in the source restricts nothing — it
emits an ordinary bridge network. Nothing selects it, and the mode that does the work is the one
called `egress`. The generated table lists what each mode actually emits, because the names do not
say it.

**The limit worth knowing in advance:** the proxy allows a connection by the hostname the client
asked for, and it does check that the name resolves to a public address before connecting. What it
cannot do without opening the encrypted traffic is verify that the host on the other end is the one
the name promised. Techniques such as domain fronting can therefore reach a host that was not on the
list, and a permitted name that resolves to somewhere else takes the traffic with it. Terminating the encryption at the proxy is what
closes that, and doing so means the proxy can read the traffic — a real trade-off, planned for the
managed service where the certificate authority would be ours, and not something to switch on
casually.

## Secrets

**Read this part twice, because it is the one people get wrong.**

Today, when you allow a package to use one of your keys, **the key ends up inside the sandbox, and
the code in there can read it.** The name-only mechanism means the *name* travels — `--env
OPENAI_API_KEY` — and the container runtime supplies the value on the other side. That keeps the
value off the command line, out of this process's error messages and out of logs. It does not keep
it away from the program you are running. A package you gave a key to can copy that key.

What protects you is therefore not secrecy from the program; it is **who can get one, and where the
program can send it**:

* the package must be preinstalled and sealed — a package fetched at run time never gets a key;
* the names must match the ones consent was recorded for, or the run is refused;
* there must be a sealed, valid list of destinations, and the same run is restricted to it;
* pass-through is refused outright unless the network is the destination-limited one.

The refusal codes are extracted from the source into
[the facts file](_facts/code-facts.json) under `credentialed_run_refusals`, so this list can be
checked rather than believed. Every one of them refuses; none of them relaxes anything. And none of
this has been exercised end to end outside unit tests, which is why
[the availability table](availability.md) says available, not tested.

**The rule we are working towards** — that the process running other people's code should never
hold your key at all — needs the broker: the sandbox sees a meaningless placeholder and a proxy
swaps in the real value only for a destination that was allowed, so a package that copies its whole
environment gets a useless string. **That is planned, not built.** Until it exists, treat a key you
share with a package as a key that package has.

## <a id="the-old-setting"></a>The old setting

`sandbox.host_trust_policy` predates all of this. It decides which trust tiers may run *directly on
your machine*, and it still exists:

* `curated_only` — **what a fresh install uses.** Only AgentNode's own curated code runs on your
  machine; third-party code is sandboxed, or refused if that is not possible.
* `default` — more permissive: curated **and** trusted third-party code run directly on your
  machine. **This is not protected execution.** It is a compatibility mode from before the current
  rule, kept so upgrades do not break, and it is on its way out.
* `none` — everything is sandboxed, including curated code.

If you upgraded from an older version and had ever changed any setting, your existing value was
kept, which may well be `default`. Check with:

```
agentnode config get sandbox.host_trust_policy
```

Nothing selects `default` for you, no automatic path chooses it, and no screen describes it as safe.
It is an outdated compatibility setting.

## What this model does not claim

* It does not claim a container is a virtual machine.
* It does not claim the policy layer contains anything.
* It does not claim the credential broker exists — nothing substitutes a credential at a proxy.
* It does not claim the mode named `restricted` restricts anything. It does not.
* It does not claim the destination-limited network has been exercised anywhere but Linux CI.
* It does not claim any of this has been tested outside Linux continuous integration.
* It does not claim a non-technical person can set it up — that test has not been run.

---

Next: [What actually works today](availability.md) · [Administration and policy](admin-policy.md)
