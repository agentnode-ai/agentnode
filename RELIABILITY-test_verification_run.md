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
