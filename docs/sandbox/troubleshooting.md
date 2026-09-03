# When something does not work

*Every entry says: what you saw, what it means, what AgentNode stopped, and what to do next.*

Start here, always. It changes nothing and it diagnoses your actual machine:

```
agentnode sandbox doctor
```

---

## "No secure place to run this"

**In plain words.** AgentNode could not create the sealed workspace, so it did not start the
program.

**What AgentNode prevented.** Someone else's code was about to run on your computer with your files
in reach. It did not run. Nothing was executed and nothing was changed.

**What to do.** [Set up the local sandbox](setup-local.md) — one program, once.

If you may not install software on this machine, go to
[the next entry](#you-are-not-allowed-to-install-software-on-this-machine): there is something you
can do, and it is not waiting for a feature.

<details><summary>For engineers</summary>

The runtime probe searches `PATH` for `docker`, then `podman`, and refuses with a
`SandboxRequiredError` when neither is usable. There is no host fallback anywhere on this path.
`agentnode sandbox doctor --json` prints the structured check list and performs no action.
</details>

---

## You are not allowed to install software on this machine

**In plain words.** The sealed workspace needs one program installed, and you do not have the
rights to install it. That is a permission question, not a fault in AgentNode, and there is no
setting that removes the requirement.

**Where you stand right now.** Nothing ran. Nothing was downloaded, installed or changed, and
there is no half-finished state to clean up. Stopping here costs you nothing.

**1. Confirm that for yourself.** This command changes nothing:

```
agentnode sandbox doctor --json
```

**2. Ask the person who administers the machine, with the answer already in hand.** Send them
that output and this sentence:

> *AgentNode will not run third-party code without a container sandbox. It needs one of `podman`
> or `docker` installed on this machine. Podman is the smaller ask: it runs under my own account
> and needs no service running as an administrator.*

**3. What you can still do meanwhile.** Everything that does not execute somebody else's code
works unchanged — browsing the registry runs nothing and installs nothing:

```
agentnode search pdf
agentnode skill list
```

**4. If the answer is no.** Then AgentNode cannot run third-party code on this machine, today, at
all — and it will keep saying so rather than running it unprotected. The options that would
remove the requirement by moving the work elsewhere are described in
[choose where it runs](choose.md); [the availability table](availability.md) records that none of
them exists yet. On a machine you do control, [the local setup](setup-local.md) takes one
install. If you think this is wrong for your situation, say so at
[the issue tracker](https://github.com/agentnode-ai/agentnode/issues) — the case of "I cannot
install anything" is exactly the one the remote options are meant to answer, and how often it
comes up decides when they get built.

---

## "No Docker or Podman found"

**In plain words.** The program that creates the sealed workspace is not installed, or AgentNode
cannot see it from where it was started.

**What AgentNode prevented.** The same thing: nothing ran.

**What to do.** Install one — [setup](setup-local.md). If you are certain it *is* installed, you are
almost certainly hitting the WSL2 case: a runtime installed inside a Linux distribution is invisible
to an AgentNode started from Windows, and the other way round. Start both from the same side.

---

## "Found but its daemon is not reachable"

**In plain words.** It is installed but not running.

**What to do.**

* **Windows and macOS:** start Docker Desktop and wait until it reports its engine running.
* **Linux, systemd (most distributions):** `sudo systemctl start docker` — or
  `systemctl --user start podman.socket` if you use Podman.
* **Windows, and it still fails:** this same message appears when WSL2 or Hyper-V is switched off,
  or when hardware virtualization is disabled in the BIOS. Docker Desktop's own installer offers to
  switch the first two on; the third is a setting in your computer's firmware.

---

## "Pinned sandbox image is not present locally"

**In plain words.** The sealed workspace is built from one specific image, and it has not been
downloaded yet.

**What to do.**

```
agentnode sandbox pull
```

A one-off download.

---

## "This SDK build has no pinned sandbox image"

**In plain words.** You are running a build of AgentNode where the sandbox image was never
activated. Nothing you do locally fixes it.

**What to do.**

```
pip install --upgrade agentnode-sdk
agentnode sandbox doctor
```

Until you do, it refuses to run other people's code, which is the correct behaviour and not a fault
you can configure away.

---

## "No space left on device" while installing an MCP server

**In plain words.** The sealed workspace ran out of room while unpacking.

**What to do.**

```
pip install --upgrade agentnode-sdk
```

This was a real defect — the package managers wrote their caches into a deliberately tiny area —
and it is fixed in 0.24.0.

---

## Something that used to work now says it must be sandboxed

**In plain words.** You upgraded and the shipped setting changed. Third-party code that used to run
directly on your machine is now sandboxed, or refused if the sandbox is not set up.

**What AgentNode prevented.** Third-party code running unprotected on your machine because of a
setting from an older version.

**What to do.** [Set up the local sandbox](setup-local.md). The alternative — putting the old
setting back — is described in [the security model](security-model.md#the-old-setting), and it is
not protected execution.

---

## It still will not work

```
agentnode sandbox doctor --json
```

prints the full check list without doing anything. That output, plus your operating system and how
you installed the runtime, is the useful thing to put in a report.

**Please do not paste** your configuration file or the contents of environment variables. They can
contain keys.
