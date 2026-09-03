# The ten-person test — everything you need, in five minutes of reading

This is the one part of EM-3A that software cannot produce. The rule is deliberate and it is not
negotiable: **a real person has to try it.** Automated browser tests, Claude, Codex and invented
personas do not count, and automated accessibility checks may only *add* to this, never replace it.

You need **exactly ten** people. Not nine, not twelve.

**Nothing needs to be installed** — not by you and not by the people testing. The prototype and the
scoring page are ordinary web pages you open in a browser. The two Python scripts are the same tools
for anyone who prefers a terminal, and for CI; you never have to touch them.

## What you do, in order

1. **Find ten people** who match `screening.md`. Roughly: they use a computer or phone for everyday
   things, they have never worked as a software developer, and they have not typed a command into a
   black window in the last year. At least two of them must actually use a screen reader,
   keyboard-only navigation, or large text — see `accessibility.md`.
2. **Write the list before the first session** and fingerprint it — drop the file onto
   `evaluate.html`, which also does this, or run `python commit_roster.py <your-list.txt>`.
   Either prints a fingerprint. Put the fingerprint in `results.csv`; **keep the list itself on your own
   machine.** No names go into the repository, into logs, or into any review evidence — only U01–U10
   and the confirmed screening facts.
3. **Give each person the link** to the prototype (`onboarding-prototype.html`, or the hosted copy).
   Nothing to install, no account, no terminal.
4. **Read `session-script.md` aloud** — the opening sentence word for word, then the five tasks.
   Then ask the two closing questions, word for word.
5. **Fill in one row per person** in `results.csv`.
6. **Score it.** Open `evaluate.html` in your browser and drop `results.csv` onto it — it applies
   the thresholds and prints PASS or FAIL, with no Python and nothing installed. If you would rather
   use a terminal, `python evaluate.py results.csv --gate prototype` does the same thing and is what
   CI uses.

Budget about 15 minutes per person, plus your setup. It does not have to happen in one day.

## The one thing that decides it

Two thresholds, both fixed before anyone was recruited:

* **8 of the 10** complete the first safe run **unaided** — and, at the whole-programme gate
  only, within three minutes. The prototype gate has no time bound;
* **zero** people answer "yes" to *"Do you think code from a stranger ran unprotected on your
  computer?"*

The second one is absolute. A single "yes" fails the whole run, even if all ten finished. Someone who
completes the task but believes they were exposed has not been given a product they can trust.

## The rule that makes the result mean anything

**You may not help.** If a person is stuck, you may say exactly one thing: *"What would you do
next?"* You may not point, hint, explain, or take the mouse. If you do anything else, that person is
recorded as a failure — not because they failed, but because the measurement did.

That will feel uncomfortable. Watching someone struggle with something you built is the entire point:
it is the only way to find out whether the product works without you standing next to it.

## What to do with a bad result

Nothing, immediately. Record it and hand over the raw rows. A failed run is a finding about the
product, not about the participants, and the fix is never to loosen the threshold — the thresholds
were frozen before recruitment precisely so they cannot move afterwards.
