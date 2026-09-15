# Three yardsticks for Managed Alpha R2, frozen before the work they measure

The commit that adds this file **contains no code**. Everything these three profiles judge is
written afterwards, in later commits, and that ordering is checkable in git rather than asserted
here:

```
git log --follow --format='%h %ad %s' -- sdk/docs/review/FREEZE-R2.md
git log --follow --format='%h %ad %s' -- sdk/agentnode_sdk/gateway/accounts.py
```

The same reasoning as [FREEZE.md](FREEZE.md): criteria written after an implementation are criteria
the implementation cannot fail, because you can always find a way to describe what you happened to
build such that what you built satisfies it — and the describing feels like reviewing.

## The three

R2 is not one contract. It is three that fail in different ways and would hide each other's
failures if judged together, so each has its own profile and each passes or fails on its own.

| Profile | SHA-256 | Bytes | Judges |
| --- | --- | --- | --- |
| `alpha-r2-tenancy-r1` | `6a2336c5eaad226fd8b45cd0b927ad057b317a1b1029e478df13523c90d88fc1` | 7 317 | whether two customers can exist here without reaching each other |
| `alpha-r2-admission-r1` | `40020d5cfaf6197f73d5a4d88cdd098cfa6bfcfcd972ae715acc6f2feea3f308` | 8 864 | what happens before a job runs, and whether the operator's policy is really the only basis |
| `alpha-r2-dataops-r1` | `b7be76035d95b049e215269041b4bcd81eb133331465d6424414a910330ca547` | 7 754 | what the service keeps, what it deletes, and whether use is attributable without being alterable |

`.claude/` is gitignored, so the live profile a reviewer loads is not itself in the repository.
Byte-identical copies sit beside this file. At review time the two must still match:

```
sha256sum sdk/docs/review/alpha-r2-tenancy-r1.json
sha256sum .claude/codex-review/profiles/alpha-r2-tenancy-r1.json
```

If they differ, the profile was edited after the freeze and the review that ran under it is worth
nothing — rerun it under the frozen text, or say plainly that the yardstick moved.

## What each one is for

**Tenancy (T1–T7).** Until now every device on this gateway could list every other device, and
`devices.revoke` resolved its target by scanning every credential the gateway holds. One customer
could therefore withdraw another's. That is the defect class this profile exists to catch, so it
asks the question in the general form — *can account A obtain or change anything belonging to
account B, on any operation, through any door* — rather than as a list of the places it is known
to be wrong today.

**Admission (D1–D9).** The obvious failure is doing too little. The likelier one is claiming too
much, which is why **D8 is blocking**: no part of this may be described as detecting illegal or
malicious *intent*. What can honestly be built is technical limits, operator rules, behavioural
signals and the ability to suspend — and the difference between that and intent detection is the
difference between a true claim and a false one.

**Data operations (P1–P8).** Deletion and tamper-evidence pull against each other: a hash chain
exists to prove nothing was removed, and erasure is the removal of something. **P4 is the
criterion that reconciles them**, and it is written so that a reconciliation which hands anybody
who can delete a way to remove a line invisibly fails it.

Each profile ends with the same criterion — that the evidence would fail without the mechanism.
That is not padding. A counter-check that passes either way is the failure mode this project has
hit most often, and stating it per profile means it is judged three times rather than once.

## What a pass does not mean

Each profile carries this itself; repeated here so it is not learned only by reading JSON.

The topology is `single-host-development`. Two accounts' jobs run on one kernel. The tenancy
profile can establish that one account cannot **read, reach or withdraw** another's data and
credentials through the service; it cannot establish that one account's *job* cannot affect
another's through the machine. That needs the separate worker host, which is a founder decision
and is not taken here.

A pass is not authorisation to expose anything, to release, to deploy, to take payment, or to buy
a domain or infrastructure. Port 8099 stays closed.
