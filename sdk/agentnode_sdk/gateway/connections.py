"""Where a client keeps the gateways it has paired with.

A pairing produces a long-lived token. It is the whole credential -- anyone holding it can submit
work to that gateway and read the results -- so where it is written down matters as much as how it
was obtained.

Three rules, and each exists because the obvious alternative is worse:

* **One file, owner-readable only.** Created with mode 0600 *before* anything is written to it,
  not chmod-ed afterwards: a file that is briefly world-readable while it contains a token has
  already leaked it on a shared machine.
* **Never in the environment, never in a URL, never on a command line.** All three are visible to
  other processes, get captured in shell history, and end up in crash reports.
* **Written atomically.** A half-written connection file is one that has lost a token the user
  cannot recover without re-pairing at the machine.

The file is deliberately not encrypted. Encrypting it would need a key, and a key kept beside the
thing it protects is decoration -- it would let this docstring claim more than the design delivers.
What protects the file is the filesystem, and saying so plainly is better than implying more.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path


def default_path() -> Path:
    """`~/.agentnode/gateways.json`, or wherever AGENTNODE_HOME points."""
    home = os.environ.get("AGENTNODE_HOME")
    root = Path(home) if home else (Path.home() / ".agentnode")
    return root / "gateways.json"


@dataclass(frozen=True)
class SavedGateway:
    """One paired gateway, as this machine remembers it."""

    name: str
    url: str
    token: str
    gateway_id: str = ""
    fingerprint: str = ""

    def redacted(self) -> dict:
        """Everything except the credential. This is what may be printed or logged."""
        return {
            "name": self.name,
            "url": self.url,
            "gateway_id": self.gateway_id,
            "fingerprint": self.fingerprint,
        }


class ConnectionStore:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else default_path()

    # ------------------------------------------------------------------ reading

    def _read(self) -> dict:
        if not self.path.is_file():
            return {"gateways": {}, "default": ""}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"gateways": {}, "default": ""}
        if not isinstance(loaded, dict):
            return {"gateways": {}, "default": ""}
        return {
            "gateways": dict(loaded.get("gateways") or {}),
            "default": str(loaded.get("default") or ""),
        }

    def names(self) -> list[str]:
        return sorted(self._read()["gateways"])

    def get(self, name: str = "") -> SavedGateway | None:
        data = self._read()
        wanted = name or data["default"]
        if not wanted and len(data["gateways"]) == 1:
            wanted = next(iter(data["gateways"]))
        entry = data["gateways"].get(wanted)
        if not entry:
            return None
        return SavedGateway(
            name=wanted,
            url=str(entry.get("url", "")),
            token=str(entry.get("token", "")),
            gateway_id=str(entry.get("gateway_id", "")),
            fingerprint=str(entry.get("fingerprint", "")),
        )

    def default_name(self) -> str:
        data = self._read()
        if data["default"]:
            return data["default"]
        return next(iter(sorted(data["gateways"])), "")

    # ------------------------------------------------------------------ writing

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, stat.S_IRWXU)
        except OSError:
            pass
        # The file is created 0600 by mkstemp and stays that way through the rename, so it is
        # never briefly readable by anyone else while it holds a token.
        handle, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".gateways-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def save(self, saved: SavedGateway, make_default: bool = True) -> None:
        data = self._read()
        data["gateways"][saved.name] = {
            "url": saved.url,
            "token": saved.token,
            "gateway_id": saved.gateway_id,
            "fingerprint": saved.fingerprint,
        }
        if make_default or not data["default"]:
            data["default"] = saved.name
        self._write(data)

    def set_default(self, name: str) -> bool:
        data = self._read()
        if name not in data["gateways"]:
            return False
        data["default"] = name
        self._write(data)
        return True

    def forget(self, name: str) -> bool:
        data = self._read()
        if name not in data["gateways"]:
            return False
        del data["gateways"][name]
        if data["default"] == name:
            data["default"] = next(iter(sorted(data["gateways"])), "")
        self._write(data)
        return True

    def mode_is_private(self) -> bool | None:
        """Whether the file is owner-only. None where the platform cannot say.

        On Windows the POSIX mode is largely advisory, and reporting a confident yes there would
        be claiming a guarantee the platform does not give.
        """
        if not self.path.is_file():
            return None
        if os.name != "posix":
            return None
        return (self.path.stat().st_mode & 0o077) == 0
