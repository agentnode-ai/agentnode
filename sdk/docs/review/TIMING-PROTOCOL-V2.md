# Timing protocol v2 — the numeric amendment, frozen before any data

`TIMING-PROTOCOL-V2-DECISION-0001` returned **PASS on Option A** and raised
`F4-PROTOCOL-NUMBERS-OPEN`:

> The protocol fixes the statistical decision rule but leaves the pair count, permutation count,
> bootstrap-resample count, process/run counts, and sensitivity effect or margin qualitative.
> Authoritative collection cannot begin reproducibly while these remain open.

This file closes that, and it is committed **before the measurement exists**. The git history is
what makes that checkable rather than claimed — the same arrangement used for the existence
tests, which were frozen in one commit and satisfied in the next.

**None of the numbers below is derived from an observed value.** Not from the failed first
observation (0.569 against 0.512), not from the investigation that found its cause. Each is
justified here on its own terms, and the justification is part of the freeze.

---

## 1. What is measured

Two situations, for one authenticated caller:

* **foreign** — an identifier that exists, in another account
* **absent** — an identifier that exists nowhere

The quantity is the **median difference in nanoseconds** between them. A median rather than a
mean because latency distributions have a long right tail and a mean measures the tail.

## 2. The numbers

| | | why this number |
|---|---|---|
| identifier pairs | **64** | Each pair is one foreign and one absent identifier, generated identically and assigned by coin. 64 is enough that no single identifier's hash or bucket can carry the result, and small enough that each is probed often. Chosen as a power of two; not derived from any measurement. |
| probes per identifier per process | **256** | Gives 16 384 observations per group per process — four significant figures of resolution on a median without the run taking longer than a CI lane tolerates. |
| warmup probes per process | **2 048**, discarded | Enough that the interpreter's first-call costs, import-time laziness and allocator warmup are behind the measurement rather than inside it. |
| fresh processes per run | **5** | Process state — allocator layout, hash seed, address-space layout — is a confound that cannot be removed inside one process. Five is enough for a median across processes to be meaningful. |
| independent runs | **3** per Python version | On Linux, on 3.10, 3.11 and 3.12: **nine runs**. Three per version so that one unlucky runner cannot be the whole answer for a version. |
| permutations | **10 000** | An empirical two-sided p-value with resolution 1e-4, which is finer than the 1e-2 the decision rule needs. |
| bootstrap resamples | **10 000** | The same resolution for the confidence interval, by the same argument. |

## 3. Pooling

Per process, a median difference is computed from that process's own paired observations. The
run's statistic is the **median of the five per-process medians**.

The permutation null is built **the same way**: labels are shuffled *within* each process, the
per-process medians are recomputed, and their median is taken. Shuffling within processes rather
than across them keeps process-to-process variation in the null where it belongs — a null built
by shuffling across processes would be wider than the statistic it judges and would pass
anything.

## 4. The decision rule

Over the **nine holdout runs**, combined once rather than judged nine times:

* **Fisher's method** over the nine two-sided permutation p-values, and the combined p must be
  **≥ 0.01**;
* **and** the 99% bootstrap confidence interval on the pooled median difference — pooled over all
  nine runs — must **contain zero**.

Both must hold. One combined decision rather than nine separate ones, because nine independent
tests at 1% would fail about 9% of the time on a gateway with no channel at all, and a gate that
cries wolf once in eleven runs teaches people to ignore it. Fisher's method is also *stricter*
against the thing that matters: a small consistent effect that never crosses in any single run
still combines to a small p.

## 5. The sensitivity control

A deliberate difference is inserted on the foreign path and the whole protocol is run against it.
The inserted effect is **one additional dictionary probe on a populated mapping** — defined as
work rather than as a number of nanoseconds, because it is the smallest unit of work the
mechanism under test could plausibly leak, and because a nanosecond figure would be a statement
about a machine.

Acceptance: the sensitivity run must produce a combined **p < 0.001** and a 99% interval
**excluding zero**. If it does not, **the entire run is void** and no verdict is taken from it.
An instrument that cannot see one extra dict probe is not evidence that there is no extra dict
probe.

## 6. Calibration and holdout

* the **calibration** set establishes only two things: that the machinery runs end to end, and
  that the sensitivity control fires. No verdict is taken from it, and no number in this file may
  be revised after seeing it.
* the **holdout** set is generated separately, after calibration has passed, and the verdict comes
  from it alone under the rule in §4.

If the holdout fails, the response is to harden the implementation and generate a **new** holdout
under this same frozen file. It is never to adjust anything in §2, §4 or §5.

## 7. Both vantages

* **internal** — at `_a_run_of_this_caller`, where the branch would be. More sensitive than any
  attacker, because the door's own work only adds noise on top.
* **black box** — at the real API boundary, over HTTPS, with a real credential, measuring what
  somebody outside actually has.

The internal one is the gate. The black-box one is reported alongside it and is expected to be
noisier; a null there is weaker evidence than a null internally, and it is reported as such.

## 8. The keyed digest: lifecycle and cost

`F1-HOT-PATH-AND-SECRET` is right that a per-gateway secret is a dependency. So there is not one:

* the namespacing key is **generated in memory at process start** and **never written to disk**;
* it is **never rotated**, because it is never persisted — a restart produces a new one, and the
  index it namespaces is rebuilt at startup anyway;
* it therefore has **no lifecycle, no file, no permissions and nothing to leak**. Losing it is
  what a restart is.

Its cost must be **the same for every caller-supplied identifier, whatever its length**. The
identifier is bounded by the contract before it is hashed and the digest runs over a fixed-width
buffer, so a four-character identifier and a four-kilobyte one cost the same. A test asserts
that, and it is a test about work rather than about time: the same code path, the same buffer
size, for any input.

## 9. What a PASS under this file will and will not mean

It will mean: no effect survived above a null distribution measured in the same runs; the
instrument demonstrably catches one extra dictionary probe at the same settings; and the verdict
came from data that did not exist when this file was written.

It will **not** mean constant-time execution in CPython. Garbage collection, allocator behaviour,
dictionary probing and the interpreter's own scheduling all remain. The report will say that in
those words, as this file does.
