# What this topology does not protect against

`single-host-development` is a closed development and test topology. These are the risks that
remain after everything in this directory has been applied correctly, kept here because they are
easiest to forget precisely when the deployment is working smoothly.

Reviewed independently as `ALPHA-ISOLATION-TRADE-0002` (PASS on all four criteria, three risks
retained). A pass there is a review of the decision, not a claim that these risks were removed.

## HIGH — the control plane and the worker share a kernel

Two accounts on one machine are not a tenancy boundary. A sandbox escape reaches this host, and
this host is where the control plane's pairing state, signing identity, client tokens and ledger
live. The account separation raises the cost of that; it does not make it impossible.

Nothing built on this topology may be described as production-ready, multi-tenant, or isolated
between control plane and worker. Before live operation the worker belongs on its own machine,
and the privilege model below is reassessed there rather than carried across.

## MEDIUM — the worker may execute setuid binaries

`NoNewPrivileges` cannot be set on the worker unit: rootless podman maps ids through
`newuidmap`, which is setuid-root, and the kernel refuses the `uid_map` write under it. The
alternative was the `docker` group, which is host-root outright, so this is the smaller exposure
by a wide margin — but it is an exposure, and it is retained deliberately rather than solved.

The user namespace reduces the consequence: root inside a container is an unprivileged uid
outside. It does not remove the surface.

## LOW — what was measured was measured here

The `NoNewPrivileges` incompatibility, the sysctl and namespace conflicts, and the memory ceiling
behaviour under a missing user session were all measured on one host, at one set of versions
(Fedora 44, systemd 259, podman 5.8.4, crun). They say nothing about a future dedicated worker
host.

That is partly self-correcting: the worker measures its own ceiling before it agrees to serve and
refuses if the ceiling does not hold, so a host where this is untrue fails closed rather than
quietly. The privilege questions have no such guard and have to be asked again.

## What is not on this list

The things that were checked and hold on this deployment: neither account is in a root-equivalent
group, the worker cannot read the control plane's directory, the control plane cannot start a
container, the socket is reachable by one uid, and a ceiling was shown to bind before the worker
opened it. Those are properties of the deployment, verified there — not reasons any of the above
is smaller than it says.
