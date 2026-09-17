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
