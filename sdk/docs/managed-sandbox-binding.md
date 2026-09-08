# Two levels of binding, and only one of them is built

This gateway binds its conformance report to the **operator policy**: what this machine's owner
allows. That is implemented, measured and enforced — a report is about one policy, and readiness
holds only while the policy in force still hashes to the digest the report was taken for.

A managed sandbox operated by AgentNode needs a **second** level, and it is not built here. This
page records what it is, so that it is neither forgotten nor quietly half-implemented.

## What is built

The operator policy covers what the machine's owner permits: the network mode, the allowlist, the
resource and time limits, the runtime requirements, and the set of properties that must have been
measured. Its canonical digest is inside every conformance report, and every admission compares the
two. Changing the policy invalidates the report for that policy, and the new one takes effect only
after it has been measured as itself.

## What is not built, and what it would have to do

A managed service has rules of its own — what it will run for anyone, independent of what a
particular operator allows. Those are service and abuse rules, and they change on their own
schedule.

Three things follow, and all three are outstanding:

**A stable digest for the service rules.** The operator policy has one; the service policy would
need its own, versioned the same way, so that a report can say which service rules were in force
when it was taken.

**An admission decision bound to each individual job.** The operator policy is a ceiling checked
once per job against a report. A service rule is a decision *about that job* — what it asked for,
what it carries, who sent it. That decision has to be bound into the run record the same way the
policy digests are, or the record cannot say why the job was allowed.

**Re-measurement when the service rules add a technical duty.** Not every change to a service rule
touches what the sandbox must be able to enforce. Some do: a rule that requires egress be sealed, or
that a class of job run without network at all, introduces a new mandatory property. When that
happens the existing conformance report no longer establishes what is now required, and it must be
invalidated exactly as an operator-policy change invalidates it.

## Why it is recorded rather than started

The current arc is the evidence contract for external verification. Building a second binding level
now would mean changing the mechanism that was just reviewed and passed, in the same breath as
repairing the machinery that is supposed to check it. This page exists so that the requirement
survives the gap.

Nothing in this build implements any part of it. There is no service-policy digest, no per-job
admission decision beyond the operator ceiling, and no re-measurement triggered by a service-rule
change. A reader should assume none of the three is present until this page says otherwise.
