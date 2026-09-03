"""EM-3B-R1 / R3: one refusal, structured, with a way out that exists.

The conformance suite measured the old one and it failed for the right reason: *"no container
runtime (docker or podman) found on PATH"* names no next step, and the documentation promises that
every refusal carries at least one thing the person can actually do.

There were also two answers to the same question. The SDK produced a bare sentence in
``ContainerBackend._probe``; the CLI doctor produced structured checks with fixes in
``_build_env_checks``. Only one of them ever had a next step, and neither distinguished a
permission problem, an engine in the wrong mode, or a device where a local sandbox is not possible.
This module is the single source both now render, so they cannot drift apart again.

Two rules the classifier keeps:

* **An action is offered only where it exists.** Every action declares the platforms it applies to,
  and one that does not apply is not shown. There is no host execution, no way to switch the
  sandbox off, and no remote option -- because no remote backend exists to send work to.
* **Every action is followed by a re-check.** A link that leaves someone guessing whether it worked
  is not a way out, so each refusal names the command that answers that.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from enum import Enum

#: What the person runs to find out whether their fix worked.
RECHECK = "agentnode sandbox doctor"

#: Platforms where a local container sandbox can exist at all.
LOCAL_CAPABLE = ("win32", "linux", "darwin")

#: The signatures a container runtime uses when the daemon is there but this user may not use it.
_PERMISSION = re.compile(
    r"permission denied|dial unix.*permission|connect: permission denied|"
    r"got permission denied while trying to connect|access is denied|"
    r"error loading seccomp filter.*operation not permitted", re.I)


class RefusalCase(str, Enum):
    NOT_INSTALLED = "not_installed"
    NOT_RUNNING = "not_running"
    NOT_PERMITTED = "not_permitted"
    INCOMPATIBLE = "incompatible"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    IMAGE_MISSING = "image_missing"
    IMAGE_PLACEHOLDER = "image_placeholder"


@dataclass(frozen=True)
class Action:
    """One thing a person can actually do, on the platforms where it is actually possible."""
    text: str
    command: str | None = None
    url: str | None = None
    platforms: tuple = LOCAL_CAPABLE

    def applies_to(self, platform: str) -> bool:
        return platform in self.platforms


@dataclass(frozen=True)
class Refusal:
    case: RefusalCase
    headline: str
    prevented: str
    actions: tuple = ()
    recheck: str = RECHECK
    details: str = ""

    def __post_init__(self) -> None:
        if not self.headline.strip() or not self.prevented.strip():
            raise ValueError("a refusal needs a headline and what it prevented")
        if self.case is not RefusalCase.PLATFORM_UNSUPPORTED and not self.actions:
            raise ValueError(
                f"{self.case.value}: a refusal must carry something the person can do. Only "
                "platform_unsupported may carry none, because on that device there is nothing")

    def render(self) -> str:
        lines = [self.headline, "", self.prevented, ""]
        if self.actions:
            lines.append("What you can do:")
            for a in self.actions:
                lines.append(f"  - {a.text}")
                if a.command:
                    lines.append(f"      {a.command}")
                if a.url:
                    lines.append(f"      {a.url}")
            lines += ["", f"Then check it worked:  {self.recheck}"]
        if self.details:
            lines += ["", f"Details: {self.details}"]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "case": self.case.value, "headline": self.headline, "prevented": self.prevented,
            "recheck": self.recheck, "details": self.details,
            "actions": [{"text": a.text, "command": a.command, "url": a.url} for a in self.actions],
        }


_PREVENTED = ("Someone else's code was about to run on this computer with your files in reach. It "
              "did not run: nothing was executed and nothing was changed.")

_INSTALL = (
    Action("Install Docker Desktop, then start it", url="https://docs.docker.com/get-docker/",
           platforms=("win32", "darwin")),
    Action("Install Podman with your package manager -- it needs no service running as an "
           "administrator", command="sudo apt install podman   # or: sudo dnf install podman",
           platforms=("linux",)),
    Action("Or install Docker Engine the way your distribution documents",
           url="https://docs.docker.com/engine/install/", platforms=("linux",)),
)

_START = (
    Action("Start Docker Desktop and wait until it reports its engine running",
           platforms=("win32", "darwin")),
    Action("Start the service", command="sudo systemctl start docker", platforms=("linux",)),
    Action("Or, with Podman", command="systemctl --user start podman.socket",
           platforms=("linux",)),
    Action("If it still fails on Windows: WSL2 or Hyper-V may be switched off, or hardware "
           "virtualization may be disabled in the firmware", platforms=("win32",)),
)

# Deliberately NOT offered: joining the docker group. That is administrator-equivalent power on the
# machine, permanently, and the documentation says so. It is not a fix to hand someone in passing.
_PERMITTED_ACTIONS = (
    Action("Use rootless Podman, which runs under your own account and asks for nothing extra",
           command="sudo apt install podman   # or: sudo dnf install podman",
           platforms=("linux",)),
    Action("Or ask whoever administers this machine for access to the container runtime. Send them "
           "the output of the check below", command=RECHECK + " --json",
           platforms=("win32", "linux", "darwin")),
)

_INCOMPATIBLE_ACTIONS = (
    Action("Switch Docker Desktop to Linux containers (right-click the tray icon)",
           platforms=("win32",)),
    Action("Check what the engine is running", command="docker info --format '{{.OSType}}'"),
)


def _pick(actions, platform: str) -> tuple:
    return tuple(a for a in actions if a.applies_to(platform))


def classify(availability, *, platform: str | None = None, placeholder: bool = False,
             probe_error: str = "") -> Refusal | None:
    """Turn a probe result into the refusal a person can act on, or None when nothing is wrong.

    ``probe_error`` is what the runtime said when it was asked whether it was usable; it is what
    separates "the daemon is not running" from "you are not allowed to talk to it".
    """
    platform = platform or sys.platform
    # One source: the probe records what the runtime said, and a caller may pass it explicitly.
    probe_error = probe_error or getattr(availability, "probe_error", "") or ""
    if getattr(availability, "available", False):
        return None

    if platform not in LOCAL_CAPABLE:
        return Refusal(
            RefusalCase.PLATFORM_UNSUPPORTED,
            "There is no sealed workspace on this kind of device",
            _PREVENTED + " A local sandbox needs a container runtime, and this platform has none. "
            "Sending the work to a sandbox elsewhere is not built yet, so there is nothing to "
            "offer here rather than something that would not work.",
            actions=(), details=f"platform={platform}")

    backend = getattr(availability, "backend", "none")
    if backend == "none":
        return Refusal(
            RefusalCase.NOT_INSTALLED,
            "The program that creates the sealed workspace is not installed",
            _PREVENTED, actions=_pick(_INSTALL, platform),
            details=f"neither docker nor podman is on PATH (platform={platform})")

    if not getattr(availability, "daemon_ok", False):
        if probe_error and _PERMISSION.search(probe_error):
            return Refusal(
                RefusalCase.NOT_PERMITTED,
                f"{backend} is installed, but this account may not use it",
                _PREVENTED + " Your user cannot reach the container runtime's socket.",
                actions=_pick(_PERMITTED_ACTIONS, platform),
                details=f"{backend} refused the connection: {probe_error.strip()[:200]}")
        return Refusal(
            RefusalCase.NOT_RUNNING,
            f"{backend} is installed, but it is not running",
            _PREVENTED, actions=_pick(_START, platform),
            details=(f"{backend} is on PATH; asking it for its status failed"
                     + (f": {probe_error.strip()[:200]}" if probe_error else "")))

    if getattr(availability, "engine_os", "") not in ("", "linux"):
        return Refusal(
            RefusalCase.INCOMPATIBLE,
            f"{backend} is running, but not in a mode that can run the sandbox",
            _PREVENTED + " The sealed workspace is a Linux image, and this engine is set to run "
            f"{availability.engine_os} containers.",
            actions=_pick(_INCOMPATIBLE_ACTIONS, platform),
            details=f"engine OS type is {availability.engine_os!r}, the pinned image needs linux")

    if placeholder:
        return Refusal(
            RefusalCase.IMAGE_PLACEHOLDER,
            "This build of AgentNode has no sealed workspace image",
            _PREVENTED + " Nothing you can do locally fixes this one.",
            actions=(Action("Update AgentNode", command="pip install --upgrade agentnode-sdk"),),
            details="the pinned image digest is a placeholder in this build")

    return Refusal(
        RefusalCase.IMAGE_MISSING,
        "The sealed workspace image has not been downloaded yet",
        _PREVENTED,
        actions=(Action("Download it once", command="agentnode sandbox pull"),),
        details=f"the pinned image is not present locally for {backend}")
