"""Who owns a server that a test started, and what giving it back means.

Twenty-two places in this suite said

    threading.Thread(target=server.serve_forever, daemon=True).start()

and threw the handle away. Nobody owned what that started, so nothing could end it: the thread
kept the server alive, the server kept its listening socket, and a run finished with five of them
still going. A finalizer cannot reach any of that -- a running thread is a reference, so the
object is never collected and the finalizer never fires. Only whoever started it can stop it.

So starting one goes through `owned()`, which registers the pair with the ExitStack that belongs
to the running test. The stack is the ownership boundary; the test is the owner. That is the
regular lifecycle and not a safety net: the stack unwinds whether the test passed, failed, raised
or was interrupted half way through setting something up.

`stop()` is the whole of giving one back, in order, and all four steps matter:

    shutdown()      serve_forever returns, and the watchers are told to end
    join()          with a bounded deadline -- waited for, never assumed
    server_close()  the listening socket goes back
    state.close()   the state's directory descriptor goes back

Waiting rather than assuming is deliberate. The watchers sleep between looks, so they end within
a second or two rather than at once; a test that measured immediately would be measuring the
sleep. A bounded deadline still catches the real fault, because a thread that has genuinely
leaked never ends however long anyone waits.
"""
from __future__ import annotations

import contextlib
import threading

#: The ExitStack belonging to the running test. Set by the autouse fixture in conftest, which is
#: the only thing that may set it: a module-level owner shared between tests would be no owner.
_owner: contextlib.ExitStack | None = None

#: How long to wait for a thread to notice it is finished before calling it stuck.
PATIENCE = 10.0


def _adopt(server, thread, state) -> None:
    if _owner is not None:
        _owner.callback(stop, server, thread, state)


def owned(server, state=None):
    """Start serving, and make sure somebody is responsible for stopping it.

    Returns the thread, so a caller that wants to join it itself still can. Calling `stop()` twice
    is harmless, so a test that tidies up explicitly does not fight the stack that would have.
    """
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _adopt(server, thread, state)
    return thread


def stop(server, thread=None, state=None, seconds: float = PATIENCE) -> None:
    """Give a server back. Safe to call twice, and safe on one that never started."""
    try:
        server.shutdown()
    except Exception:                                         # noqa: BLE001 - already gone
        pass
    if thread is not None:
        thread.join(timeout=seconds)
    try:
        server.server_close()
    except Exception:                                         # noqa: BLE001 - already closed
        pass
    if state is not None:
        state.close()


@contextlib.contextmanager
def serving(server, state=None):
    """For a caller that wants the boundary in one place rather than at test scope."""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        stop(server, thread, state)


def still_serving() -> int:
    """How many serving threads are alive. What a guard counts."""
    return sum(1 for t in threading.enumerate()
               if "serve_forever" in t.name or t.name.startswith("http-handler"))


def quiet_again(down_to: int, seconds: float = 8.0) -> int:
    """Wait, with a deadline, for threads to actually end. Never an assumption about speed."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and threading.active_count() > down_to:
        time.sleep(0.05)
    return threading.active_count()
