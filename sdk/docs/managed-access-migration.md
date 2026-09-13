# Moving to the managed access contract

This is for anyone already talking to a gateway over the older `/v1` addresses. Nothing you are
using has been removed or changed shape. What has happened is that a declared contract now exists
alongside it, every new capability is being added there, and two of the older routes are going to
move. This note says which, what changes when they do, and what to do about it.

## What is new

Every operation the contract declares lives under `/v1/op/<operation>`, is described by
`/v1/openapi.json`, and is available unchanged over MCP at `/v1/mcp`. All three end in the same
place: one server-side dispatcher that establishes who is asking, checks the device has not been
revoked, checks the capability, checks the parameters against the declaration, and only then
carries anything out. There is no second path through it and no adapter that can skip a step.

Contract protocol version: **1**. Each operation also carries its own `since`, so a client can ask
`capabilities` and find out exactly which operations this gateway has, rather than inferring it
from one service version number.

## What changed for callers this release

**`cancel` no longer holds you.**

Tearing a sandbox down and *confirming* it is gone is what makes a terminal state worth anything,
and it takes as long as it takes — up to the gateway's settle window of 45 seconds. The contract's
`cancel` used to do that inline, which meant the caller, and the person watching a spinner, waited
that long.

It now comes back immediately:

| field | meaning |
| --- | --- |
| `state` | `stopping`, or the finished state if the run had already ended |
| `accepted` | whether *this* call is what started the stopping |
| `cleanup_verified` | whether the sandbox has been confirmed gone; not yet known while stopping |

`stopping` is a **running** state, not a finished one. Poll `status` until `state` is one of
`finished`, `refused`, `cancelled`, `unverified`, `interrupted`. Cleanup is still a precondition
for reaching any of those — nothing here shortens the confirmation, it only moves who waits for it.
A cancellation that fails does **not** become `cancelled`.

Asking twice is safe: the second call joins the first and answers `accepted: false`. Cancelling
something that already finished is also not an error — it answers with the state it really has.

If you were reading the old return shape of the contract's `cancel`, the `state` field is still
there and still means the same thing. The change is that it can now say `stopping`, which is why
`capabilities` publishes the closed list of states rather than leaving you to discover them.

## What is going to move, and what it will cost you

Four older addresses still make their own decisions rather than going through the dispatcher. They
are listed in `agentnode_sdk/access/routes.py`, and a test compares that list against the request
handler's own source, so the list cannot quietly go stale. Each is still there for a reason, and
each reason is a client that has to move first.

### `/v1/jobs/<run>/cancel` — still waits, and still will until its clients move

This one is the reason this section exists. It calls the gateway's cancel inline, so **its callers
still wait up to the settle window**, which is exactly what the contract's cancel stopped doing.

It cannot simply be switched over, because its answer is the waiting:

- it returns **200** when the run stopped and **202** when the gateway would wait no longer;
- the SDK's `gateway.client.cancel` turns that into a `settled` flag;
- the CLI's `agentnode remote cancel` prints a different thing depending on it.

An asynchronous cancel can never truthfully answer 200, so migrating the server without migrating
those clients would turn "it stopped" into "it was asked to" with no change any of them could see.
That is the silent break this migration is meant to avoid.

**What to do now:** if you are writing anything new, use `/v1/op/cancel` and poll `status`. The old
route keeps working until its clients are moved deliberately, in a change that says so.

### `/v1/jobs/<run>` — the older status

Returns the whole signed run record. The contract's `status` deliberately returns a narrower
shape. Clients read fields the narrow shape does not carry, so moving this route means either
widening the declaration or breaking those readers. Use `/v1/op/status` for new work.

### `/v1/jobs` — the older submit

Carries `required_properties`, `mandatory` and `optional` policy shapes that the contract's
`submit` does not yet declare. Migrating it today would quietly *narrow* what you are allowed to
ask for — the requirements would be dropped rather than refused, which is worse than leaving it
where it is. The contract has to grow those fields first.

### `/v1/token/rotate` — replacing a credential

No contract operation covers it, and that is deliberate: an AI holding a device token should not be
able to mint its successor as one tool call. Credential management is not sandbox use.

## What will never move

`/v1/hello` and `/v1/pair` run **before** anybody has a credential — the first says which gateway
you have reached, the second is how you come to hold a token at all. The dispatcher's first act is
to establish who is asking, so these two cannot go through it by their nature. A pre-authentication
route is not a bypass of authentication; it is what authentication is built out of. Pairing keeps
its own single-use claim, its own expiry and its own attempt budget.

## What this is not

The contract's own `capabilities` carries these limits on every reader-facing surface, and they are
repeated here so nothing is learned only by reading code:

- the test topology is **single-host-development**. It is not multi-tenant, not production-safe and
  not escape-proof, and must not be described as any of those;
- confirmation that a sandbox is gone is a confirmation by this gateway, of this gateway's own
  records — it is not an external attestation;
- four addresses still decide for themselves. Until that number is zero, a change to the rules has
  to be made in more than one place, which is the risk one dispatcher exists to remove.

## A declared refusal that cannot currently happen

`device_revoked` is declared on every operation, and a client written against the contract would
reasonably branch on it. It is very nearly unreachable.

Withdrawing a device deletes its token entry, so the next request fails to identify at all and is
answered `not_authenticated`. The `device_revoked` check sits *after* identification and can only
fire in the narrow window where a token still resolves but its owner has changed.

This is not a security gap — access stops immediately either way, on every path, which is what
revocation has to guarantee and what `test_console_browser.py` checks. It is a documentation
defect: the contract offers a distinction the gateway does not actually make. Closing it means
either keeping revoked devices identifiable so the more specific refusal can be given, or removing
the refusal from the declaration. Both are changes to how identity is stored, so neither belongs in
a release that was only meant to add a page.

Until then, a client should treat `not_authenticated` as covering "this device was withdrawn".
