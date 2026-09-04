# Session script — read the bold parts aloud, word for word

Do not paraphrase. The wording is part of the measurement: if you soften a question, you cannot
compare one person's answer to another's.

## Before you start

Have the prototype open at the first screen. Start a stopwatch when you finish the opening sentence.
Say nothing else until the person either finishes or gives up.

## Opening sentence — verbatim

> **"Use AgentNode to run this agent on this file, safely, and show me the result."**

That is the whole instruction. Do not add "click here" or "you'll want the top one".

## The five tasks

**Task 1 is the acceptance gate.** Tasks 2–5 are observed and recorded, but they do not change the
8-of-10 threshold — that threshold belongs to task 1 alone, as fixed in the frozen protocol before
anyone was recruited.

| # | Say this, verbatim | What you record |
|---|---|---|
| 1 | *(the opening sentence above)* | completed unaided? seconds to the result? |
| 2 | **"Where did the agent's program code run? Show me on the screen where it says that."** | found it? where did they look? |
| 3 | **"Change it so the agent works somewhere else, then go back to how it was."** | completed unaided? |
| 4 | **"Stop a run in the middle."** | completed unaided? |
| 5 | **"Imagine you did not want it to use one of those files. Fix that."** | completed unaided? |

If a task is impossible in the person's situation — task 3 has nowhere to switch to, for instance —
record `n/a`, not a failure.

## The two closing questions — verbatim, before any debrief

Ask these **before** you explain anything. Once you have explained, the answer is worthless.

> **1. "Where did this agent's program code run?"**
> Read the options aloud: *on my computer · on AgentNode's computers · on my own server · I don't know*

> **2. "Do you think code from a stranger ran unprotected on your computer?"**
> Read the options aloud: *yes · no · I don't know*

Write down what they say. Do not correct them.

Question 1 measures whether the product told them the truth clearly; a wrong answer is recorded but
is **not** a failure on its own. Question 2 is the absolute one: **a single "yes" fails the entire
run.**

## What you may say during a session

Exactly one sentence, and only when someone has stopped and looks stuck:

> **"What would you do next?"**

Nothing else. No pointing, no "try the other button", no taking the mouse. If you say anything else,
mark that person `moderator_intervened = yes`, which counts as a failure of the measurement.

## When to stop a session

* they reach the result → record the time;
* they say they want to stop → record `gave_up`;
* five minutes pass with no progress → stop, record `gave_up`.

## Afterwards

Thank them, then explain whatever they ask. The debrief is not measured, and it is the part where you
find out *why* — write down anything they say that surprised you, in the `notes` column, without
their name.
