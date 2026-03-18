"""Verification sandbox — venv creation, subprocess execution, cleanup.

Network policy:
- pip install: network allowed (needs to fetch dependencies)
- import check: no network needed (offline import only)
- smoke test: runs with AGENTNODE_VERIFY_RESTRICTED=1 env hint

IMPORTANT: Network restriction is best-effort via environment variable, NOT hard
isolation. A malicious package can ignore the flag and still make network requests.
Real sandboxing (seccomp, nsjail, bubblewrap, containers) is planned for a future
version. The security scan (trust/scanner.py) handles detection of malicious network
access separately.
"""

from __future__ import annotations

import io
import logging
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile

from app.config import settings

logger = logging.getLogger(__name__)

MAX_LOG_BYTES = 10_240  # 10 KB log cap


def _truncate_log(text: str) -> str:
    """Truncate log output to MAX_LOG_BYTES."""
    if len(text) > MAX_LOG_BYTES:
        return text[:MAX_LOG_BYTES] + "\n... (truncated)"
    return text


class VerificationSandbox:
    """Ephemeral venv sandbox for package verification."""

    def __init__(self) -> None:
        self.work_dir: str = tempfile.mkdtemp(prefix="agentnode_verify_")
        self.pkg_dir: str = ""
        self.venv_dir: str = os.path.join(self.work_dir, ".venv")
        if platform.system() == "Windows":
            self.python: str = os.path.join(self.venv_dir, "Scripts", "python.exe")
        else:
            self.python: str = os.path.join(self.venv_dir, "bin", "python")

    def extract_artifact(self, artifact_bytes: bytes) -> bool:
        """Extract tar.gz artifact into work_dir. Returns success."""
        try:
            with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
                for member in tar.getmembers():
                    if os.path.normpath(member.name).startswith("..") or os.path.isabs(member.name):
                        continue
                tar.extractall(self.work_dir, filter="data")

            # Find the package root (directory with setup.py/pyproject.toml)
            for root, dirs, files in os.walk(self.work_dir):
                if "pyproject.toml" in files or "setup.py" in files or "setup.cfg" in files:
                    self.pkg_dir = root
                    return True

            # Fallback: use work_dir itself
            self.pkg_dir = self.work_dir
            return True
        except Exception as e:
            logger.warning(f"Artifact extraction failed: {e}")
            return False

    def create_venv(self) -> tuple[bool, str]:
        """Create a Python venv. Returns (success, log)."""
        try:
            result = subprocess.run(
                ["python3", "-m", "venv", self.venv_dir],
                capture_output=True,
                text=True,
                timeout=30,
            )
            log = (result.stdout + "\n" + result.stderr).strip()
            return result.returncode == 0, _truncate_log(log)
        except subprocess.TimeoutExpired:
            return False, "venv creation timed out (30s)"
        except Exception as e:
            return False, str(e)

    def pip_install(self) -> tuple[bool, str]:
        """Install the package into the venv. Returns (success, log).

        Network access is allowed for pip (needs to fetch dependencies).
        """
        timeout = settings.VERIFICATION_PIP_TIMEOUT
        try:
            result = subprocess.run(
                [self.python, "-m", "pip", "install", "--no-cache-dir", "."],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.pkg_dir,
            )
            log = (result.stdout + "\n" + result.stderr).strip()
            return result.returncode == 0, _truncate_log(log)
        except subprocess.TimeoutExpired:
            return False, f"pip install timed out ({timeout}s)"
        except Exception as e:
            return False, str(e)

    def run_python_code(
        self, code: str, timeout: int = 15, restrict_network: bool = False,
    ) -> tuple[bool, str]:
        """Run a Python script inside the venv. Returns (success, log).

        When restrict_network=True, sets AGENTNODE_VERIFY_RESTRICTED=1 in the
        subprocess environment as a hint that network access should be avoided.
        """
        try:
            env = os.environ.copy()
            if restrict_network:
                env["AGENTNODE_VERIFY_RESTRICTED"] = "1"

            result = subprocess.run(
                [self.python, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.pkg_dir,
                env=env,
            )
            log = (result.stdout + "\n" + result.stderr).strip()
            return result.returncode == 0, _truncate_log(log)
        except subprocess.TimeoutExpired:
            return False, f"Execution timed out ({timeout}s)"
        except Exception as e:
            return False, str(e)

    def run_pytest(self) -> tuple[bool, str]:
        """Run pytest inside the venv. Returns (success, log)."""
        # First install pytest
        try:
            subprocess.run(
                [self.python, "-m", "pip", "install", "--no-cache-dir", "pytest"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            return False, "Failed to install pytest"

        try:
            # Detect test directory
            test_dir = None
            for candidate in ["tests", "test"]:
                full = os.path.join(self.pkg_dir, candidate)
                if os.path.isdir(full):
                    test_dir = candidate
                    break

            if not test_dir:
                return False, "No tests/ or test/ directory found"

            # Set AGENTNODE_VERIFICATION=1 so publishers can skip
            # integration tests with: @pytest.mark.skipif(os.getenv("AGENTNODE_VERIFICATION"))
            env = os.environ.copy()
            env["AGENTNODE_VERIFICATION"] = "1"

            result = subprocess.run(
                [self.python, "-m", "pytest", test_dir, "-v", "--tb=short",
                 "-m", "not integration"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self.pkg_dir,
                env=env,
            )
            log = (result.stdout + "\n" + result.stderr).strip()
            return result.returncode == 0, _truncate_log(log)
        except subprocess.TimeoutExpired:
            return False, "pytest timed out (60s)"
        except Exception as e:
            return False, str(e)

    def has_tests(self) -> bool:
        """Check if the package has a test directory."""
        if not self.pkg_dir:
            return False
        for candidate in ["tests", "test"]:
            if os.path.isdir(os.path.join(self.pkg_dir, candidate)):
                return True
        return False

    def cleanup(self) -> None:
        """Remove the entire work directory."""
        try:
            shutil.rmtree(self.work_dir, ignore_errors=True)
        except Exception:
            logger.warning(f"Failed to cleanup verification dir: {self.work_dir}")
