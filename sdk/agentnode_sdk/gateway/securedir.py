"""Reading and writing the gateway's secrets without going through a name twice.

A pathname is not an object. Between the moment a path is checked and the moment it is opened, the
name can be made to refer to something else -- a symlink pointing elsewhere, a directory renamed
out from under the process, a file replaced by a link to somebody else's. Every check that is
followed by a separate open is therefore a check of one thing and a use of another, and
`EM3C-EXTERNAL-0007` found the pairing claim doing exactly that: renaming, reading and unlinking
the live pairing record by name.

So nothing here works from a name. A directory is opened once, the *descriptor* is judged, and
every subsequent operation happens relative to that descriptor. The name of the directory can be
changed, moved or replaced afterwards and it makes no difference: the descriptor still refers to
the inode that was inspected.

Four things are checked, and each rules out a specific way of being handed the wrong object:

* **type** -- a directory is a directory and a secret is a regular file. A fifo or a device in
  place of the tokens file would otherwise be opened and read from.
* **owner** -- it belongs to the user running this. Something owned by someone else is something
  someone else can change.
* **mode** -- nobody else can read it. This is the property the whole directory exists to have.
* **link count** -- exactly one name refers to this file. A second hard link is a second door into
  the same bytes, in a directory whose permissions say nothing about this one.

`O_NOFOLLOW` closes the last gap: the final component may not be a symlink, so a name inside the
verified directory cannot redirect the open somewhere outside it.

## Where this cannot be done

`dir_fd` is a POSIX facility; Windows has no equivalent in the standard library, and reparse points
would need to be handled through APIs that are not available here. On Windows these functions
refuse rather than falling back to pathname operations that would look the same and protect
nothing. What that means for a gateway is stated where it is decided, not here.
"""
from __future__ import annotations

import os
import stat

#: Whether the descriptor-relative facilities this module needs exist at all.
SUPPORTED = (
    os.name == "posix"
    and {os.open, os.rename, os.unlink, os.replace} <= set(os.supports_dir_fd)
)


class UnverifiableState(Exception):
    """The state could not be verified, so nothing was read or written."""


class InsecureState(Exception):
    """The state was verified and is not private. Nothing was read or written."""


def _require_supported() -> None:
    if not SUPPORTED:
        raise UnverifiableState(
            "this platform cannot open files relative to a verified directory, so the gateway's "
            "secrets cannot be protected against a path being changed underneath it. A gateway "
            "needs a system where that is possible."
        )


def _judge(info, what: str, *, expect_dir: bool) -> None:
    if expect_dir and not stat.S_ISDIR(info.st_mode):
        raise InsecureState(f"{what} is not a directory")
    if not expect_dir and not stat.S_ISREG(info.st_mode):
        raise InsecureState(
            f"{what} is not an ordinary file. Something else in its place -- a link, a pipe, a "
            "device -- would be read as though it were the gateway's own."
        )
    if info.st_uid != os.getuid():
        raise InsecureState(
            f"{what} belongs to another user, who can therefore change it at any moment"
        )
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise InsecureState(
            f"{what} can be read by other accounts on this machine "
            f"(mode {oct(stat.S_IMODE(info.st_mode))})"
        )
    if not expect_dir and info.st_nlink != 1:
        raise InsecureState(
            f"{what} has {info.st_nlink} names. A second hard link is a second door into the same "
            "bytes, in a place whose permissions say nothing about this one."
        )


def open_state_dir(root) -> int:
    """Open the state directory and judge the descriptor. The caller closes it."""
    _require_supported()
    try:
        fd = os.open(str(root), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise UnverifiableState(f"the gateway's directory could not be opened: {exc}") from exc
    try:
        _judge(os.fstat(fd), f"the gateway's directory ({root})", expect_dir=True)
    except BaseException:
        os.close(fd)
        raise
    return fd


def verify_still_private(fd: int, root) -> None:
    """Re-judge a held descriptor, immediately before it is used.

    The descriptor keeps referring to the same inode however the name is manipulated, but the
    inode's own owner and mode can still be changed. This is what turns "it was private when the
    gateway started" into "it is private now".
    """
    _judge(os.fstat(fd), f"the gateway's directory ({root})", expect_dir=True)


def same_object(fd: int, root) -> bool:
    """Whether the name still refers to the object this descriptor holds.

    Compared by device and inode, not by string: two different paths can name one directory and
    one path can name two directories at different moments, so the name is not the thing.
    """
    try:
        by_name = os.stat(str(root))
    except OSError:
        return False
    held = os.fstat(fd)
    return (by_name.st_dev, by_name.st_ino) == (held.st_dev, held.st_ino)


def read_secret(fd: int, name: str) -> str | None:
    """Read a file inside the verified directory. None when it is not there."""
    _require_supported()
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        # ELOOP lands here when the name is a symlink: refused rather than followed.
        raise UnverifiableState(f"{name} could not be opened safely: {exc}") from exc
    try:
        _judge(os.fstat(handle), name, expect_dir=False)
        with os.fdopen(os.dup(handle), "r", encoding="utf-8") as reader:
            return reader.read()
    finally:
        os.close(handle)


def write_secret(fd: int, name: str, text: str) -> None:
    """Replace a file inside the verified directory, atomically and owner-only."""
    _require_supported()
    tmp = "." + name + ".new"
    try:
        os.unlink(tmp, dir_fd=fd)
    except FileNotFoundError:
        pass
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(os.dup(handle), "w", encoding="utf-8") as writer:
            writer.write(text)
    finally:
        os.close(handle)
    os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)


def claim_secret(fd: int, name: str, into: str) -> bool:
    """Rename one name to another inside the verified directory. True if this caller won.

    The rename is the claim: exactly one caller can move a given name, so this is how a
    single-use secret is taken without a window in which two callers both hold it.
    """
    _require_supported()
    try:
        os.rename(name, into, src_dir_fd=fd, dst_dir_fd=fd)
        return True
    except OSError:
        return False


def remove_secret(fd: int, name: str) -> None:
    _require_supported()
    try:
        os.unlink(name, dir_fd=fd)
    except OSError:
        pass


def exists(fd: int, name: str) -> bool:
    _require_supported()
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
        return True
    except OSError:
        return False
