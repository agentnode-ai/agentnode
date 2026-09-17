# `alpha-runtime-pin-r1` asks for evidence and makes evidence impossible

## What happened

`ALPHA-RUNTIME-PIN-0002` returned BLOCK with all nine blocking criteria NOT_EVIDENCED. The first
sentence of its summary:

> All nine blocking criteria are NOT_EVIDENCED. Every manifest input is assigned role "context",
> which is background only and cannot substitute for reviewed evidence.

That is not a judgement about the work. It is a property of the profile.

## Why it is structural

The runner assigns roles from the PROFILE, never from the request — deliberately, so that a
request cannot declare its own material normative and choose the yardstick it is measured by:

```python
# Four trusted roles. An input the profile does not list defaults to the LOWEST authority,
# "context", so an unlisted file can never gain evidential or normative weight by accident.
VALID_ROLES = ("normative_spec", "frozen_evidence", "supporting_evidence", "context")
DEFAULT_ROLE = "context"
```

`alpha-runtime-pin-r1` has no `roles` key at all. Every other profile in this directory has one —
`alpha-r2-dataops-r5`, `managed-access-ci-job-r1`, and the rest. So under r1 every input is
context, context cannot be evidence, and nine criteria that ask for evidence cannot be met **by
any evidence set whatsoever**. There is no submission that passes it.

This is the same shape as `completion-gate` v1, where G1 and G4 were jointly unsatisfiable: any
evidence set satisfying one failed the other. The remedy then is the remedy now.

## What was done about it

`alpha-runtime-pin-r2`:

* `corrects: alpha-runtime-pin-r1`, with the reason recorded in the profile itself;
* the nine criteria **byte-identical** to r1's — verified by comparing the serialised arrays, not
  by reading them;
* one addition, the `roles` map, naming which files are normative specification, which are frozen
  evidence, which are supporting evidence, and which remain context.

Nothing in r1 is edited. `alpha-runtime-pin-r1.json` keeps its hash
`068ffb3ecc8ddd2ecdcb798a6a25c9a74e93aff68ad5c623d23cb65f1ac9d418`, and the BLOCK verdict produced
under it stays exactly as it is — including its substantive findings, which were separate from this
and were acted on rather than argued with.

## What this is NOT

It is not a weakening. Not one criterion changed, and the criteria are the hard part; the roles map
says which files the reviewer may treat as evidence, not what counts as passing. The prose in the
bundle is deliberately left at `context`, where it belongs: a brief explaining the work should
never be the thing that proves it.

It is also not a claim that the BLOCK was wrong. Four of its substantive findings were correct and
are fixed: the caller-supplied commit, the unpinned start, the missing per-mechanism
counter-checks, and the unevidenced cancellation.
