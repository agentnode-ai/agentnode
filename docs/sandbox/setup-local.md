# Set up the local sandbox

*For everyone. Six steps, one of which is a download of a few hundred megabytes.*

> **How long it takes, honestly:** we do not know. Our own guess is roughly ten minutes on a normal
> connection, most of it waiting for that download — but nobody outside the project has been timed
> doing this, so treat that as our estimate and not as your experience. What is written below is
> what each step is and what you should see when it worked.

You install one program once. After that AgentNode finds it by itself and you never think about it
again.

> **Before you start:** run `agentnode sandbox doctor`. It changes nothing and tells you exactly
> what is missing on *your* machine. If it already says **Sandbox ready**, you are done — skip this
> page.

## Windows

**You should see:** an icon called Docker Desktop in your taskbar, showing "Engine running".

1. Download **Docker Desktop** from <https://docs.docker.com/get-docker/> and install it. AgentNode
   does not install it for you — a tool that silently installs system software is a tool you cannot
   audit.
2. Start Docker Desktop and wait until it says the engine is running. The first start takes a while.
3. Back in AgentNode:

   ```
   agentnode sandbox pull
   agentnode sandbox doctor
   ```

   **You should see:** `Sandbox ready — community packages run isolated.`

**If Docker Desktop will not start**, it is almost always one of three things, and the doctor names
which: Windows virtualization turned off in the BIOS, WSL2 not installed, or Hyper-V disabled.
Docker Desktop's own installer offers to fix the last two.

### Windows with WSL2

If you already work inside a WSL2 Linux distribution, you have a choice, and it matters:

* **Docker Desktop with WSL2 integration** — install as above, then switch on integration for your
  distribution in Docker Desktop's settings. AgentNode finds the runtime from both sides.
* **A runtime installed inside the distribution** — then run AgentNode inside that same
  distribution. AgentNode looks for the runtime on the `PATH` it can see, so a runtime installed in
  Linux is invisible to an AgentNode started from Windows, and the doctor will say it found nothing.

Either works. Mixing them is what causes the confusing case.

## macOS

**You should see:** the Docker whale in your menu bar.

1. Install **Docker Desktop** from <https://docs.docker.com/get-docker/>, or **Podman Desktop** from
   <https://podman-desktop.io/> — AgentNode accepts either.
2. Start it.
3. Then:

   ```
   agentnode sandbox pull
   agentnode sandbox doctor
   ```

## Linux

**Prefer Podman if you have the choice.** It runs without a background service owned by root, so
the setup below does not ask you to widen anything:

```
sudo apt install podman        # Debian, Ubuntu
sudo dnf install podman        # Fedora, RHEL
```

AgentNode finds Podman by itself and needs nothing further.

> ### If you use Docker instead, read this first
>
> To use Docker without `sudo`, the usual advice is to add yourself to the `docker` group. **That
> is equivalent to giving yourself administrator rights on the machine, permanently.** Anyone who
> can talk to the Docker service can start a container that has the whole disk mounted. It is not a
> convenience setting; it is a privilege change, and on a shared or work machine it may not be
> yours to make.
>
> If you accept that, your distribution documents the exact step.
>
> If you would rather not, use rootless Podman above. **Do not run AgentNode itself with `sudo`**
> as a way around this. That gives the whole application — and everything it installs and runs —
> administrator authority over the machine, which is a larger change than the one you were trying
> to avoid, and it is not what the sandbox is for.
>
> If neither is open to you, the decision belongs to whoever administers the machine:
> [what to do when you may not install software](troubleshooting.md#you-are-not-allowed-to-install-software-on-this-machine).

Then:

```
agentnode sandbox pull
agentnode sandbox doctor
```

## Checking it really works

```
agentnode sandbox doctor
```

**You should see** three ticks — runtime, daemon, image — and `Sandbox ready`. If any line has a
cross, that line carries its own fix.

`agentnode sandbox status` shows the same thing in short form, and
`agentnode sandbox doctor --json` prints it for scripts without doing anything.

## When something is wrong

Every failure the doctor reports comes with one concrete next step. The four you are most likely to
meet:

| What it says | What it means | What to do |
|---|---|---|
| no Docker or Podman found | nothing is installed, or it is not on the `PATH` this AgentNode can see | install one, or start AgentNode from the same place the runtime lives — see the WSL2 note above |
| found but its daemon is not reachable | it is installed but not running | start Docker Desktop; on Linux with systemd: `sudo systemctl start docker` (or `podman.socket`) |
| pinned sandbox image is not present | the sealed workspace image has not been downloaded | `agentnode sandbox pull` |
| this SDK build has no pinned sandbox image | you are on a build where the image was never activated | `pip install --upgrade agentnode-sdk` |

More cases, with explanations: [Troubleshooting](troubleshooting.md).

## Undoing it

AgentNode does not own any of this, so removing it is entirely in your hands:

* the downloaded sandbox image is an ordinary container image; your runtime's own tools remove it;
* Docker Desktop and Podman uninstall like any other application;
* `agentnode config set sandbox.host_trust_policy curated_only` puts the trust setting back to what
  a fresh install uses, if you changed it.

Removing the runtime does not break AgentNode. It goes back to refusing to run other people's code,
which is the safe state.

---

Next: [What the sandbox protects you from, and what it does not](security-model.md) ·
[Troubleshooting](troubleshooting.md)
