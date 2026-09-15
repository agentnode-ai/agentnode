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


def owned(server, state=None, owner=None):
    """Start serving, and make sure somebody is responsible for stopping it.

    The default owner is the RUNNING TEST, which is right for a server the test itself makes and
    wrong for anything that must outlive it. That distinction cost 36 failures and twenty minutes
    of runtime: the session-scoped gateway registered its cleanup with whichever test happened to
    trigger the fixture, was shut down when that test ended, and every later test waited thirty
    seconds for a server that was not there.

    So a fixture whose scope is wider than one test passes its OWN owner, and
    `test_serving_ownership.py` refuses to let a bare call appear inside one.
    """
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if owner is not None:
        owner.callback(stop, server, thread, state)
    else:
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
    # The service owns a bounded pool for cancellations. It is lazy -- a server that never
    # cancelled anything has nothing to close -- but a test that did cancel something would
    # otherwise leave its hands behind, and this is the boundary that catches that.
    service = getattr(server, "service", None) or getattr(type(server), "service", None)
    close = getattr(service, "close", None)
    if callable(close):
        try:
            close()
        except Exception:                                     # noqa: BLE001 - already closed
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


# ------------------------------------------------------- where a serving thread came from
#
# Counting threads says something is wrong and nothing about what. A count cannot be told apart
# from a fixture that is legitimately still serving, which is exactly the ambiguity that stopped
# the previous round concluding anything. So each serving thread carries where it was started,
# and the question at the end becomes "which line made this one" rather than "is eleven too many".

_REAL_START = threading.Thread.start

#: Thread targets worth remembering the birthplace of. Handler threads matter as much as serving
#: ones: a request thread still blocked after its server is gone is holding a socket.
SERVING_SHAPES = ("serve_forever", "process_request", "answer", "_answer")


def _is_interesting(thread) -> bool:
    target = getattr(thread, "_target", None)
    named = "%s %s" % (getattr(target, "__qualname__", "") or getattr(target, "__name__", ""),
                       thread.name)
    return any(shape in named for shape in SERVING_SHAPES)


def _remember_where_it_started(self):
    try:
        if _is_interesting(self):
            import os
            import traceback as _tb

            self._agentnode_born = (
                os.environ.get("PYTEST_CURRENT_TEST", "(outside a test)"),
                "".join(_tb.format_stack(limit=12)[:-1]),
            )
    except Exception:                                         # noqa: BLE001 - never break a start
        pass
    return _REAL_START(self)


def remember_births() -> None:
    """Installed once, from conftest. Idempotent."""
    if threading.Thread.start is not _remember_where_it_started:
        threading.Thread.start = _remember_where_it_started


def outstanding() -> list:
    """Serving and handler threads still alive. Asked AFTER every fixture has been torn down,
    where the answer is unambiguous: nothing owns these, so each one is a leak."""
    return [t for t in threading.enumerate()
            if t is not threading.main_thread()
            and (any(s in t.name for s in SERVING_SHAPES) or hasattr(t, "_agentnode_born"))]


def describe(threads) -> str:
    """Name, owner, birthplace and what it is doing now -- for each, not as a total."""
    import sys
    import traceback as _tb

    frames = sys._current_frames()
    out = []
    for t in threads:
        born_in, birthplace = getattr(t, "_agentnode_born", ("(not recorded)", ""))
        stack = "".join(_tb.format_stack(frames[t.ident])) if t.ident in frames else "(gone)"
        out.append(
            "\n--- %s  (daemon=%s, alive=%s)\n"
            "    started during: %s\n"
            "    started at:\n%s"
            "    doing now:\n%s" % (t.name, t.daemon, t.is_alive(), born_in,
                                    _indent(birthplace), _indent(stack)))
    return "".join(out)


def _indent(text: str) -> str:
    return "".join("      " + line + "\n" for line in (text or "").splitlines())


# ------------------------------------------------------------------ the worker's own socket
#
# The gateway is not the only thing in this product that serves. The worker's `Bench` listens on a
# unix socket in its own `serve_forever`, and the late measurement found six of those still
# blocked in `accept()` after the session had ended -- one per test that opened a bench, each
# holding a listening socket, none of them owned. `stop_serving()` has always existed and clears
# the serving flag BEFORE closing the socket, so `accept()` raises and the loop returns rather
# than spinning. Nobody was calling it.


def owned_bench(bench):
    """Start a worker bench serving, with the running test responsible for stopping it."""
    thread = threading.Thread(target=bench.serve_forever, daemon=True)
    thread.start()
    if _owner is not None:
        _owner.callback(stop_bench, bench, thread)
    return thread


def stop_bench(bench, thread=None, seconds: float = PATIENCE) -> None:
    """Give a bench back: stop serving, then wait -- with a deadline -- for the loop to notice."""
    try:
        bench.stop_serving()
    except Exception:                                         # noqa: BLE001 - already stopped
        pass
    if thread is not None:
        thread.join(timeout=seconds)
