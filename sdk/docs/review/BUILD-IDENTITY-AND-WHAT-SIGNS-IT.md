# The build identity, and exactly how far it is proven

R5 asks for a build identity that is not a version number, and for it to appear wherever the
service identifies itself. This is what was built, and — separately — what it does and does not
establish. The second half is here because a reader would otherwise assume the first half covers
more than it does.

## What the version number was doing

`agentnode_sdk/__init__.py` said `0.24.1`. `pyproject.toml` said `0.25.0`. The wheel was therefore
built as 0.25.0, the gateway stamped `0.24.1` onto every answer it gave, and `agentnode --version`
printed `0.24.1`. Both numbers were written by hand, in two files, and they had already drifted.

That is on top of the original failure: 0.24.1 was installed before AND after the R2 deployment,
which changed the code. Anything comparing versions saw one build where there were two.

Both are fixed at the root rather than at the symptom. The version now exists once — in
`__init__.py` — and `pyproject.toml` builds the wheel's version from it via
`[tool.hatch.version]`. There is no second copy left to drift.

## What identifies a build now

`managed-<commit12>+<artefact12>`, computed by `runtime_pin.build_id()` from the commit and the
artefact digest. Computed, not written down beside them: the deployment hands the function the two
values it just checked, so the identity cannot say one thing while the pin says another.

It appears in:

| where | what carries it |
|---|---|
| every answer the gateway gives | the `gateway` stamp — `identity.as_dict()` |
| the signed conformance report | `ReportBinding.build_id`, with `commit` and `artefact_sha256` |
| `agentnode remote status --verbose` | a `build` line, beside the gateway id |
| the pin on disk | `/etc/agentnode/runtime-pin.json` |

An installation made before the pin existed reports an empty build id. It is left empty rather
than filled with a guess: a gateway that cannot tell which build it is should say so.

## What signs it, and what does not

**The signed conformance report is the authenticated statement.** `ReportBinding` carries the
build id, the commit and the artefact digest, and the report is signed over the whole binding.
A client that wants to know which build it is talking to verifies that report.

**The copy in the per-answer stamp is not signed.** The per-answer response binding covers the
gateway id and the version, not the build id, and adding a field to it would change a shape that
stored evidence and older readers recompute — an evidence reader whose `response_binding` lacks the
new field reports every newer answer as inconsistent. So the stamp's build id is a convenience for
a reader who already trusts the channel, and nothing more.

This is a real limit and it is named rather than implied: **if the only thing you have is an
answer, the build id in it is a claim. The proof is the signed report.**

## What was deliberately NOT changed

The gateway **fingerprint** stays `sha256(gateway_id + "\n" + version)`. It is what a paired client
pins when it is introduced to a gateway and re-checks on every later answer, so it has to mean
"the same gateway", not "the same code". Folding the build id in would make every deployment tell
every paired device that something else is answering on that address — true of the code, false of
the machine, and it is the machine a person paired with. `test_a_new_build_does_not_unpair_every_device`
holds that property so a later change to it has to be a deliberate one.

## The fingerprint: what it was, what it cost, and what it is now

**This section was written after the change was forced by a real failure, not before it.**

The original text below this document's earlier heading said the fingerprint stays
`sha256(gateway_id + "\n" + version)` because it has to mean "the same gateway", and that folding
the build id in would unpair every device on every deployment. That reasoning was right and the
conclusion was half-applied: **the version was already in there**, and the version is a property
of the code, not of the machine.

It stayed invisible for as long as it did because two different builds both called themselves
0.24.1 — the very defect this work exists to fix. The first deployment that genuinely changed the
version made it visible immediately: a client that had paired with the alpha refused to talk to
it, reporting that something else was answering on that address.

Measured, on the running machine:

```
the client had saved : e84fbe455dbc5157325f8493079554cc1a5cf90f1075d0dfdd954c72490dcb7f
the gateway then said: fe89106a23cbcda21a59a77679a7794b5f5c8859801ea9b8228a04e4bc75e254

sha256("44fe109457de794572cdfdc03ef281a8" + "\n" + "0.24.1") = e84fbe45...
sha256("44fe109457de794572cdfdc03ef281a8" + "\n" + "0.25.0") = fe89106a...
```

The client was right to refuse what it was told. It was told the wrong thing.

**The fingerprint is now `sha256(gateway_id)`.** One question, one answer: is this still the
gateway I paired with? Which BUILD is answering is a different question and has `build_id`.

**What it costs:** the deployment that introduces this unpairs every currently paired device,
once. Every device has to pair again. That is a real cost, it is paid deliberately, and it buys
the property that no later upgrade ever costs it again.

**And the reason it was hard to change:** the formula existed in FOUR places — the gateway, the
client that re-checks it, the evidence reader that recomputes it, and a test helper whose
docstring said it computed things "the way the gateway produces it". It is now one function,
`agentnode_sdk.gateway.identity.fingerprint_of`, and the other three call it.
