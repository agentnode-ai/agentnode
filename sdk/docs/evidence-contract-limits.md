# What the evidence contract guarantees, and what it does not

Two requirements on the external-run evidence turned out to pull against each other. This page
records the contradiction, the decision, and what changed — because a criterion that is quietly
relaxed is worse than one that was wrong, and a reader of a later review needs to see that the
yardstick moved and why.

## The contradiction

A review profile was frozen before the evidence contract was written. Two of its criteria:

* one required the acceptance tests to travel the real path, which in practice means the record
  contains the actual `stdout` and `stderr` of the commands that ran;
* the other asked whether a raw credential could reach the evidence **by any path**, naming nested
  structures, fields added later, error messages and exception text.

Command output is arbitrary text produced by software the recorder does not control. It can contain
any string, including a credential nobody told the recorder about. So while the record contains real
output, the answer to the second question is yes.

Two reviews found this, correctly, and read the module's own honest disclosure as evidence of the
gap. That reading was right: the module says what it cannot do, and what it cannot do is what the
criterion asked for.

## The decision

`EM3C-V8-DECISION-0001` chose to **keep real output and state the exposure**, over three
alternatives: storing only digests and extracted fields, filtering output through a per-step
allowlist, or moving raw text to a separate artefact.

The reason is the same in each case. Output nobody predicted is the case most worth seeing, and the
alternatives all turn exactly that into a blank, a digest, or a second file that can drift from the
first. Evidence that is tidy when a run behaves and empty when it does not is evidence of the wrong
thing.

## What the contract now guarantees

* Every value the recorder was told is a secret is removed wherever it appears, at any depth,
  including in command lines, both streams, nested structures and fields added later.
* Every value under a key whose name marks it as a secret is removed whatever it contains.
* Recognisable private-key material is removed on sight.
* If any value the recorder was told to redact survives its own pass, **nothing is written at all**.
  The redactor's failure stops the record rather than producing one that quietly contains a
  credential.

## What it does not guarantee

A credential nobody named, with an unremarkable name and an unremarkable shape, sitting in the
middle of ordinary command output, is not reached by any of those layers.

Whoever runs the recording carries that. It is stated at the moment a recording starts, not only
here and not only in the module, because the risk arrives when commands run rather than when
documentation is read.

## Where this is acceptable and where it is not

For a gateway whose operator runs both machines, this is an informed trade: the same operator
controls the processes, the machines and the evidence, and would already have access to everything
the output could disclose.

It is **not** acceptable by default for a service running somebody else's sandbox. That setting
needs the workload confined so that no credential is available to it in the first place, tenant
isolation, controlled egress and metadata access, and an output policy written for adversarial
workloads. See `managed-sandbox-binding.md`, which records the other things such a service would
need and states plainly that none of them is built here.

## The criterion that changed

The original criterion asked for absolute absence. Its replacement asks for the layers above, for
the recorder's visible failure, and for the exposure to be stated to the operator. It deliberately
no longer claims that an unrecognised, uncollected credential cannot appear in arbitrary output.

Earlier verdicts issued under the original criterion stand as issued. They were not wrong; the
criterion was unsatisfiable alongside the requirement to keep real output, and the record of that
belongs here rather than in a quietly edited profile.
