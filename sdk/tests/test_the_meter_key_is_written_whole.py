"""A key written in place can be read in half, and was.

`test_what_is_kept.py` failed in a full suite and passed alone, three runs in a row, each time on
a different test of the same class:

    ValueError: Unable to load PEM file ... MalformedFraming
        path = .../state/meter-key.pem

Not a leak and not the pin. `meter.signing_key` does `if path.exists(): load`, and
`save_signing_key` wrote the PEM in place -- so a second caller arriving between "the file
exists" and "the last byte is in it" loaded a truncated key. The same write also created the
private key with the umask, so it existed at 0644 until a `chmod` ran a moment later.

These are the tests for the fix. They are about the FILE, not about the race being hard to
provoke: a race that is only sometimes visible is still a defect all the time.
"""
from __future__ import annotations

import stat
import sys
import threading

import pytest

from agentnode_sdk.gateway import meter


def _make(root):
    return meter.signing_key(root).private_bytes_raw()


class TestOneKeyEvenWhenEverybodyAsksAtOnce:

    def test_twelve_callers_on_an_empty_directory_get_one_key_and_no_error(self, tmp_path):
        """The observed failure, provoked on purpose. Before the fix this raises
        `ValueError: MalformedFraming` from whichever thread read the half-written file."""
        got, failed = [], []

        def ask():
            try:
                got.append(_make(tmp_path))
            except Exception as exc:                          # noqa: BLE001
                failed.append(exc)

        threads = [threading.Thread(target=ask) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not failed, "a caller could not read the key: %r" % (failed[:2],)
        assert len(got) == 12
        assert len(set(got)) == 1, "the gateway ended up with more than one meter key"

    def test_and_the_key_on_disk_reads_back_whole(self, tmp_path):
        first = _make(tmp_path)
        assert _make(tmp_path) == first


class TestThePrivateKeyIsNeverReadableByAnybodyElse:

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
    def test_it_is_0600_and_not_only_after_a_chmod(self, tmp_path):
        """0600 comes from how the file is CREATED. The earlier version created it with the
        umask and narrowed it afterwards, so there was a window -- short, and a window."""
        _make(tmp_path)
        mode = stat.S_IMODE((tmp_path / meter.METER_KEY_NAME).stat().st_mode)
        assert mode == 0o600, "the meter key is mode %o" % mode

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
    def test_and_no_half_written_copy_is_left_beside_it(self, tmp_path):
        _make(tmp_path)
        leftovers = [p.name for p in tmp_path.iterdir()
                     if p.name.startswith(".") and meter.METER_KEY_NAME in p.name]
        assert not leftovers, "a temporary copy of the private key was left behind: %s" % leftovers
