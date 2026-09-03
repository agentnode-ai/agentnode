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

Default is `none`. A sandboxed process gets no network unless a package declared where it needs to
reach and that was allowed.

There is a restricted mode in the source that routes traffic through a proxy on an isolated network.
**It is not finished** — the argv is built, the network and proxy are not created, and the source
says so. It is marked experimental in [availability](availability.md) and you should not plan
against it.

**The limit worth knowing in advance:** a proxy that allows connections by hostname decides from a
name the client supplied, without opening the encrypted traffic. Techniques such as domain fronting
can therefore reach a host that was not on the list. Terminating the encryption at the proxy is what
closes that, and doing so means the proxy can read the traffic — a real trade-off, planned for the
managed service where the certificate authority would be ours, and not something to switch on
casually.

## Secrets

The rule is: **the process running other people's code should never hold your key.**

Today, an MCP server can be given the *name* of an environment variable to receive, never the value
on the command line. Even that path is marked inert in the source — there is no live caller — so in
practice nothing is passed today.

The planned shape is a broker: the sandbox sees a meaningless placeholder, and a proxy swaps in the
real value only for a host that was explicitly allowed. The sandbox never holds the secret, so a
malicious package that copies its whole environment out gets a useless string. That is planned, not
built.

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
* It does not claim the restricted network or the credential broker work today.
* It does not claim any of this has been tested outside Linux continuous integration.
* It does not claim a non-technical person can set it up — that test has not been run.

---

Next: [What actually works today](availability.md) · [Administration and policy](admin-policy.md)
