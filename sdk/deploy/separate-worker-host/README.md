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

## The two artefacts

    python build_artefacts.py --wheel dist/agentnode_sdk-<v>-py3-none-any.whl

writes one per role into `../artefacts/`:

    agentnode-control-plane-<v>.tar.gz
    agentnode-worker-<v>.tar.gz

Each holds the wheel, that role's unit, that role's install, upgrade, rollback and diagnose
scripts, and a `MANIFEST.sha256` over all of it. The build FAILS if either one contains the
other's unit. Unpack the right one on each host, check the manifest, and run `./install.sh`.

THE WHEEL INSIDE BOTH IS THE SAME WHEEL, and `WHAT-THIS-IS.txt` in each artefact says so to
whoever unpacks it. There is one Python distribution containing both roles' modules, so a worker
host has the control plane's code on disk with nothing starting it. What keeps that from being a
quiet lie is the import graph, above: this role's start path reaches nothing of the other's, and
a new crossing fails the suite.

## Installing

**Inside an artefact the scripts are `install.sh`, `upgrade.sh`, `rollback.sh` and
`diagnose.sh`** — one of each, for that role. The longer names used elsewhere in this file are
what the same files are called in the repository. Running from either place works: the scripts
look their unit files up in both layouts instead of assuming one.

**There are four steps and they do not fit into one pass on either machine.** That is not
untidiness. The control plane is the CA, so the worker cannot be enrolled before it has run —
and a control plane must not start serving before it has measured its worker, which cannot
happen before the worker is up. Two phases on one side, three stops on the other:

```
1. control plane   ./install.sh            builds everything local, then stops
2. worker          ./install.sh   (x3)     stopping twice for the enrolment handover
3. worker          systemctl start agentnode-worker
4. control plane   ./install.sh --verify   reaches the worker, and only then starts
```

**1 — the control plane.** `AGENTNODE_DEPLOYMENT` is required; every identity is named inside a
deployment and there is no default.

```sh
sudo AGENTNODE_WHEEL=$PWD/wheel/agentnode_sdk-<v>-py3-none-any.whl \
     AGENTNODE_WORKER_ADDRESS=tcps://10.0.1.5:8443 \
     AGENTNODE_DEPLOYMENT=your-deployment-id \
     ./install.sh
```

It makes the account, the code, the CA, this gateway's identity, the pair key, its own time
floor, the configuration and the unit — and it **enables the gateway without starting it**. It
judges the worker's address as far as anything can before the worker exists: a literal private
address, a real port, not loopback and not a wildcard, not an address of this machine, and how
the kernel would route it. It cannot tell a correct address from a valid one belonging to some
other host on the same network; that needs the worker's identity, and step 4 is where it is
established. Said here rather than left to be assumed.

**2 — the worker host**, three passes: it stops for the one-shot enrolment secret, then for the
certificate, then finishes.

```sh
sudo AGENTNODE_WHEEL=$PWD/wheel/agentnode_sdk-<v>-py3-none-any.whl \
     AGENTNODE_LISTEN=tcps://10.0.1.5:8443 \
     AGENTNODE_DEPLOYMENT=your-deployment-id \
     ./install.sh
```

**4 — the control plane again.**

```sh
sudo ./install.sh --verify
```

Mutual TLS to the worker, the deployment and the worker identity this gateway expects, and only
then the start. Safely repeatable: it creates no identity and resets no floor. If it fails, the
gateway stays installed, enabled and stopped — the safe end of a failed verification rather
than a half-finished install.

### A run that stopped can be run again

Every step of both scripts checks before it acts, so an interrupted install is resumed by
running it again. That was claimed in this file before it was true: `pki floor init` was the one
step with no guard, and under `set -e` a partially completed install could never be resumed —
the only way forward was wiping the machine. It is guarded now, like the rest.

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
# ON THE WORKER HOST. Its own floor, keyed to THIS kernel's boot -- and NOT the issuer's run:
# `pki tick` reads the inventory and publishes the revocation list, neither of which exists
# here. Asking a worker for it used to be in this file, and on a real worker host it failed
# every time, which is how a worker ended up with no floor and refusing every connection.
sudo agentnode pki floor advance --role worker \
     --certificate /var/lib/agentnode-worker/tls/cert.pem \
     --anchor /etc/agentnode/trust/ca.pem

# ON THE CONTROL PLANE, where the issuer is:
sudo agentnode pki tick

# EITHER: the revocation list and the withdrawn-identity list, and whether they are fresher
# than their validity
ls -l /etc/agentnode/trust/
```
