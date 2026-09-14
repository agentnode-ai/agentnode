# Counter-checks

A green test proves nothing about a property until the property has been removed and the test has
been watched to go red. Each block below removes ONE property, names a DIFFERENT test, and shows
its exit status. A block whose named test still passes is reported as proving nothing, because
that is what it does.

These were run with `scratchpad/cc_final.py` against a tree synced to `/root/rr/sdk`. Every
block restored the file it touched, and each restoration was verified by comparison. This is a
record of what happened, not an instruction to anybody reading it.

## The bounded cancellation (earlier cycle, `scratchpad/cc_stop.py`)

| | property removed | test that went red |
| --- | --- | --- |
| 1 | a repeat starts a second cancellation instead of joining | `test_a_storm_against_one_run_produces_exactly_one_cancellation` |
| 2 | an unconfirmed teardown is forgotten rather than kept | `test_a_teardown_that_could_not_confirm_stays_written_down` |
| 3 | a thread per request instead of a fixed pool | `test_the_hands_are_a_fixed_number_however_many_runs_are_stopping` |
| 4 | unlimited new cancellations per device | `test_a_device_asking_for_too_many_new_stops_is_refused` |
| 5 | `close()` does not wait for the hands | `test_and_everything_started_is_joined_by_close` |
| 6 | status reports terminal while cleanup is unconfirmed | `test_and_the_run_says_stopping_until_it_is_really_over` |
| 7 | the operator's stop does not apply to cancellation | `test_the_operators_stop_refuses_a_cancellation_as_well` |
| 8 | a restarted gateway does not pick up what it was stopping | `test_a_gateway_built_over_the_same_directory_picks_up...` |

One block in that run was a bad revert of mine that broke collection. The harness reported "no
test ran, proves nothing" rather than counting it, and it was redone.

## The consent gate and the tool exclusions (earlier cycle)

| | property removed | test that went red |
| --- | --- | --- |
| A | the door is a constant, so any door matches | `test_an_approval_given_over_one_door_is_not_usable_from_another` |
| B | the approval is not recomputed for the job in hand | `test_a_disclosure_for_one_job_will_not_run_another` |
| C | the account and door are dropped from the bound list | `test_the_bound_list_itself_contains_what_it_must` |
| D | what it does not model is left unsaid | `test_and_it_says_plainly_what_it_does_not_cover` |

Two of the first four were mine rather than the code's. One removed an entry from the bound list,
which also removed the parametrised case that checked it -- so nothing ran. That is a real
weakness in a test parametrised over the list it polices, and the required set is now asserted
separately, where deleting something cannot delete its own check. The other used `[] or [...]`,
which is truthy, so nothing was reverted at all.

## This cycle

| | property removed | test that went red |
| --- | --- | --- |
| 1 | the session cookie is readable by scripts | `test_the_cookie_is_one_a_script_cannot_read_or_send_from_elsewhere` |
| 2 | changing state no longer needs the confirmation value | `test_but_changing_something_does` |
| 3 | withdrawing a device leaves its session records behind | `test_and_the_session_records_are_gone_rather_than_merely_unusable` |
| 4 | the session identifier is stored rather than its hash | `test_and_what_is_stored_on_disk_is_only_a_hash` |
| 6 | the challenge stops caring WHICH connection called | `test_not_another_connection_on_the_same_account` |
| 7 | ... or over which channel | `test_not_the_right_connection_over_the_wrong_channel` |
| 8 | ... or whether it happened before the challenge existed | `test_not_a_job_that_happened_before_the_challenge_existed` |
| 9 | ... or whether the call actually succeeded | `test_not_an_attempt_that_was_refused` |
| 10 | the page builds markup out of what somebody typed | `test_it_is_rendered_rather_than_run` (both cases) |
| 11 | a token may be written where the config file is readable | `test_nothing_is_written_and_the_reason_is_given` |
| 12 | the invitation is not needed to open a browser session | `test_is_good_for_one_exchange` |
| 13 | withdrawing a device leaves its unspent download standing | `test_an_unspent_download_is_gone` |
| 14 | ... and its work still running | `test_work_it_had_in_flight_is_stopped` |

13 and 14 were added after a review refused the package on revocation. Removing a credential
stops the NEXT request, and that is not the whole of revocation: a device's sessions, its work
in flight, and any unspent download it was issued are all authority it has ALREADY been given.
The download is the sharpest, because collecting one mints a fresh credential -- so leaving one
standing is a way to walk straight back in.

Three of these were wrong on the first attempt and are worth recording as such.

**3 and 6 named a test that a DIFFERENT property was already protecting.** Withdrawing a device
makes it unidentifiable, so its sessions stop working whether or not the records are removed --
and the test observing that would have gone on passing with the removal gone. A test that
isolates the records was added, and the counter-check re-run against it. Likewise the challenge's
device binding: the first test tried the browser, which also differs by channel, so the channel
check caught it. Re-run against a connection on the *same* channel, it goes red.

**5 has no separate check to remove, and that is the finding.** "An ended session is nobody" is
not enforced by a check that could be deleted: the session store is the only thing that maps a
session identifier to a device, so an identifier that is not in it yields nothing to be. Removing
the two redundant guards leaves the property standing, which is why the block stays green. It is
recorded here rather than dressed up as a red test, because a counter-check that cannot fail is
not evidence and saying otherwise would be worse than saying nothing.

## The baseline

Six tests fail in a full run and failed identically at `f686861`, before any of this work: four
in `test_agent_m1_transaction`, one in `test_agent_m1_amendment`, one in
`test_layer3_installer_concurrency`. They belong to the installer work. The comparison was made
by exporting `f686861` to a separate tree and running the same command against it, not by
inspection.

**There were seven, and the seventh was mine.**
`test_stopping.py::test_the_hands_are_a_fixed_number_however_many_runs_are_stopping` failed in a
full run, and calling it "pre-existing" because it also failed at `f686861` was true and
misleading: `test_stopping.py` was written earlier in this same arc, and that test is the
bounded-execution gate. A review refused the package for it, correctly — baseline attribution
cannot turn a failed mandatory gate into a pass.

The cause was the test, not the code. It counted every thread in the process whose name began
with `agentnode-stopping` and required exactly two, so it passed alone and failed in a full run
where other tests legitimately have pools of their own. It was measuring the suite rather than
the property. It now measures the pool's own hands, and a second test states the global version
honestly: N pools cost N times the fixed number, and not more.
