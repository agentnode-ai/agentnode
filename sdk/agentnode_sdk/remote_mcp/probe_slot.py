"""A probe's run slot from the serving process's own scheduler (attest-renewal D6, C11, C20).

Every call limit lives in ONE in-memory `Scheduler` inside `rmcp-control` (target-router T12). A renewal
probe runs in a different process (`targets_cli`, root, from a timer), so without this it would run
outside every limit. This is the one door between the two: a root-only Unix socket served on the control
process's own event loop.

  * the runtime directory is root's own, a real directory (not a symlink), 0700; the socket is 0600;
  * every connection's peer credentials (SO_PEERCRED) must be UID 0 -- anything else, or a platform that
    cannot say, is refused;
  * ONE request per connection, at most 256 bytes, read within 5 s: `{"acquire": "<target id>"}`;
  * the request goes through `Scheduler.acquire` exactly as a call does -- one global AND one target
    running slot, the target's FIFO queue, round-robin with the others, the same refusals
    (`target_full`, `overloaded`, `queue_wait_exceeded`) -- under a SYNTHETIC account
    (`agentnode-probe:<target id>`) that no real account id can equal, so a probe never counts against a
    customer's allowance and never jumps a queue;
  * the grant is held until the client closes the connection, and released exactly once on EOF, on any
    further byte (a second request is a protocol violation), on a malformed request, on cancellation and
    on server shutdown -- so a probe client that dies frees its slot at once;
  * a socket pathname already present is replaced only after proving no live listener answers on it.

There is no TCP variant, no configuration that makes one, and no other operation.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import struct
import time
from typing import Callable

from agentnode_sdk.remote_mcp.scheduler import SchedulerRefused

PROBE_SLOT_SOCKET = "/run/agentnode-mcp/probe-slot.sock"
PROBE_ACCOUNT_PREFIX = "agentnode-probe:"
MAX_REQUEST_BYTES = 256
READ_DEADLINE_SECONDS = 5.0
WRITE_DEADLINE_SECONDS = 5.0
#: The refusals a client waits out and asks again for (each time through the normal queue, no priority).
RETRYABLE = frozenset({"target_full", "overloaded", "queue_wait_exceeded", "too_many_concurrent_calls"})
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


class SlotServiceRefused(Exception):
    """The slot service will not start here (unsafe directory, a live listener on the path)."""


class SlotRefused(Exception):
    """No slot: `code` is the scheduler's refusal, or `control_not_serving` / `protocol` / `peer`."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


def _valid_target(value) -> bool:
    return isinstance(value, str) and 3 <= len(value) <= 64 and set(value) <= _ID_CHARS


def peer_uid(sock) -> int | None:
    """The connecting process's UID from SO_PEERCRED, or None where the platform cannot say."""
    opt = getattr(socket, "SO_PEERCRED", None)
    if opt is None or sock is None:
        return None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, opt, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        return uid
    except OSError:
        return None


def prepare_runtime_dir(path: str, *, owner_uid: int) -> None:
    """The socket's directory: created 0700 if absent; else it must be a real directory owned by
    `owner_uid` (a symlink, another owner or a group/other-accessible mode is refused)."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise SlotServiceRefused("%s is not a directory (a symlink is refused)" % path)
    if st.st_uid != owner_uid:
        raise SlotServiceRefused("%s is owned by uid %d, not %d" % (path, st.st_uid, owner_uid))
    if st.st_mode & 0o077:
        raise SlotServiceRefused("%s is accessible to group/other (mode %o)" % (path, st.st_mode & 0o777))


def clear_stale_socket(path: str) -> None:
    """Remove a leftover socket pathname, but only a SOCKET that no live listener answers on."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise SlotServiceRefused("%s exists and is not a socket" % path)
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(path)
    except (ConnectionRefusedError, FileNotFoundError):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    finally:
        probe.close()
    raise SlotServiceRefused("a live listener owns %s" % path)


class ProbeSlotServer:
    """Serves probe slots from `scheduler` on `path`. `limits_of(target_id)` -> (concurrency, queue) of
    an ACTIVE register target, or None (refused `target_unknown`); it may block (it reads the register),
    so it runs off the event loop."""

    def __init__(self, scheduler, limits_of: Callable[[str], "tuple[int, int] | None"], path: str = PROBE_SLOT_SOCKET,
                 *, peer_uid_required: int = 0, uid_of: Callable = peer_uid):
        self.scheduler = scheduler
        self.limits_of = limits_of
        self.path = path
        self.peer_uid_required = peer_uid_required
        self._uid_of = uid_of
        self._server = None
        self._inode = None
        self._tasks: set = set()
        self.granted = 0                       # introspection for tests and the audit of a run
        self.released = 0

    async def start(self) -> None:
        if os.name != "posix" or not hasattr(asyncio, "start_unix_server"):
            raise SlotServiceRefused("this platform has no Unix sockets")
        prepare_runtime_dir(os.path.dirname(self.path), owner_uid=os.geteuid())
        clear_stale_socket(self.path)
        old = os.umask(0o177)                  # the socket is created 0600, never briefly wider
        try:
            self._server = await asyncio.start_unix_server(self._handle, path=self.path, limit=MAX_REQUEST_BYTES)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        self._inode = os.lstat(self.path).st_ino

    async def stop(self) -> None:
        # stop accepting, then end every held connection (its grant is released in its handler), and only
        # then wait for the server: on Python >= 3.12 `wait_closed` waits for open connections too
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        try:
            if self._inode is not None and os.lstat(self.path).st_ino == self._inode:
                os.unlink(self.path)
        except FileNotFoundError:
            pass

    async def _reply(self, writer, doc: dict) -> None:
        writer.write((json.dumps(doc, sort_keys=True) + "\n").encode("ascii"))
        await asyncio.wait_for(writer.drain(), WRITE_DEADLINE_SECONDS)

    async def _handle(self, reader, writer) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        grant = None
        try:
            if self._uid_of(writer.get_extra_info("socket")) != self.peer_uid_required:
                await self._reply(writer, {"refused": "peer"})
                return
            try:
                line = await asyncio.wait_for(reader.readuntil(b"\n"), READ_DEADLINE_SECONDS)
                req = json.loads(line.decode("ascii"))
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                    ValueError, UnicodeDecodeError):
                await self._reply(writer, {"refused": "protocol"})
                return
            if not isinstance(req, dict) or set(req) != {"acquire"} or not _valid_target(req["acquire"]):
                await self._reply(writer, {"refused": "protocol"})
                return
            target_id = req["acquire"]
            loop = asyncio.get_running_loop()
            limits = await loop.run_in_executor(None, self.limits_of, target_id)
            if limits is None:
                await self._reply(writer, {"refused": "target_unknown"})
                return
            concurrency, queue = limits
            try:
                grant = await self.scheduler.acquire(PROBE_ACCOUNT_PREFIX + target_id, target_id,
                                                     concurrency=concurrency, queue=queue)
            except SchedulerRefused as refused:
                await self._reply(writer, {"refused": refused.code})
                return
            self.granted += 1
            await self._reply(writer, {"granted": target_id})
            # held until the client goes away; ANY further byte is a second request: refused, released
            extra = await reader.read(1)
            if extra:
                try:
                    await self._reply(writer, {"refused": "protocol"})
                except Exception:                              # noqa: BLE001 - the slot is released below
                    pass
        except (ConnectionError, asyncio.TimeoutError, OSError):
            pass
        finally:
            if grant is not None:
                grant.release()
                self.released += 1
            try:
                writer.close()
            except Exception:                                  # noqa: BLE001
                pass
            self._tasks.discard(task)


class ProbeSlot:
    """A held slot: the open connection. `release()` (or leaving the `with`) gives it back."""

    def __init__(self, sock: socket.socket, target_id: str, waited: float, attempts: int):
        self._sock = sock
        self.target_id = target_id
        self.waited = waited
        self.attempts = attempts

    def still_held(self) -> bool:
        """True only while the connection is open and SILENT: the server closing it (control restarted) or
        saying anything after the grant (a refusal) both mean the slot is gone -- the probe's result is void."""
        try:
            self._sock.setblocking(False)
            try:
                self._sock.recv(1, socket.MSG_PEEK)
            except BlockingIOError:
                return True                                   # nothing to read: still held
            return False                                      # EOF, or an answer after the grant
        except OSError:
            return False
        finally:
            try:
                self._sock.setblocking(True)
            except OSError:
                pass

    def release(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def acquire(target_id: str, *, path: str = PROBE_SLOT_SOCKET, budget: float = 600.0,
            clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> ProbeSlot:
    """Ask the serving process for a slot; wait out the retryable refusals with a bounded backoff
    (every attempt goes through the queue anew, no priority), at most `budget` seconds in all."""
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        raise SlotRefused("control_not_serving", "no Unix sockets on this platform")
    start = clock()
    backoff, attempts = 2.0, 0
    while True:
        attempts += 1
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(30.0)
        try:
            s.connect(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            s.close()
            raise SlotRefused("control_not_serving", type(exc).__name__) from exc
        try:
            s.sendall((json.dumps({"acquire": target_id}) + "\n").encode("ascii"))
            buf = b""
            while not buf.endswith(b"\n") and len(buf) < 512:
                chunk = s.recv(512)
                if not chunk:
                    break
                buf += chunk
            reply = json.loads(buf.decode("ascii")) if buf else {}
        except (OSError, ValueError) as exc:
            s.close()
            raise SlotRefused("protocol", type(exc).__name__) from exc
        if reply.get("granted") == target_id:
            s.settimeout(None)
            return ProbeSlot(s, target_id, round(clock() - start, 3), attempts)
        s.close()
        code = str(reply.get("refused") or "protocol")
        left = budget - (clock() - start)
        if code in RETRYABLE and left > 0:
            sleep(min(backoff, left))
            backoff = min(backoff * 2, 30.0)
            continue
        raise SlotRefused(code)


__all__ = ["MAX_REQUEST_BYTES", "PROBE_ACCOUNT_PREFIX", "PROBE_SLOT_SOCKET", "ProbeSlot", "ProbeSlotServer",
           "RETRYABLE", "SlotRefused", "SlotServiceRefused", "acquire", "clear_stale_socket", "peer_uid",
           "prepare_runtime_dir"]
