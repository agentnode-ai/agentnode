# The worker account's privileges on the single-host development topology

A record of one decision and what was measured on the host where it applies. It describes; it
asks for nothing and directs nothing.

## What the decision is about

The managed sandbox alpha runs a control plane and a sandbox worker as two accounts on one
machine (`AgentNodeDevelopServer`, Fedora 44, systemd 259, docker 29.8 and podman 5.8.4 both
installed). The topology is labelled `single-host-development` in every record it produces. Two
accounts on one kernel are not a tenancy boundary and are not escape-proof between the two; the
architecture for live operation puts the worker on its own machine.

The question here is narrower: what privileges the account that runs foreign code holds, and what
systemd hardening its unit carries.

## The two shapes available

**Shape A — the worker in the `docker` group, `NoNewPrivileges=yes`.**
This is what the unit shipped with before this work. Membership of the `docker` group permits
starting a privileged container that bind-mounts the host root filesystem; on this host that is
equivalent to root. The account holding it is the one whose entire purpose is running code
submitted by clients.

**Shape B — rootless podman, no such group, `NoNewPrivileges=no`.**
The worker drives podman as itself with a subordinate uid range (`400000:65536`). Every container
it starts runs in a user namespace in which root inside the container maps to an unprivileged uid
outside. No account on the machine is in `docker`, `wheel`, `sudo`, `root` or `adm`, and the
deployment script exits non-zero if either ever is.

`NoNewPrivileges=yes` is not available in shape B. Rootless podman establishes its user namespace
through `/usr/bin/newuidmap`, which is setuid-root; under `NoNewPrivileges=yes` the kernel refuses
the `uid_map` write with `Operation not permitted` and no container starts. Measured on the host:
`newuidmap: write to uid_map failed: Operation not permitted`.

**What was chosen: shape B.**

## What that costs, stated plainly

Under shape B the worker account may execute setuid binaries. That is a real reduction against
shape A's `NoNewPrivileges=yes` and is not dismissed here. What it does not do is make the account
root: it may run `newuidmap`, `su`, `ping` and similar, none of which grant privilege without a
credential the account does not hold. Shape A's `docker` group grants host-root capability
directly and without a credential.

## Other settings that could not be applied, and why

Measured on the host rather than inferred:

* `ProtectHostname=yes` — crun fails with `sethostname: Operation not permitted`; a container
  runtime's function is creating namespaces, and this prevents the container's own UTS namespace.
* `RestrictNamespaces=yes` — same class; not applied.
* `ProtectHome=yes` — hides `/run/user`, which is where the account's session and therefore the
  cgroups that hold its ceilings live. `/home` and `/root` are closed by name instead
  (`InaccessiblePaths`), which was verified to refuse a directory listing.
* `Group=` the shared bridge group — `newuidmap` refuses when the process gid is not the account's
  passwd gid (`Target process is owned by a different user`). The worker keeps its own primary
  group; the shared group is supplementary, and the socket receives that group from its
  directory's setgid bit instead.

Retained: `ProtectSystem=strict` with three `ReadWritePaths`, `PrivateTmp`, `ProtectKernelTunables`,
`ProtectKernelModules`, `ProtectClock`, `ProtectProc=invisible`, `RestrictSUIDSGID`,
`RestrictRealtime`, `LockPersonality`, `SystemCallArchitectures=native`, and
`InaccessiblePaths=-/var/lib/agentnode` — the control plane's directory, holding the pairing state,
the signing identity, client tokens and the ledger.

## A property of shape B that had to be enforced rather than assumed

Rootless podman on this cgroup-v2 host reports that it can hold a memory ceiling, accepts
`--memory`, and does not apply it when the account has no systemd user session: it falls back to
the cgroupfs manager and the limit is dropped. Measured: an allocation of 256 MB inside a 64 MB
ceiling ran to completion and exited 0. With a session present (`loginctl enable-linger`), the
same allocation was stopped at 32 MB with exit 137, and a control run without a ceiling reached
256 MB.

Every check that *asks* reports correctly in the failing state: `check_available()` reports an
available runtime, and `memory_limit_enforceable` is derived for podman from the cgroup version,
which is v2 and accurate.

Consequently the worker performs a measurement before it opens its socket: it allocates past the
ceiling and requires that the ceiling be what stopped it — that the attempt began, did not
complete, and ended in a way attributable to the limit. A worker that cannot show this does not
listen, `None` ("cannot tell") refuses as `False` does, and no command-line flag waives it. The
measurement is `memory_ceiling_proof` in `conformance/runner.py`, the same function the
conformance suite uses, so the gate and the report cannot hold two definitions of "enforced".

## Observed state after deployment

Both services active. Socket `srw-rw---- agentnode-worker:agentnode-bridge`, its directory
`drwxr-s--- agentnode-worker:agentnode-bridge`, key `-rw-r----- root:agentnode-bridge`, gateway
state `drwx------ agentnode-gateway:agentnode-gateway`. The control-plane account could not start
a container; the worker account could not read the control plane's state; no container remained
after the runs.

## What this record does not establish

That two accounts on one kernel isolate the control plane from the worker. They do not, and
nothing above is offered as evidence that they do. It also does not establish anything about the
worker once it is moved to its own machine, where `NoNewPrivileges` and the group question would
both be posed again on a host that holds no control-plane material.
