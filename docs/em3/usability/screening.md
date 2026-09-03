# Screening — who counts as one of the ten

All three must be true. Ask, do not assume.

1. **Not a software professional.** They have never worked as a software developer, system
   administrator, or in IT support. Studying something unrelated is fine.
2. **No command line in the last twelve months.** Ask plainly: *"Have you typed a command into a
   black window on a computer in the last year?"* A "no" is what you need.
3. **Ordinary computer or phone use.** They send email, browse, use a phone. Someone who never
   touches a screen at all is not the target of this product.

## Who cannot be one of the ten

* anyone who works on this project or has seen the prototype before;
* anyone in the founder's household;
* anyone who has heard the pitch. Enthusiasm is not the thing being measured.

## The two accessibility participants

At least two of the ten must **actually use** a screen reader, keyboard-only navigation, or text at
200 % or larger — in their normal life, not for the test. They are part of the same ten and count
towards the same threshold; they are not a separate, softer group. See `accessibility.md`.

## What goes in the evidence

Only this, per person: `U01`–`U10`, the three screening answers as yes/no, and whether they are one
of the accessibility participants. **No names, no email addresses, no ages, no employer, nothing that
identifies anyone**, in the repository, in logs, or in any review package.

Keep your own list of who is who on your own machine. `commit_roster.py` turns it into a fingerprint
so it can be proved the list existed before the first session, without the list ever leaving your
computer.
