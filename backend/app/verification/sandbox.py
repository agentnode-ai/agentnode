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
import re
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


class IsolationLevel:
    """Network isolation levels for verification sandbox.

    NONE = no isolation (e.g., pip install needs network)
    BEST_EFFORT = env-var hint, not kernel-enforced
    ENFORCED = hard isolation (container --network=none, nsjail, etc.)
    """
    NONE = "none"
    BEST_EFFORT = "best_effort"
    ENFORCED = "enforced"


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
        # P1-L9: drop a pidfile so the hourly cleanup cron can tell an
        # in-flight sandbox from a crashed/orphaned one. The cleanup job
        # refuses to delete a dir whose pidfile points at a still-running
        # process, even if the dir is older than the age cutoff.
        try:
            with open(os.path.join(self.work_dir, ".pid"), "w") as pf:
                pf.write(str(os.getpid()))
        except Exception:
            logger.debug("sandbox: could not write pidfile in %s", self.work_dir)

    def extract_artifact(self, artifact_bytes: bytes) -> bool:
        """Extract tar.gz artifact into work_dir. Returns success."""
        try:
            with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
                # Reject archives containing path-traversal entries
                for member in tar.getmembers():
                    if os.path.normpath(member.name).startswith("..") or os.path.isabs(member.name):
                        logger.warning(f"Rejecting artifact: dangerous path '{member.name}'")
                        return False
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
        """Create a Python venv. Uses uv if available and enabled, else python -m venv."""
        timeout = 30
        try:
            if settings.VERIFICATION_USE_UV and shutil.which("uv"):
                self._installer = "uv"
                result = subprocess.run(
                    ["uv", "venv", self.venv_dir],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            else:
                self._installer = "pip"
                result = subprocess.run(
                    ["python3", "-m", "venv", self.venv_dir],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            log = (result.stdout + "\n" + result.stderr).strip()
            return result.returncode == 0, _truncate_log(log)
        except subprocess.TimeoutExpired:
            return False, f"venv creation timed out ({timeout}s)"
        except Exception as e:
            return False, str(e)

    @property
    def installer(self) -> str:
        """Return which installer was used (uv or pip)."""
        return getattr(self, "_installer", "pip")

    def pip_install(self) -> tuple[bool, str]:
        """Install the package into the venv. Uses uv if available, falls back to pip.

        Network access is allowed (needs to fetch dependencies).
        """
        timeout = settings.VERIFICATION_INSTALL_TIMEOUT
        try:
            if self.installer == "uv" and shutil.which("uv"):
                result = subprocess.run(
                    [
                        "uv", "pip", "install",
                        "--python", self.python,
                        "--no-cache",
                        ".",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=self.pkg_dir,
                )
            else:
                result = subprocess.run(
                    [self.python, "-m", "pip", "install", "--no-cache-dir", "."],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=self.pkg_dir,
                )
            log = (result.stdout + "\n" + result.stderr).strip()
            ok = result.returncode == 0

            # Fallback: if uv fails, try pip
            if not ok and self.installer == "uv":
                logger.info("uv install failed, falling back to pip")
                self._installer = "pip"
                pip_timeout = settings.VERIFICATION_PIP_TIMEOUT
                result = subprocess.run(
                    [self.python, "-m", "pip", "install", "--no-cache-dir", "."],
                    capture_output=True,
                    text=True,
                    timeout=pip_timeout,
                    cwd=self.pkg_dir,
                )
                fallback_log = (result.stdout + "\n" + result.stderr).strip()
                log = log + "\n[FALLBACK to pip]\n" + fallback_log
                ok = result.returncode == 0

            return ok, _truncate_log(log)
        except subprocess.TimeoutExpired:
            return False, f"install timed out ({timeout}s)"
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

    def run_python_code_enforced(
        self, code: str, timeout: int = 30,
        heavy_ml: bool = False, image_override: str | None = None,
    ) -> tuple[bool, str]:
        """Run Python code in an isolated container with --network=none.

        Returns (success, log). If VERIFICATION_SANDBOX_MODE=container and no
        runtime is available, fails hard — no silent degradation.
        Falls back to best-effort subprocess only when mode is 'subprocess'.

        heavy_ml: mount pre-cached ML model volumes, increase memory/tmpfs.
        image_override: use a different container image (e.g. browser image).
        """
        from app.config import CONTAINER_RUNTIME, settings

        if not CONTAINER_RUNTIME:
            if settings.VERIFICATION_SANDBOX_MODE == "container":
                logger.error(
                    "VERIFICATION_SANDBOX_MODE=container but no container runtime "
                    "available. Refusing to silently degrade to best-effort."
                )
                return False, (
                    "Enforced sandbox unavailable: no container runtime (docker/podman) "
                    "found. VERIFICATION_SANDBOX_MODE is set to 'container' — refusing "
                    "to fall back to subprocess. Install docker or podman, or set "
                    "VERIFICATION_SANDBOX_MODE=subprocess for best-effort mode."
                )
            logger.warning("No container runtime available — falling back to best-effort subprocess")
            return self.run_python_code(code, timeout=timeout, restrict_network=True)

        # Write code to a temp file that gets mounted into the container
        code_file = os.path.join(self.work_dir, "_verify_code.py")
        with open(code_file, "w") as f:
            f.write(code)

        # Output dir for structured results
        out_dir = os.path.join(self.work_dir, "_out")
        os.makedirs(out_dir, exist_ok=True)

        # Find the site-packages dir inside the venv for PYTHONPATH.
        # Venv symlinks point to host Python — unusable inside the container.
        # Instead, mount site-packages and use the container's own Python.
        site_pkgs = ""
        venv_lib = os.path.join(self.venv_dir, "lib")
        if os.path.isdir(venv_lib):
            for d in os.listdir(venv_lib):
                sp = os.path.join(venv_lib, d, "site-packages")
                if os.path.isdir(sp):
                    site_pkgs = sp
                    break

        memory_limit = "2g" if heavy_ml else "512m"
        tmpfs_size = "256m" if heavy_ml else "64m"

        cmd = [
            CONTAINER_RUNTIME, "run", "--rm",
            "--network=none",
            "--read-only",
            "--pids-limit=128",
            f"--memory={memory_limit}",
            "--cpus=1.0",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size={tmpfs_size}",
            "-v", f"{self.pkg_dir}:/workspace:ro",
            "-v", f"{out_dir}:/out:rw",
            "-v", f"{code_file}:/run_code.py:ro",
        ]

        if heavy_ml:
            cache_dir = settings.VERIFICATION_MODEL_CACHE_DIR
            hf_cache = os.path.join(cache_dir, "huggingface")
            whisper_cache = os.path.join(cache_dir, "whisper")
            torch_cache = os.path.join(cache_dir, "torch")
            if os.path.isdir(hf_cache):
                cmd += ["-v", f"{hf_cache}:/home/verifier/.cache/huggingface:ro"]
            if os.path.isdir(whisper_cache):
                cmd += ["-v", f"{whisper_cache}:/home/verifier/.cache/whisper:ro"]
            if os.path.isdir(torch_cache):
                cmd += ["-v", f"{torch_cache}:/home/verifier/.cache/torch:ro"]
            cmd += [
                "-e", "HF_HUB_OFFLINE=1",
                "-e", "TRANSFORMERS_OFFLINE=1",
                "-e", "HF_HOME=/home/verifier/.cache/huggingface",
                "-e", "TORCH_HOME=/home/verifier/.cache/torch",
                "-e", "XDG_CACHE_HOME=/home/verifier/.cache",
                "-e", "TMPDIR=/tmp",
            ]

        # Mount site-packages and add to PYTHONPATH so imports work
        if site_pkgs:
            cmd += ["-v", f"{site_pkgs}:/site-packages:ro"]
            cmd += ["-e", "PYTHONPATH=/workspace:/site-packages"]
        else:
            cmd += ["-e", "PYTHONPATH=/workspace"]

        container_image = image_override or settings.VERIFICATION_CONTAINER_IMAGE
        cmd += [
            "-w", "/workspace",
            "--user", "1000:1000",
            container_image,
            "python", "/run_code.py",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 10,  # container overhead buffer
            )
            log = (result.stdout + "\n" + result.stderr).strip()
            return result.returncode == 0, _truncate_log(log)
        except subprocess.TimeoutExpired:
            # Kill the container if it didn't exit
            try:
                subprocess.run(
                    [CONTAINER_RUNTIME, "kill", "--signal=KILL", "agentnode-verify"],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass
            return False, f"Container execution timed out ({timeout}s)"
        except Exception as e:
            logger.error(f"Container execution failed: {e}")
            return False, f"Container execution error: {e}"

    def get_isolation_level(self, step: str = "import") -> str:
        """Return the actual isolation level for a given step.

        Reports honestly: if container mode is configured but runtime is missing,
        returns BEST_EFFORT (not ENFORCED) to avoid trust miscommunication.
        """
        from app.config import CONTAINER_RUNTIME, settings
        if step == "install":
            return IsolationLevel.NONE  # pip needs network
        if settings.VERIFICATION_SANDBOX_MODE == "container" and CONTAINER_RUNTIME:
            return IsolationLevel.ENFORCED
        return IsolationLevel.BEST_EFFORT

    def run_pytest(self) -> tuple[bool, str]:
        """Run pytest inside the venv. Returns (success, log)."""
        # First install pytest — use uv if available, else pip
        try:
            if self.installer == "uv" and shutil.which("uv"):
                subprocess.run(
                    ["uv", "pip", "install", "--python", self.python, "pytest"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            else:
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

    def run_pytest_in_container(self, timeout: int = 60) -> tuple[bool, str]:
        """Run pytest inside an isolated container. Returns (success, log).

        All publisher tests run here — no trust-level exceptions.
        pytest is installed on host (needs network), then site-packages
        are mounted read-only into the container.
        """
        from app.config import CONTAINER_RUNTIME, settings

        if not CONTAINER_RUNTIME:
            return False, "No container runtime available (docker/podman)"

        # Install pytest into venv on host (needs network)
        try:
            if self.installer == "uv" and shutil.which("uv"):
                subprocess.run(
                    ["uv", "pip", "install", "--python", self.python, "pytest"],
                    capture_output=True, text=True, timeout=30,
                )
            else:
                subprocess.run(
                    [self.python, "-m", "pip", "install", "--no-cache-dir", "pytest"],
                    capture_output=True, text=True, timeout=30,
                )
        except Exception:
            return False, "Failed to install pytest"

        # Detect test directory
        test_dir = None
        for candidate in ["tests", "test"]:
            if os.path.isdir(os.path.join(self.pkg_dir, candidate)):
                test_dir = candidate
                break
        if not test_dir:
            return False, "No tests/ or test/ directory found"

        # Find site-packages in venv for PYTHONPATH mount
        site_pkgs = ""
        venv_lib = os.path.join(self.venv_dir, "lib")
        if os.path.isdir(venv_lib):
            for d in os.listdir(venv_lib):
                sp = os.path.join(venv_lib, d, "site-packages")
                if os.path.isdir(sp):
                    site_pkgs = sp
                    break

        cmd = [
            CONTAINER_RUNTIME, "run", "--rm",
            "--network=none",
            "--read-only",
            "--pids-limit=128",
            "--memory=512m",
            "--cpus=1.0",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "-v", f"{self.pkg_dir}:/workspace:ro",
            "-e", "AGENTNODE_VERIFICATION=1",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTEST_ADDOPTS=--cache-clear -p no:cacheprovider",
        ]

        if site_pkgs:
            cmd += ["-v", f"{site_pkgs}:/site-packages:ro"]
            cmd += ["-e", "PYTHONPATH=/workspace:/site-packages"]
        else:
            cmd += ["-e", "PYTHONPATH=/workspace"]

        cmd += [
            "-w", "/workspace",
            "--user", "1000:1000",
            settings.VERIFICATION_CONTAINER_IMAGE,
            "python", "-m", "pytest", f"/workspace/{test_dir}",
            "-v", "--tb=short", "-m", "not integration",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )
            log = (result.stdout + "\n" + result.stderr).strip()
            return result.returncode == 0, _truncate_log(log)
        except subprocess.TimeoutExpired:
            return False, f"Container pytest timed out ({timeout}s)"
        except Exception as e:
            logger.error(f"Container pytest failed: {e}")
            return False, f"Container pytest error: {e}"

    def has_tests(self) -> bool:
        """Check if the package has a test directory."""
        if not self.pkg_dir:
            return False
        for candidate in ["tests", "test"]:
            if os.path.isdir(os.path.join(self.pkg_dir, candidate)):
                return True
        return False

    def generate_auto_tests(self, tools: list[dict]) -> bool:
        """Generate basic auto-tests when no test directory exists.

        Creates a tests/ directory with import, callable, and signature checks.
        Returns True if tests were generated.
        """
        if not self.pkg_dir or not tools:
            return False

        valid = [t for t in tools if t.get("entrypoint") and ":" in t["entrypoint"]]
        if not valid:
            return False

        test_dir = os.path.join(self.pkg_dir, "tests")
        os.makedirs(test_dir, exist_ok=True)

        lines = [
            '"""Auto-generated verification tests."""',
            "import importlib",
            "import inspect",
            "",
        ]

        for tool in valid:
            module_path, func_name = tool["entrypoint"].rsplit(":", 1)
            # Sanitize tool name to valid Python identifier chars only
            from app.shared.validators import sanitize_to_identifier, is_safe_identifier
            safe_name = sanitize_to_identifier(tool.get("name", "unknown"))

            # Validate module_path and func_name to prevent code injection
            if not all(is_safe_identifier(part) for part in module_path.split(".")):
                logger.warning("Skipping tool with unsafe module_path: %s", module_path)
                continue
            if not is_safe_identifier(func_name):
                logger.warning("Skipping tool with unsafe func_name: %s", func_name)
                continue

            lines.append(f'def test_{safe_name}_importable():')
            lines.append(f'    mod = importlib.import_module("{module_path}")')
            lines.append(f'    assert hasattr(mod, "{func_name}"), "Function \'{func_name}\' not found"')
            lines.append("")
            lines.append(f'def test_{safe_name}_callable():')
            lines.append(f'    mod = importlib.import_module("{module_path}")')
            lines.append(f'    fn = getattr(mod, "{func_name}")')
            lines.append(f'    assert callable(fn), "\'{func_name}\' is not callable"')
            lines.append("")
            lines.append(f'def test_{safe_name}_has_signature():')
            lines.append(f'    mod = importlib.import_module("{module_path}")')
            lines.append(f'    fn = getattr(mod, "{func_name}")')
            lines.append(f'    sig = inspect.signature(fn)')
            lines.append(f'    assert sig is not None')
            lines.append("")

        test_file = os.path.join(test_dir, "test_auto_verify.py")
        with open(test_file, "w") as f:
            f.write("\n".join(lines))

        return True

    def cleanup(self) -> None:
        """Remove the entire work directory."""
        try:
            shutil.rmtree(self.work_dir, ignore_errors=True)
        except Exception:
            logger.warning(f"Failed to cleanup verification dir: {self.work_dir}")
