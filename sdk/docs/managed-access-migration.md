# Moving to the managed access contract

For anyone already talking to a gateway over the older `/v1` addresses. Those addresses still
work and still answer in the shapes they always have. What changed is that none of them decides
anything any more, and that a job will not run until a person has agreed to it.

Protocol version: **2**.

## The one change that will stop an older client working

**Nothing runs that a person was not shown first.**

`prepare` describes exactly what a job would do and hands back a single-use proof that it was
shown. `submit` spends that proof. A submission without one is refused with `disclosure_required`,
and the answer says which call to make, in what order, and which client version knows how.

The gateway does **not** call `prepare` on a caller's behalf. A gateway that obtains the consent
it requires, on behalf of the party it is protecting the person from, has not obtained consent.
So an older client fails here, on purpose, with an answer it can act on. That is a break, and it
is not a silent one.

What to do:

```python
from agentnode_sdk.gateway import client as gc

shown = gc.prepare(connection, artifact, command=[...], network="none", wall_clock_s=60)
#  ... show `shown` to a person. Let them decide. ...
record = gc.submit(connection, artifact, accepted_disclosure=shown["accepted_disclosure"],
                   command=[...], network="none", wall_clock_s=60)
```

On the command line, `agentnode remote run` and `agentnode remote test` print what would happen
and ask. `--yes` says a person has read that and accepts it. With no terminal and no `--yes`,
nothing runs — there is nobody to ask.

### What the proof is bound to

Changing any of these between `prepare` and `submit` refuses the submission. They are recomputed
from the job in hand and compared; the approval is not a token that unlocks whatever comes next.

The account and the channel it was approved on · the connection it was approved **for** · where it
would run · the artifact digest, size and command · the network mode and destination allowlist ·
the resource limits and the ceilings in force · the requested policy digest · the operator policy
digest · which named secrets would be released · what it would add to your usage · how long the
approval lasts · a nonce.

Deliberately **not** bound: how much you have used so far. Those counters move on their own, and
binding them would invalidate every approval the moment anything else ran.

### Approving in one place for something that runs in another

This is the ordinary case, not an edge one. A person confirms in a browser; the AI they set up
runs the job over MCP afterwards.

`prepare` takes `execution_channel` and `execution_device`. They are shown to the person in
words — "Claude Code on my laptop, over MCP" — and bound. That exact connection may submit; REST
may not, the same device on another channel may not, another device on the same channel may not.
Leaving them out means "the connection I am using", which is most callers and needs no change.
Nominating somebody else's connection needs `manage_devices`.

## Cancelling no longer holds you

`/v1/jobs/<run>/cancel` answers **202** at once. 202 means what it always meant: asked for, not
confirmed stopped. **200 is gone** — only a route that waited could ever have said it, so nothing
changed meaning quietly; a value stopped being sent and the protocol version says so.

The waiting moved into the client, where it holds nobody but itself:
`gc.cancel(connection, run_id, settle=60)` and `agentnode remote cancel --wait`. What `settled`
means is unchanged — terminal, with the sandbox confirmed gone.

On the contract's own `cancel`, the run reports `stopping` until cleanup is confirmed. `stopping`
is a **running** state. Poll `status` until it is one of `finished`, `refused`, `cancelled`,
`unverified`, `interrupted`. A cancellation that fails does not become `cancelled`.

## Refusals

`device_revoked` was **removed**. `MANAGED-REVOCATION-0001` settled it: withdrawing a device
deletes the only record that could tell it from a credential that never existed, so the contract
was promising a distinction the gateway does not make. A withdrawn device, an ended session, an
expired credential and one this sandbox never issued are all `not_authenticated`. Treat that as
covering "this device was withdrawn". Access still stops immediately, on every path.

Two refusals were **added**: `disclosure_required` and `upgrade_required`.

## What every address is now

Every one of them reaches the dispatcher, runs before anybody has a credential, or hands back a
file. `agentnode_sdk/access/routes.py` is the register, and `test_routes_register.py` and
`test_one_way_in.py` compare it against the request handler's own source — including a check that
no route reaches anything on the service except what renders and signs an answer.

| address | what it is |
| --- | --- |
| `/v1/op/*`, `/v1/openapi.json`, `/v1/mcp` | the contract |
| `/v1/jobs`, `/v1/jobs/<run>`, `/v1/jobs/<run>/cancel`, `/v1/token/rotate` | translators |
| `/v1/hello`, `/v1/pair` | the only two reachable without a credential |
| `/console` | one file, no decisions |

A translator parses the older shape, hands the request to the dispatcher, and renders the answer
in the envelope its clients read. Signed requests stay signed — the proof goes to the dispatcher
rather than being checked at the door and the answer trusted — and signed answers stay signed,
because clients verify the binding and that is not decoration: an outcome could otherwise be
changed in transit and the binding recomputed over it.

`hello` and `pair` go through one bootstrap path, `dispatch.before_anyone()`, which refuses
anything that is not one of those two. Neither is a declared operation, so neither can appear in
any schema a model is handed — not by being filtered out, but by there being nothing to filter.
Both are recorded in the audit, because an account being probed looks like a run of failed
pairings and a log that kept only the successes would not show it.

`hello` answers anybody who can reach the port, so what it says is a written-down list
(`dispatch.WHAT_A_STRANGER_IS_TOLD`) rather than whatever the gateway happens to return. A field
added to the gateway's own view of itself does not become public by being added.

**An invitation is good for one attempt.** The claim is made before the code is compared, so a
wrong guess spends it and the person is told to ask for a new one. That makes guessing
structurally impossible rather than merely slow. It is a real trade — anybody who can reach the
port can burn an invitation an operator has opened — bounded by the attempt throttle and the
fifteen-minute window, and it costs an operator one button press where the alternative would cost
a credential.

## The contract can now say everything the older requests could

`submit` grew `job_id`, `required_properties`, `mandatory`, `optional`, `nonce`, and three claims
that are **checked rather than recomputed over**: `artifact_sha256`, `policy_sha256` and
`issued_at`. A request whose signature covers something other than what arrived is refused, not
quietly corrected. `network` grew `unrestricted`, which the older door could ask for and the
contract could not express.

`submit`, `status`, `result` and `cancel` can return `answer_binding` — the gateway's identity,
protocol, binding and signature — attached only to a caller that proved it holds the token's
secret, since nobody else could check it.

`devices.rotate` replaces `/v1/token/rotate`. It is declared for a person rather than a model, so
it reaches the dispatcher and is never offered as a tool: an AI handed a device's token must not
be able to mint its successor in one call.

## What this is not

- The topology is **single-host-development**: not multi-tenant, not production-safe, not
  escape-proof.
- Confirmation that a sandbox is gone is this gateway's confirmation, of its own records. It is
  not an external attestation.
- This sandbox does not classify what a job processes, has no per-job region or retention, and
  has no billing. The disclosure says so rather than staying silent.
