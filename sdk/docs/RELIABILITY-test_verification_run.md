# test_verification_run.py — open reliability point

Status: **NOT REPRODUCED, INSTRUMENTED, NOT A RELEASE GATE.**

## What was claimed before and what is actually established

The SDK lane failed on four of five pushes during the alpha review, always in this file, always
a submission to the session gateway timing out after thirty seconds, always near the end of the
run, on a different Python version each time. One real fault was found and fixed while chasing it
(two watcher threads per gateway outliving every server the suite created). Whether that fault was
the cause of the timeouts is **not established**, and this file exists so that it is not assumed.

## What was measured

On the Linux DevelopServer, at this head:

* the file alone: **34 passed, 1 skipped in 6.5s**, threads flat at 4, descriptors flat at 14;
* the whole collectable suite: **4853 passed in 405s**, and this file passed within it.

So the CI failure did not reproduce here. What did show up is resource accumulation across the
run: threads 1 → 12 (peak 15), descriptors 12 → 390 (peak 391), with 6 `serve_forever` threads
still alive at the end. 298 tests leave something behind; `test_em3c_gateway.py` accounts for 208
descriptors of the 400.

That accumulation is a **test-harness** fault, not a product one. `GatewayState.close()` exists
and releases the held directory descriptor; the tests simply never call it, and servers they start
are often never shut down. The sandbox core is not implicated and is not being reopened for it.

390 descriptors and 15 threads are also well inside any normal limit, so the leak is **not
sufficient on its own** to explain a thirty-second timeout. It is a real untidiness with a
plausible but unproven connection to the symptom.

## What is now in place, so the next occurrence classifies itself

`tests/reliability.py`:

* a **trail** — threads, descriptors and thread-name shapes recorded around every test when
  `AGENTNODE_DIAGNOSE` is set, so a fault that accumulates shows as a line going up rather than as
  whichever test happens to trip over it;
* a **dump** — the stack of every live thread, taken only when something has already timed out and
  attached to the failure. A server thread blocked on something names it; no blocked thread at all
  says the opposite. Those are different faults and this is what tells them apart.

The session gateway's submit and wait paths carry that dump now.

## The rule this file sets

Until a CI occurrence has been captured with the dump attached and classified from it, **this
lane's result is not a product-release gate**, and it is not to be re-run until green. A green
obtained by repetition says nothing about the fault and hides the next one.


## The leaks, measured before and after (2026-09-12)

The trail said 298 tests left something behind: descriptors 12 → 390 at the end of a run, threads
1 → 12. That is verification noise whatever else it is, and noise of that shape hides the next
lifecycle fault, so it was fixed rather than left instrumented.

The cause was not a missing `close()` -- that has always existed. It was that a `GatewayState`
nobody closed could never give its descriptor back at all, which is a leak in production too: this
service is about to hold one state per user and per device instead of one per process, and there
would be no call site to blame. The state now releases the descriptor when it is dropped as well
as when it is closed, the finalizer is DETACHED on an explicit close (closing a descriptor number
twice can close somebody else's file, because the number is reused the moment it is free), and the
state is a context manager.

Measured on the same suite, same machine, before and after:

    descriptors at the end   390  ->  17      (peak 391 -> 67)
    threads at the end        12  ->  11      (peak  15 -> 14)

`tests/test_lifecycle_release.py` holds it down: the descriptor comes back when closed, when
dropped, and from a `with`; the finalizer does not fire after an explicit close; every terminal
state in `TERMINAL_STATES` leaves the same nothing behind; and ten cycles do not cost more than
the first. That last one is the property the trail found missing, and it is stated as growth --
counts coming DOWN as stragglers finish is the opposite of the fault and must not fail it.

### What is still open

Threads. Five `serve_forever` and three handler threads are alive at the end of a run, from test
helpers that start a server and never shut it down. One of each is the session gateway and is
legitimate. A finalizer cannot fix the rest, because a running thread holds a reference to the
server it is serving, so nothing collects it -- these have to be shut down by the code that
started them, across roughly a dozen helpers.

That is ordinary work and it is NOT done yet. It is smaller than it was and it is bounded, and it
does not block the access layer.
