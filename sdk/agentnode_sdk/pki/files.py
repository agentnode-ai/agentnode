"""Every file operation the issuer makes, through one seam -- so that a crash can be decided.

Decision 5.0: act on a written state only once it is durable. Decision 6: a crash between a rename
and the fsync of its directory has two permitted outcomes -- the rename survived, or it was lost --
and a real crash picks one by chance. A test that leaves the choice to chance passes a wrong
implementation whenever the lucky outcome comes up. So nothing in `issuer.py` touches the disk
directly. It goes through `Files`, and in a test `RecordingFiles` stands in and can end a
simulated crash in either outcome ON PURPOSE:

    LOST      every directory operation since that directory's last fsync is undone, and every
              file whose contents were written but not fsynced is cut off at half its length
    SURVIVED  everything written is still there, fsynced or not

`durable_replace` is the only way a file is committed: temporary file, fsync of the file, rename,
fsync of the directory. The commit is the directory fsync returning, not the rename.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

LOST = "lost"
SURVIVED = "survived"
OUTCOMES = (LOST, SURVIVED)


class SimulatedCrash(BaseException):
    """The machine went down, in a test. A BaseException so that no `except Exception` in the
    code under test can swallow it and carry on as if it had not happened."""


class Files:
    """The real thing. Plain `os` calls, with the fsyncs a commit needs."""

    def point(self, name: str) -> None:
        """A named place between two steps. Nothing happens here outside a test."""

    def read(self, path) -> bytes:
        return Path(path).read_bytes()

    def exists(self, path) -> bool:
        return Path(path).exists()

    def listdir(self, directory) -> list[str]:
        return sorted(os.listdir(directory))

    def write_new(self, path, data: bytes, mode: int = 0o600) -> None:
        """Create a file that must not exist yet, and write it. Not durable until `fsync_file`
        and, for the name, until its directory is fsynced."""
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                     mode)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.chmod(str(path), mode)

    def overwrite(self, path, data: bytes) -> None:
        """Rewrite an existing file in place. NOTHING in the issuer does this -- a commit is
        always `durable_replace`. It is here so that a counter-check which replaces the commit
        with an in-place write still goes through the seam, and the fault model can show what a
        crash does to it."""
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.ftruncate(fd, 0)
            os.write(fd, data)
        finally:
            os.close(fd)

    def fsync_file(self, path) -> None:
        if os.name == "nt":                                   # pragma: no cover - not a server
            # Windows flushes only a handle opened for writing, and cannot open a read-only
            # file that way. Same reason as `fsync_dir`: the unit tests run here, the issuer
            # does not, and the crash cases are judged by the fault model.
            try:
                fd = os.open(str(path), os.O_RDWR | os.O_BINARY)
            except PermissionError:
                return
        else:
            fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def rename(self, source, target) -> None:
        os.replace(str(source), str(target))

    def fsync_dir(self, directory) -> None:
        if os.name == "nt":                                   # pragma: no cover - not a server
            # Windows cannot open a directory for fsync. The issuer runs on the Linux host; the
            # Windows path exists so the unit tests run on a developer's machine, where the
            # fault model -- not this call -- is what the crash cases are judged by.
            return
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def remove(self, path) -> None:
        os.unlink(str(path))


def durable_replace(files: Files, target, data: bytes, *, mode: int = 0o600,
                    temporary: str | None = None, label: str = "write") -> None:
    """Commit `data` at `target`: temporary file, fsync, rename, fsync of the directory.

    Four points are named so a test can crash between any two steps. If a step raises, the caller
    is told; after a successful rename a failed directory fsync is NOT a rollback -- the new name
    may or may not survive a crash, and the caller must treat the outcome as indeterminate
    (decision 3.3).
    """
    target = Path(target)
    directory = target.parent
    tmp = directory / (temporary or ("." + target.name + ".tmp"))
    files.write_new(tmp, data, mode)
    files.point(label + ":written")
    files.fsync_file(tmp)
    files.point(label + ":before-rename")
    files.rename(tmp, target)
    files.point(label + ":after-rename")
    files.fsync_dir(directory)
    files.point(label + ":after-dirsync")


class RecordingFiles(Files):
    """A test's stand-in: real files on disk, plus a journal of what a crash could take away.

    Arm it with `crash_at(point)`; the next time the code under test passes that point it raises
    `SimulatedCrash`. Then call `settle(outcome)` to leave the directory exactly as a machine that
    went down there would have left it, in the chosen outcome.
    """

    def __init__(self) -> None:
        self._armed: str | None = None
        #: Per directory, the operations since its last fsync, oldest first.
        self._journal: dict[str, list[tuple]] = {}
        #: Files whose contents were written and not yet fsynced.
        self._unsynced: set[str] = set()
        self.passed: list[str] = []
        self.failures: dict[str, BaseException] = {}

    # -- arming ------------------------------------------------------------------------------

    def crash_at(self, point: str) -> None:
        self._armed = point

    def fail_at(self, operation: str, error: BaseException) -> None:
        """Make one kind of call raise instead -- `fsync_dir`, say -- to model an I/O failure."""
        self.failures[operation] = error

    def fail_after(self, point: str, operation: str, error: BaseException) -> None:
        """Make `operation` raise only when the last point passed was `point` -- the directory
        fsync right after a rename, say, and not the one every call begins with. Once."""
        self._fail_after = (point, operation, error)

    def _maybe_fail(self, operation: str) -> None:
        if operation in self.failures:
            raise self.failures[operation]
        planned = getattr(self, "_fail_after", None)
        if planned and planned[1] == operation and self.passed and self.passed[-1] == planned[0]:
            self._fail_after = None
            raise planned[2]

    def point(self, name: str) -> None:
        self.passed.append(name)
        if self._armed == name:
            self._armed = None
            raise SimulatedCrash(name)

    # -- journalled operations ----------------------------------------------------------------

    def _note(self, directory, entry: tuple) -> None:
        self._journal.setdefault(str(Path(directory)), []).append(entry)

    @staticmethod
    def _snapshot(path) -> tuple[bytes, int] | None:
        p = Path(path)
        if not p.exists():
            return None
        return p.read_bytes(), stat.S_IMODE(p.stat().st_mode)

    def write_new(self, path, data: bytes, mode: int = 0o600) -> None:
        super().write_new(path, data, mode)
        self._note(Path(path).parent, ("create", str(path)))
        self._unsynced.add(str(path))

    def overwrite(self, path, data: bytes) -> None:
        existed = Path(path).exists()
        super().overwrite(path, data)
        if not existed:
            self._note(Path(path).parent, ("create", str(path)))
        self._unsynced.add(str(path))

    def fsync_file(self, path) -> None:
        self._maybe_fail("fsync_file")
        super().fsync_file(path)
        self._unsynced.discard(str(path))

    def rename(self, source, target) -> None:
        self._maybe_fail("rename")
        before = self._snapshot(target)
        moving = self._snapshot(source)
        super().rename(source, target)
        self._note(Path(target).parent, ("rename", str(source), str(target), before, moving))
        if str(source) in self._unsynced:
            self._unsynced.discard(str(source))
            self._unsynced.add(str(target))

    def fsync_dir(self, directory) -> None:
        self._maybe_fail("fsync_dir")
        super().fsync_dir(directory)
        self._journal.pop(str(Path(directory)), None)

    def remove(self, path) -> None:
        before = self._snapshot(path)
        super().remove(path)
        self._note(Path(path).parent, ("remove", str(path), before))
        self._unsynced.discard(str(path))

    # -- the crash ---------------------------------------------------------------------------

    @staticmethod
    def _put(path: str, snapshot) -> None:
        p = Path(path)
        if snapshot is None:
            if p.exists():
                p.unlink()
            return
        data, mode = snapshot
        p.write_bytes(data)
        os.chmod(path, mode)

    def settle(self, outcome: str) -> None:
        """Leave the disk as a machine that crashed now would have, in the given outcome."""
        if outcome not in OUTCOMES:
            raise ValueError(outcome)
        if outcome == LOST:
            # Directory operations first, newest first, carrying the "contents not yet fsynced"
            # mark back with the name it belonged to -- then the tearing, on whatever still
            # holds unsynced contents. The other order would cut off a file a rename had put
            # back, which is a state no crash produces.
            for directory, entries in self._journal.items():
                for entry in reversed(entries):
                    if entry[0] == "create":
                        self._put(entry[1], None)
                        self._unsynced.discard(entry[1])
                    elif entry[0] == "rename":
                        _, source, target, before, moving = entry
                        self._put(target, before)
                        self._put(source, moving)
                        if target in self._unsynced:
                            self._unsynced.discard(target)
                            self._unsynced.add(source)
                    elif entry[0] == "remove":
                        self._put(entry[1], entry[2])
            for path in sorted(self._unsynced):
                p = Path(path)
                if p.exists():
                    data = p.read_bytes()
                    p.write_bytes(data[: len(data) // 2])
        self._journal.clear()
        self._unsynced.clear()
        self._armed = None


__all__ = ["Files", "LOST", "OUTCOMES", "RecordingFiles", "SURVIVED", "SimulatedCrash",
           "durable_replace"]
