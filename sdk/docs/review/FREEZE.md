# The yardstick, frozen before the work it measures

**Profile:** `managed-console-final-r1`
**SHA-256:** `0068b9ac2346790717cf1b22848e54dc8d9fc3f22281212d993680604200ffe9`
**Size:** 13 988 bytes
**Frozen:** in the commit that added this file, which contains no code.

## Why this file exists

Criteria written after an implementation are criteria the implementation cannot fail. You can
always find a way to describe what you happened to build such that what you built satisfies it,
and the describing feels like reviewing. The only defence is ordering: decide what would count as
correct before knowing what you are going to write.

So this is frozen *prospectively*. The commit that adds this file changes no code. Everything the
profile judges is written afterwards, in later commits. That ordering is checkable in git rather
than asserted here — `git log --follow sdk/docs/review/FREEZE.md` against the log of anything the
profile names will show which came first.

`.claude/` is gitignored, so the live profile the reviewer loads is not itself in the repository.
That is why a byte-identical copy sits beside this file. At review time the two must still match:

```
sha256sum sdk/docs/review/managed-console-final-r1.json
sha256sum .claude/codex-review/profiles/managed-console-final-r1.json
```

Both must equal the digest above. If they differ, the profile was edited after the freeze and the
review that ran under it is worth nothing — rerun it under the frozen text, or say plainly that
the yardstick moved.

## What it contains

Fourteen blocking criteria.

**A1–A7 are carried over unchanged.** They are the seven properties agreed when the console was
still a prototype, and the review that was to run under them was never performed. They are not
discarded and not weakened — they are absorbed into the larger check:

| | |
| --- | --- |
| A1 | no auth or policy bypass by an adapter or a legacy path |
| A2 | cancel blocks no client |
| A3 | the invitation is single-use and expires |
| A4 | revocation takes effect at once, everywhere |
| A5 | an actual tool call decides COMPATIBLE |
| A6 | no security guarantee is overstated |
| A7 | the browser suite runs without technical setup |

**B1–B7 are new.** They exist because reviewing the prototype showed A1–A7 were not sufficient: a
prototype can satisfy every one of them and still keep a bearer token in `sessionStorage`, spawn an
unbounded thread per cancellation, and leave four older routes deciding for themselves. Each of
these is something the prototype, as it stood, would have failed:

| | |
| --- | --- |
| B1 | cancel is bounded, idempotent and durable |
| B2 | no publicly reachable route past the dispatcher |
| B3 | no durable JavaScript-readable browser token |
| B4 | the revocation contract is consistent |
| B5 | compatibility is bound to device, way in and challenge |
| B6 | nothing is left running |
| B7 | a person can get through it |

## What a pass does not mean

The profile carries this itself, and it is repeated here so it is not learned only by reading JSON.
The topology is single-host-development: not multi-tenant, not production-safe, not escape-proof.
Whether a real person can complete the flow unaided is DEFERRED_EXTERNAL_VALIDATION and is not
established by any automated result. A pass is not authorisation to expose anything, to merge, to
release, to deploy, to take payment, or to buy a domain or infrastructure.
