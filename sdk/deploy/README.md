# Two accounts, two services, one socket

This is the arrangement `ALPHA-BOUNDARY-0001` asked for, minus the second machine it actually
wanted. Read that first sentence again before deploying anything: **on one host this is not
isolation.** The worker's account can drive a container runtime, which where that means the
`docker` group is root-equivalent, so an escape out of a sandbox reaches the host — and the host
is where the control plane's secrets are. What the arrangement below buys is that the control
plane's *own account* cannot do that, and that moving the worker to another machine later is a
change to one address rather than a rewrite.

The topology these units produce is `single-host-development`. Every conformance report and every
job's binding carries that word. Nothing here may be described as production-safe, multi-tenant,
or escape-proof between the control plane and the worker.

## The accounts

```sh
# The account that runs foreign code. The only one in the docker group.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin agentnode-worker
sudo usermod -aG docker agentnode-worker

# The account that holds the secrets. In NO group that can reach a runtime.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin agentnode-gateway

# The one thing they share: the ability to speak to each other's socket.
sudo groupadd --system agentnode-bridge
sudo usermod -aG agentnode-bridge agentnode-gateway
sudo usermod -aG agentnode-bridge agentnode-worker
```

Check what you just made, because a group membership that is not there is a service that will not
start and one that is there by accident is the whole point undone:

```sh
id agentnode-gateway   # must NOT list docker
id agentnode-worker    # must list docker and agentnode-bridge
```

## The key

The gateway and the worker authenticate every message to each other. On one host this is belt and
braces — anything that is root here can read the key along with everything else — and it is here
because it is what carries over when the worker moves to a machine where that is not true.

```sh
sudo install -d -m 0750 -o root -g agentnode-bridge /etc/agentnode
sudo /opt/agentnode/venv/bin/agentnode worker key --at /etc/agentnode/worker.key
sudo chown agentnode-worker:agentnode-bridge /etc/agentnode/worker.key
sudo chmod 0640 /etc/agentnode/worker.key
```

## Telling the gateway where its worker is

This is the only place anything names where the worker is. Nothing else in the product names a
socket, a path, an account or a host.

**It does not follow that the worker can be moved to another machine by editing this.** The
address is handed to a transport that speaks unix sockets and refuses every other scheme, so a
worker elsewhere needs a transport this build does not have, and writing one is a change to the
product rather than to a deployment. What this arrangement establishes is that it would be the
only thing to add.

```jsonc
// /var/lib/agentnode/state/config.json
{
  "worker_address": "unix:///run/agentnode/worker.sock",
  "worker_key": "/etc/agentnode/worker.key"
}
```

When the second machine exists, that address becomes a network one and nothing else changes.

## The services

```sh
sudo cp agentnode-worker.service agentnode-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agentnode-worker agentnode-gateway
```

The worker starts first and the gateway waits for it. A gateway whose worker is not there does not
run jobs on the host instead: it tells the client that nobody established what happened, which is
the honest answer and the one that keeps a sandbox from quietly becoming a shell.

## What to check afterwards

```sh
# The socket exists, belongs to the worker, and the bridge group is the only other way in.
ls -l /run/agentnode/worker.sock          # srw-rw---- agentnode-worker agentnode-bridge

# The gateway's account cannot reach a runtime, by any route.
sudo -u agentnode-gateway docker ps       # must fail with a permission error

# The worker's account cannot read the control plane's secrets.
sudo -u agentnode-worker ls /var/lib/agentnode/state   # must fail
```

If the second of those succeeds, the gateway's account is in a group it must not be in, and the
arrangement is decorative. If the third succeeds, the worker can read every client's token and
this gateway's signing key, and it is worse than decorative.
