# Two machines: a control plane and a worker

The topology is called `separate-worker-host` and it is the one the architecture asked for. The
sibling directory above this one (`deploy/`) is `single-host-development`: two accounts on one
kernel, which is not isolation and says so. This directory is what replaces it.

**Nothing here has been run across two machines.** It is written from the code, and every part of
it that can exist on one host has been exercised there. The measurement that would make this
directory a claim rather than a procedure — a gateway and a worker on two real kernels — has not
happened, and no file in this repository may be read as saying it has.

## Which artefact goes where

There is one wheel and two roles. `agentnode_sdk/roles.py` is the manifest: it names the modules
each role needs and the ones it must never reach, and `tests/test_the_two_roles_are_separable.py`
walks the import graph and fails if the worker's start path reaches the control plane's. That
test is the reason this section is a fact and not an intention. What is NOT yet true is two
wheels; the roles are separable, they are not separately packaged, and until they are, a worker
host has the control-plane code on disk even though nothing starts it.

| | control plane | worker host |
|---|---|---|
| account | `agentnode-gateway` | `agentnode-worker` |
| unit | `control-plane.service` | `worker-host.service` |
| state | `/var/lib/agentnode` 0700 | `/var/lib/agentnode-worker` 0700 |
| holds | CA, signing identity, tokens, accounts, ledger, queue | its own certificate, the signed lists, one pair key, its journal |
| container runtime | none, and its account may not reach one | rootless podman, as itself |
| listens on | the client port, deliberately opened | the private address only |

## Installing

On the control plane first, because it is the CA and the worker cannot be enrolled before one
exists:

```sh
sudo AGENTNODE_WHEEL=/tmp/agentnode_sdk-<v>.whl \
     AGENTNODE_WORKER_ADDRESS=tcps://10.0.1.5:8443 \
     ./install-control-plane.sh
```

Then, on the worker host:

```sh
sudo AGENTNODE_WHEEL=/tmp/agentnode_sdk-<v>.whl \
     AGENTNODE_LISTEN=tcps://10.0.1.5:8443 \
     ./install-worker-host.sh
```

Neither script opens a port. `AGENTNODE_LISTEN` must be a literal private address: the worker
refuses a wildcard bind in either topology, because a worker that binds every interface on a
cloud host is reachable from the internet. Both scripts stop before starting anything if
`agentnode worker preflight` refuses, and preflight is also what the units run — the same
checks, in the same order, whether a person or systemd asks.

The enrolment steps between the two, and the deletion of the residues enrolment leaves behind,
are in `install-worker-host.sh` where they have to be performed. Read it before running it.

## Upgrading

`./upgrade-one-host.sh <wheel>` upgrades THE HOST IT IS RUN ON and nothing else. The two hosts
are deliberately not in lockstep: a security update that cannot be applied to one without the
other is a worse failure than the skew it avoids. What is checked instead is the wire version, on
every connection, before any work crosses — `describe` reports each side's tested range, and no
common version is a refusal that names both ranges and both builds rather than a downgrade.

Upgrade the worker first when the change is to execution, because a gateway that refuses work is
visible and a worker running the wrong code is not.

## Rolling back

`./rollback-one-host.sh` restores the previous wheel on the host it runs on. The order across the
pair is the decision's and is not negotiable:

1. stop admission on the control plane (`agentnode gateway stop --reason "rolling back"`),
   which also ends the runs that were going — the reason to stop at once is usually the code
   running right now,
2. roll the WORKER back, so incompatible execution code is gone while the gateway is fail-closed,
3. roll the control plane back,
4. re-measure before reopening.

A rollback of either host leaves the other's records intact. The worker's journal survives, so a
run that was in flight across the rollback is still answerable afterwards instead of being lost;
the lease does not survive, which is correct — the gateway takes a new one, with a higher epoch,
and anything issued under the old one stops counting.

## Diagnosing

`./diagnose.sh` prints what the two ends can see about each other and nothing else. It prints no
key, no token and no certificate private material; a refusal is shown as its stable cause and the
identity URI that was presented, which is a name rather than a secret. Run it on either host.

The two things worth checking first when nothing works, in this order, because they are the two
that fail closed and are easy to forget:

```sh
sudo agentnode pki tick          # ON THE WORKER HOST. The floor is keyed to THIS kernel's boot.
ls -l /etc/agentnode/trust/      # the revocation list and the withdrawn-identity list, and
                                 # whether they are fresher than their validity
```
