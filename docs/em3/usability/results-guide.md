# Filling in results.csv

One row per person, ten rows, ids U01-U10 in order. Copy `results-template.csv` to `results.csv`
first — keep the template clean.

| column | what to write |
|---|---|
| `participant` | `U01` ... `U10`. **Never a name.** |
| `roster_fingerprint` | the value `commit_roster.py` printed, on the first row is enough |
| `screen_not_developer` | `yes` if they have never worked in software/IT |
| `screen_no_terminal_12m` | `yes` if no command line in the last twelve months |
| `screen_everyday_use` | `yes` if they use a computer or phone normally |
| `assistive` | `screenreader`, `keyboard`, `magnification`, or empty |
| `completed_unaided` | `yes` only if they reached the result with no help at all |
| `seconds_to_result` | stopwatch, from the end of your sentence to the result on screen |
| `moderator_intervened` | `yes` if you said anything beyond "What would you do next?" |
| `task2..task5` | `yes`, `no`, or `n/a` when the task made no sense in their situation |
| `q1_where_did_it_run` | exactly what they answered: `local`, `agentnode`, `own_server`, `dont_know` |
| `q2_unprotected_belief` | `yes`, `no`, or `dont_know` — **verbatim, do not soften a yes** |
| `notes` | anything that surprised you. No names, no identifying details. |

Then: `python evaluate.py results.csv`

A row you are tempted to leave blank because "it did not really count" is exactly the row that
matters. Record it as it happened.
