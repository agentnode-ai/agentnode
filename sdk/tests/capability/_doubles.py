"""Test doubles for capability tests (clearly not production)."""
from __future__ import annotations

import subprocess
import sys

from agentnode_sdk.worker import Isolation, Outcome


class FakeWorker:
    """Runs the accepted bootstrap command locally (NOT a container). A test double."""

    def __init__(self, *, available=True, backend="fake-sandbox", raises=None):
        self._iso = Isolation(available=available, backend=backend)
        self._raises = raises

    def can_it_isolate(self):
        return self._iso

    def run(self, job):
        if self._raises is not None:
            raise self._raises
        stub = job.command[2]
        proc = subprocess.run([sys.executable, "-c", stub], input=job.stdin,
                              text=True, capture_output=True, timeout=60)
        return Outcome(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
