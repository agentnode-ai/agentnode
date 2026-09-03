"""EM-3B: test doubles that exist to test the SUITE, and can never stand in for a backend.

There is a real temptation here, and it is worth naming. Remote and managed backends do not exist
yet, so a double that returns perfect readings would produce a green report for them today. That
report would be a lie with a timestamp on it.

So: every double carries ``IS_TEST_DOUBLE = True``, the runner copies that onto the report, and
:meth:`ConformanceReport.is_conformant` returns False whenever it is set -- no matter how many
checks passed. A double can prove that this suite reports ``pass`` on a good backend and ``fail`` on
a bad one. It can never prove anything about a product backend, because there is nothing on the
other side of it.
"""
from __future__ import annotations

import json

from agentnode_sdk.conformance.probe import MARKER
from agentnode_sdk.sandbox.types import SandboxAvailability, SandboxRequiredError

#: A container that behaves the way the hardened flags intend.
GOOD_READINGS = {
    "probe_version": "em3b.probe.1",
    "identity": {"uid": 1000, "gid": 1000, "euid": 1000, "pid": 1, "hostname": "3f2a1b0c9d8e",
                 "python": "3.12.7"},
    "proc_status": {"CapEff": "0000000000000000", "CapPrm": "0000000000000000",
                    "CapBnd": "0000000000000000", "NoNewPrivs": "1", "Seccomp": "2"},
    "containment": {"dockerenv": True, "containerenv": False,
                    "self_cgroup": "0::/", "pid1_comm": "python"},
    "rootfs_write": {"denied": True, "error": "OSError"},
    "mounts": [{"source": "overlay", "target": "/", "fstype": "overlay", "options": "ro"},
               {"source": "tmpfs", "target": "/tmp", "fstype": "tmpfs", "options": "rw,noexec"},
               {"source": "tmpfs", "target": "/sandbox-home", "fstype": "tmpfs", "options": "rw"},
               {"source": "proc", "target": "/proc", "fstype": "proc", "options": "rw"}],
    "runtime_sockets": {"/var/run/docker.sock": {"exists": False, "connect": "absent"},
                        "/run/podman/podman.sock": {"exists": False, "connect": "absent"}},
    "home": {"path": "/sandbox-home", "is_dir": True, "entries": [], "host_artefacts": [],
             "fs_bytes": 16 * 1024 ** 2, "writable": True},
    "cgroup": {"memory_max": str(512 * 1024 ** 2), "pids_max": "256", "cpu_max": "100000 100000"},
    "filesystems": {"/": {"bytes": 10 * 1024 ** 3, "free": 5 * 1024 ** 3},
                    "/tmp": {"bytes": 64 * 1024 ** 2, "free": 64 * 1024 ** 2},
                    "/sandbox-home": {"bytes": 16 * 1024 ** 2, "free": 16 * 1024 ** 2}},
    "env_names": ["HOME", "HOSTNAME", "LANG", "PATH", "PYTHON_VERSION"],
    "network": {"dns_example_com": "blocked:gaierror", "dns_example_com_seconds": 0.01,
                "direct_1_1_1_1": "blocked:OSError", "direct_1_1_1_1_seconds": 0.01,
                "direct_8_8_8_8": "blocked:OSError", "direct_8_8_8_8_seconds": 0.01},
}

#: The same shape, from a container that is not doing what it claims: root, writable root,
#: capabilities intact, the runtime socket mounted, the host home visible, no limits, open network.
BAD_READINGS = {
    "probe_version": "em3b.probe.1",
    "identity": {"uid": 0, "gid": 0, "euid": 0, "pid": 1, "hostname": "box", "python": "3.12.7"},
    "proc_status": {"CapEff": "000001ffffffffff", "CapPrm": "000001ffffffffff",
                    "CapBnd": "000001ffffffffff", "NoNewPrivs": "0", "Seccomp": "0"},
    "containment": {"dockerenv": False, "containerenv": False, "self_cgroup": "0::/",
                    "pid1_comm": "systemd"},
    "rootfs_write": {"denied": False, "error": None},
    "mounts": [{"source": "/", "target": "/", "fstype": "ext4", "options": "rw"},
               {"source": "/home/someone", "target": "/host-home", "fstype": "ext4",
                "options": "rw"}],
    "runtime_sockets": {"/var/run/docker.sock": {"exists": True, "connect": "CONNECTED"}},
    "home": {"path": "/root", "is_dir": True, "entries": [".ssh", ".aws", "notes.txt"],
             "host_artefacts": [".ssh", ".aws"], "fs_bytes": 500 * 1024 ** 3, "writable": True},
    "cgroup": {"memory_max": "max", "pids_max": "max", "cpu_max": "max 100000"},
    "filesystems": {"/": {"bytes": 500 * 1024 ** 3, "free": 200 * 1024 ** 3},
                    "/tmp": {"bytes": 500 * 1024 ** 3, "free": 200 * 1024 ** 3}},
    "env_names": ["AWS_SECRET_ACCESS_KEY", "HOME", "OPENAI_API_KEY", "PATH"],
    "network": {"dns_example_com": "REACHED", "direct_1_1_1_1": "REACHED",
                "direct_8_8_8_8": "REACHED"},
}


class _DoubleBase:
    """Shared plumbing. Marked so no report built from it can be presented as product evidence."""

    IS_TEST_DOUBLE = True

    def __init__(self, runtime: str | None = None, readings=None, available: bool = True):
        self._runtime = runtime or "double"
        self._image = "test-double://no-image"
        self._readings = dict(readings if readings is not None else GOOD_READINGS)
        self._available = available and runtime != "agentnode-conformance-absent-runtime"

    def check_available(self):
        return SandboxAvailability(
            available=self._available, backend="double" if self._available else "none",
            reason="" if self._available else
            "no container runtime found. Install Docker or Podman, then run agentnode sandbox pull",
            executable_path=None, daemon_ok=self._available)

    def explain_unavailable(self):
        a = self.check_available()
        return "" if a.available else a.reason

    def wrap_command(self, spec):
        if spec.env_passthrough and spec.network != "egress":
            raise SandboxRequiredError(
                "env_passthrough requires network='egress' -- refusing name-only pass-through")
        cg = self._readings.get("cgroup", {})
        home = self._readings.get("home", {}).get("path", "/sandbox-home")
        fs = self._readings.get("filesystems", {})
        tmp = (fs.get("/tmp") or {}).get("bytes", 64 * 1024 ** 2)
        return ["double", "run", "--rm", "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--user", "1000:1000",
                "--pids-limit", str(cg.get("pids_max", "256")),
                "--memory", str(cg.get("memory_max", "536870912")), "--cpus", "1",
                "--tmpfs", f"/tmp:rw,noexec,nosuid,size={tmp}",
                "-e", f"HOME={home}", "--tmpfs", f"{home}:rw,size=16777216",
                "--network", "none", self._image, *spec.command]

    def conformance_host_observations(self):
        """Outside-vantage observations a double has no runtime to produce.

        Read by the runner only because ``IS_TEST_DOUBLE`` is set, and a report carrying that
        marker is never conformant. A product backend cannot use this route.
        """
        return {
            "runtime_version": "double 0.0-not-a-runtime",
            "image": self._image,
            "leftovers": {"containers": [], "networks": [], "filtered_on": "double"},
        }

    def open_agent_session(self, spec):
        if not self._available:
            raise SandboxRequiredError("no container runtime available -- refusing to run on the host")
        raise NotImplementedError("a double opens no session")


class GoodBackendDouble(_DoubleBase):
    """Reports a container that does what the hardened flags intend. Tests the pass path."""

    def __init__(self, runtime=None, readings=None, available=True):
        super().__init__(runtime, GOOD_READINGS if readings is None else readings, available)

    def run_process(self, spec, input_text=None, timeout=120.0):
        joined = " ".join(spec.command)
        if "time.sleep" in joined:
            return -1, "", f"\n[sandbox timed out after {timeout}s]"
        if "held.append" in joined:
            return 137, "", "killed"
        return 0, MARKER + json.dumps(self._readings) + "\n", ""


class BadBackendDouble(_DoubleBase):
    """Reports a container that is not isolating anything. Tests the fail path."""

    def __init__(self, runtime=None, readings=None, available=True):
        super().__init__(runtime, BAD_READINGS if readings is None else readings, available)

    def wrap_command(self, spec):
        # A backend that does not refuse the unsafe combination, so the suite has something to
        # report as self-reported and failing.
        cg = self._readings.get("cgroup", {})
        return ["double", "run", "--memory", str(cg.get("memory_max", "max")),
                "--pids-limit", str(cg.get("pids_max", "max")), "--cpus", "1",
                "--tmpfs", "/tmp:rw,size=536870912000", "-e", "HOME=/root",
                "--network", "bridge", self._image, *spec.command]

    def conformance_host_observations(self):
        return {
            "runtime_version": "double 0.0-not-a-runtime",
            "image": self._image,
            "leftovers": {"containers": ["agentnode-conformance-leftover"],
                          "networks": ["agentnode-egress-leftover"], "filtered_on": "double"},
        }

    def run_process(self, spec, input_text=None, timeout=120.0):
        joined = " ".join(spec.command)
        if "time.sleep" in joined:
            return 0, "", ""            # not stopped: the ceiling did not hold
        if "held.append" in joined:
            return 0, "ALLOCATED 768\n", ""
        return 0, MARKER + json.dumps(self._readings) + "\n", ""


class SilentBackendDouble(_DoubleBase):
    """Produces no readings at all. Tests that a broken probe reads as probe_error, never as fail."""

    def run_process(self, spec, input_text=None, timeout=120.0):
        return 0, "nothing useful here\n", ""
