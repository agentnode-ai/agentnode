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

**What to do.** [Set up the local sandbox](setup-local.md) — one program, once. If you are not
allowed to install software on this machine, the remote options are the answer, and they do not
exist yet; [the availability table](availability.md) says so plainly rather than leaving you
guessing.

<details><summary>For engineers</summary>

The runtime probe searches `PATH` for `docker`, then `podman`, and refuses with a
`SandboxRequiredError` when neither is usable. There is no host fallback anywhere on this path.
`agentnode sandbox doctor --json` prints the structured check list and performs no action.
</details>

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

**What to do.** Start Docker Desktop and wait until it reports its engine running. On Linux, start
the service the way your distribution does. On Windows this message also appears when WSL2 or
Hyper-V is switched off, or when hardware virtualization is disabled in the BIOS — the doctor names
those cases in the same line, because they look identical from the outside.

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

**What to do.** Update AgentNode. Until then it refuses to run other people's code, which is the
correct behaviour and not a fault you can configure away.

---

## "No space left on device" while installing an MCP server

**In plain words.** The sealed workspace ran out of room while unpacking.

**What to do.** Update to 0.24.0 or later. This was a real defect — the package managers wrote their
caches into a deliberately tiny area — and it is fixed.

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
