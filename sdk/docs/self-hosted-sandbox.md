# Running your own sandbox

You have a Linux machine with Docker, and a laptop you actually work on. This puts a sandbox on the
first one so that code from the second can run somewhere that is not your laptop and cannot touch
your files.

Two roles, in order. Nothing here asks you to edit JSON, copy a digest, or read an address out of a
configuration file.

---

## On the Linux machine, once

### 1. Install it

```
pip install agentnode-sdk
```

You need Docker or Podman working for the user that will run the gateway:

```
docker run --rm hello-world
```

If that fails, fix it first. The gateway will not run anything without a container runtime, and it
will tell you so rather than falling back to something less safe.

### 2. Set it up

```
agentnode gateway init
```

This creates a directory the gateway owns — its identity, the access tokens of everyone who
connects, and its record of what has run. It is created readable by you alone, and the gateway
checks that this is still true every time it starts. If someone widens the permissions later, it
refuses to serve and tells you the command that fixes it.

### 3. Check what this machine can actually enforce

```
agentnode gateway doctor --measure
```

This runs the conformance suite against your runtime: it starts several short containers and
measures whether they are really isolated, really unprivileged, really limited, and really cleaned
up. It takes a minute or two.

**Until this passes, the gateway will refuse every job.** That is deliberate. A gateway that cannot
say what it enforces does not get to run other people's code and find out afterwards.

The measurement is tied to this machine, this version and this boot. After a reboot or an upgrade,
run it again — the gateway will ask you to.

### 4. Start it

```
agentnode gateway start
```

It listens on `127.0.0.1:8099`. That is the safe default: nothing else can reach it yet.

### 5. Let your laptop in

In another terminal:

```
agentnode gateway pair
```

It prints a code like `KQ7M-3PXA-9TDF`, good for fifteen minutes and for exactly one use. Read it
out or type it in. Do not paste it into a chat — anyone who sees it before you use it can take your
place.

### Day to day

```
agentnode gateway status                    # is it running, is it protecting anything
agentnode gateway clients                   # who is connected
agentnode gateway revoke --client <id>      # disconnect one, immediately
```

To stop it, press Ctrl-C in the terminal running `start`. Anything mid-flight is ended and cleaned
up; nothing is left behind.

---

## On your laptop

### 1. Install it

```
pip install agentnode-sdk
```

You do **not** need Docker here. The containers run on the other machine; that is the point.

### 2. Connect

```
agentnode remote connect http://127.0.0.1:8099 --code KQ7M-3PXA-9TDF
```

Use whatever address reaches the gateway. Your access is saved on this machine only, in a file
readable by you alone. It is never printed and never put in a URL.

### 3. Check it works

```
agentnode remote test
```

This sends a tiny program, runs it in a container on the other machine, and shows you what came
back. If it says *It works*, everything between the two machines is set up.

### 4. Run something

```
agentnode remote run ./script.py
```

By default the code has no network access at all. To let it reach specific hosts and nothing else:

```
agentnode remote run ./script.py --allow api.example.com
```

It gets no route to the internet — only a proxy that will connect to the hosts you named. Anything
else, including an address it makes up itself, fails.

Useful while it runs:

```
agentnode remote run ./slow.py --max-seconds 300   # how long the sandbox lets it run
agentnode remote cancel --run <id>                 # stop it from another terminal
agentnode remote status                            # is the sandbox reachable and protecting
agentnode remote rotate                            # replace your access, keep the connection
agentnode remote disconnect                        # forget it here
```

---

## Reaching it from somewhere else

Everything above assumes the two are on the same machine or the same private network, over
`http://`. **Plain HTTP works only to `127.0.0.1`.** Anything else is refused, because your pairing
code and your access token cross that link first and neither survives being read on the way. There
is no setting that changes this.

To use a gateway that is genuinely elsewhere, the connection has to be encrypted. In order of how
easy they are:

**A private tunnel.** Needs no domain name and no open port, which is why it is first — it works for
a machine at home behind a router. Install Tailscale on both machines, then on the gateway:

```
tailscale serve --bg 8099
```

That gives it an `https://` address on your own private network. Leave the gateway itself on
`127.0.0.1`; the tunnel does the encrypting. Connect to the address Tailscale prints.

**A reverse proxy you already run.** If you have Caddy or nginx with a certificate, put it in front
of the gateway and leave the gateway on `127.0.0.1`. Needs a domain pointed at the machine and port
443 reachable.

**A certificate of the gateway's own**, if you already have one:

```
agentnode gateway init --tls-cert /path/fullchain.pem --tls-key /path/privkey.pem
agentnode gateway start --host 0.0.0.0
```

`agentnode gateway doctor` tells you which of these applies to your machine and prints the exact
command.

---

## What this protects, and what it does not

Code you send runs in a container, as a user with no privileges, with no network unless you asked
for specific hosts, under limits on memory, processes and time, and it is removed afterwards — and
the gateway has *measured* each of those on your machine rather than assuming them.

What it does not protect against: someone who can write to the gateway's directory. They can forge
tokens, replace its identity, or clear its records. Keep that machine's accounts to people you
would trust with the sandbox itself.

This is a self-hosted beta. It has not been used by anyone outside the project, and the remote path
has been tested between two accounts on one machine rather than across a real network.
