# What this topology does not protect against

`single-host-development` is a closed development and test topology. These are the risks that
remain after everything in this directory has been applied correctly, kept here because they are
easiest to forget precisely when the deployment is working smoothly.

Reviewed independently as `ALPHA-ISOLATION-TRADE-0002` (PASS on all four criteria, three risks
retained). A pass there is a review of the decision, not a claim that these risks were removed.

## HIGH — the control plane and the worker share a kernel

Two accounts on one kernel are **not isolation**. The worker's account can drive a container
runtime, which is its whole purpose, and a sandbox escape therefore reaches this host — the host
that holds the control plane's pairing state, signing identity, every client's token and the
ledger. The account separation raises the cost of that; it does not make it impossible, and
nothing here should be read as saying it does.

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

## Not claimed anywhere

This arrangement is not production-ready, not multi-tenant, and not escape-proof between the
control plane and the worker. Neither is the separate-worker-host arrangement described below: its
two-machine measurement has never passed, so nothing on this page may be read as saying a second
machine has removed any of the risks above.

## What is not on this list

The things that were checked and hold on this deployment: neither account is in a root-equivalent
group, the worker cannot read the control plane's directory, the control plane cannot start a
container, the socket is reachable by one uid, and a ceiling was shown to bind before the worker
opened it. Those are properties of the deployment, verified there — not reasons any of the above
is smaller than it says.

## Moving the worker to its own machine

This topology is what it is until the worker is somewhere else. Getting it there was a change to
the product, not to this deployment, and that change has been written; it is a different
arrangement with its own install, not a setting in this one.

**What is implemented** (in the code, and tested on one host):

* a declared topology, `separate-worker-host`, that permits exactly one address class -- a
  remote address over mutual TLS (`remote-tcps`) -- and refuses a unix socket or an in-process
  worker under it, so there is no fallback to this arrangement
  ([`worker/topology.py`](../agentnode_sdk/worker/topology.py),
  [`tests/test_the_worker_may_be_on_another_host.py`](../tests/test_the_worker_may_be_on_another_host.py));
* the transport itself: `from_address` hands a `tcps://` address to `TlsWorker`, which speaks
  mutual TLS, checks that the worker is the identity the gateway expects (an impostor is never
  sent the job), can use one key per pair, and refuses rather than downgrades when the
  certificate settings are missing ([`worker/remote.py`](../agentnode_sdk/worker/remote.py),
  [`tests/test_mtls_transport.py`](../tests/test_mtls_transport.py),
  [`tests/test_one_key_per_pair.py`](../tests/test_one_key_per_pair.py));
* two roles that do not reach each other's start path, and one artefact per role with its own
  install, upgrade, rollback and diagnose scripts
  ([`tests/test_the_two_roles_are_separable.py`](../tests/test_the_two_roles_are_separable.py),
  [`separate-worker-host/README.md`](separate-worker-host/README.md)).

**What has not been shown.** The two-machine measurement has never passed: the procedure was run
on two real hosts and every attempt ended in BLOCK or WARN, the last acceptance run on rebuilt
machines blocking on five of its ten criteria. The separate-worker-host README says so and a test
holds that sentence in place. Until a run passes and is independently reviewed, a worker on its own
machine is a procedure, not a property -- the arrangement is still not production-ready and not
multi-tenant, and the privilege model above is re-assessed on the worker host rather than carried
across to it.

**What stays true here.** On this topology the gateway reaches its worker over a unix socket on
the same kernel, and everything above applies to it unchanged.
