# Two criteria that only a run could satisfy, and the run waited on them

This records a change to the yardstick, in the place a later reader will look for it. A criterion
that moves quietly is worse than one that was wrong, and this one moved.

## The ordering that could not resolve itself

A review profile, `em3c-evidence-contract-r8`, was frozen to govern the evidence contract and the
driver that produces the record of the external run. Two of its twelve criteria asked for facts:

**V5-TWO-MACHINES** — *"Judge whether the evidence establishes that the client and the gateway are
different hosts with different filesystems. Determine whether any part of that conclusion still
rests on a locally failed command, whether each machine's identity and operating system are
captured over its own channel, whether every command's origin, exit status and separated outputs
are recorded, and whether a random sentinel generated on each machine is verified over the other
channel so that neither side's claim rests on its own account. Judge whether hashing of identity
values preserves what the conclusion needs."*

**V6-POLICY-BINDING-IN-EVIDENCE** — *"Judge whether the external report captures, from the active
authenticated snapshot, the canonical operator-policy digest, the policy generation, the full
normalised allowlist, the required-property set, the backend and runtime identity, the
conformance-report digest, the job's requested and effective policy digests, and the run id with
the gateway's own record of that run. Determine whether the policy digest and generation are
compared before and after the run, and whether a missing, stale or divergent binding produces a
red result rather than an omission."*

`EM3C-EVIDENCE-0008` judged ten criteria PASS and these two NOT_EVIDENCED, on the grounds that
offline simulation can validate the machinery but cannot establish that two particular computers
were two, or that one gateway answered a particular way on a particular afternoon. **That
determination was correct and stands as issued. The BLOCK it produced was correct.** Nothing here
converts it into anything else.

But the external run was authorised to happen once, after that review passed. So the run could not
begin until the review passed, and the review could not pass until the run had happened. Nothing in
a repository moves that; it is the order the two things were put in.

## The decision

`EM3C-V5V6-DECISION-0001` found the ordering genuinely circular, and split each criterion into two
with stable identifiers.

| Identifier | Stage | Blocking | Where it lives |
| --- | --- | --- | --- |
| `V5-PRE-RUN-TWO-MACHINE-EVIDENCE-MACHINERY` | pre-run | yes | `em3c-evidence-contract-r9` |
| `V5-POST-RUN-TWO-MACHINES-OBSERVED` | post-run | yes | `em3c-external-record-r1` |
| `V6-PRE-RUN-POLICY-BINDING-EVIDENCE-MACHINERY` | pre-run | yes | `em3c-evidence-contract-r9` |
| `V6-POST-RUN-POLICY-BINDING-OBSERVED` | post-run | yes | `em3c-external-record-r1` |

**`V5-PRE-RUN-TWO-MACHINE-EVIDENCE-MACHINERY`** — Judge whether the recorder and verifier are
capable of producing and rejecting a two-machine proof: each machine's identity, operating system,
filesystem discriminator, command origin, exit status, stdout and stderr must be recorded from that
machine's own channel; two independently generated random sentinels must cross in opposite
directions and be confirmed over the other channel; locally failed commands, missing data,
identical sentinel values, or unverifiable hashing must prevent success. Passing this criterion
establishes machinery readiness only and makes no claim that two real machines exist or were used.

**`V5-POST-RUN-TWO-MACHINES-OBSERVED`** — From the recorder-generated record of the authorized
external run, judge whether the actual client and gateway were different hosts with different
filesystems using successful per-channel identity evidence and both independent cross-channel
sentinel proofs. Simulation, assertions, missing statuses or streams, failed commands,
self-attestation, or incomplete discriminator evidence must be refused.

**`V6-PRE-RUN-POLICY-BINDING-EVIDENCE-MACHINERY`** — Judge whether the recorder and verifier can
capture from the active authenticated snapshot the canonical operator-policy digest, generation,
complete normalized allowlist, required-property set, backend and runtime identities,
conformance-report digest, requested and effective policy digests, run id, and gateway record;
compare digest and generation before and after; link each job to the policy in force; and produce a
red or evidence-error result for missing, stale, incomplete or divergent data. Passing establishes
machinery readiness only and makes no claim about an actual run or active snapshot.

**`V6-POST-RUN-POLICY-BINDING-OBSERVED`** — From the recorder-generated record of the authorized
external run, judge whether all named binding and run values were captured together from the active
authenticated snapshot and gateway record, whether every job was linked to the policy in force, and
whether before/after digest and generation comparisons hold. Refuse simulation, omitted fields,
unauthenticated or incomplete answers, stale or divergent bindings, unmatched run identifiers,
absent gateway records, and any value reconstructed outside the record.

## What the post-run half is not

The decision recorded that it is not weakened, not waived, and not satisfiable by anything at the
pre-run stage: a pre-run pass is about machinery, the facts the original V5 and V6 asked for remain
mandatory in full, and they are decided only against the record of the real run, where missing,
contradictory or unobserved facts are red.

## What the decision said a pre-run pass is

One thing: **"PASS — pre-run evidence machinery readiness."**

The decision recorded that such a verdict is not a V5 or V6 pass, not an external-evidence pass,
not a finding that two machines exist, not a finding that any policy was in force, and not
authorisation of anything. Its reasoning was that a verdict reading as though two machines had been
established, when no machine was involved, would be worse than the circularity it resolved.

## Review and authorisation were held to be different things

The decision recorded that no verdict at either stage authorises the external run, a merge, a
release or a deployment: a review establishes what is true of the code, and the run is authorised
separately and explicitly by the founder. The split exists so that a review can say something true
at the moment it is asked, rather than granting something it was never able to grant.

## Who adopted it, and when

Proposed by the reviewer as `EM3C-V5V6-DECISION-0001`, on the finding of `EM3C-EVIDENCE-0008`.
Adopted by the founder on 2026-09-09, together with the standing sequence: submit the pre-run
profile; freeze the post-run profile before the run; build the candidate wheel exactly once from
the approved commit and identify it by SHA-256; prepare both machines demonstrably clean; perform
exactly one run over the full A–J matrix; have the unchanged real record judged under the post-run
profile.

## How the decision tied a later verdict to the record

The post-run review is about one file: the evidence the driver wrote during that run. The decision
recorded that its verdict names that file by digest, so that the record judged and the record that
exists are the same record -- its reasoning being that a post-run verdict which cannot be tied to a
specific file establishes nothing, and that a record altered after the run is not the record of the
run.

## What the decision named as ways of being dishonest about it

Applying it backwards. Describing either criterion as unchanged. Using it to turn the earlier BLOCK
into a factual pass. Omitting the post-run review. Deriving what was expected from what was
observed. Implying evidence or authorisation that does not exist. None of those is done here, and a
later reader who finds one of them has found a defect worth naming.
