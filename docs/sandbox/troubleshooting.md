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

## "It is installed, but this account may not use it"

**In plain words.** The program that creates the sealed workspace is there and running, but your
user is not allowed to talk to it.

**What AgentNode prevented.** Nothing ran. This is a permission question, not a fault.

**What to do.**

* **Linux, the smaller ask:** use rootless Podman — it runs under your own account and needs no
  service owned by an administrator.

  ```
  sudo apt install podman        # or: sudo dnf install podman
  ```

* **Or ask whoever administers the machine**, and send them this, which changes nothing:

  ```
  agentnode sandbox doctor --json
  ```

**Deliberately not suggested:** adding yourself to the `docker` group. That is administrator-
equivalent power on the machine, permanently, and it is not a fix to take in passing.

---

## "It is running, but not in a mode that can run the sandbox"

**In plain words.** Docker Desktop can run Linux containers or Windows containers. The sealed
workspace is a Linux image, and the engine is currently set to the other one.

**What to do.** Switch Docker Desktop to Linux containers — right-click its tray icon. To see which
mode it is in:

```
docker info --format "{{.OSType}}"
```

---

## "This machine cannot hold the memory limit"

**In plain words.** The sandbox tells the runtime how much memory a program may use. On this
machine the runtime cannot account for swap, so that ceiling would not actually bind: a program
could quietly use more by swapping.

**What AgentNode prevented.** Running someone else's code under a limit that might not hold, which
is the same as running it under no limit. It refuses instead.

**What to do.** Most current Linux distributions already use cgroup v2, where the memory-and-swap
ceiling is accounted for by default. On an older cgroup v1 machine, swap accounting has to be turned
on in the kernel command line (`cgroup_enable=memory swapaccount=1`) and the machine restarted.
Either is an administrator's decision; send them the output of `agentnode sandbox doctor --json`.

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

## A program that will not stop

**In plain words.** A sandboxed program that runs past its time limit is ended — the program itself,
not just the command that started it. You do not have to clean anything up.

**Worth knowing, because it was not always true.** Until recently the time limit stopped the local
command while the program carried on inside its workspace. AgentNode said "timed out" and something
was still running. The conformance suite found that; it is fixed, and each run now removes its own
workspace and checks it is gone.

**If it cannot be shown to have stopped**, you get a different message from an ordinary timeout —
one saying the workspace could not be confirmed gone. That is deliberate: a stop nobody could verify
is not reported as a stop.

---

## It still will not work

```
agentnode sandbox doctor --json
```

prints the full check list without doing anything. That output, plus your operating system and how
you installed the runtime, is the useful thing to put in a report.

**Please do not paste** your configuration file or the contents of environment variables. They can
contain keys.
