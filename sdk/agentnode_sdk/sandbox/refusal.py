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
import shutil
import sys
from dataclasses import dataclass
from enum import Enum

#: What the person runs to find out whether their fix worked.
RECHECK = "agentnode sandbox doctor"

#: Platforms where a local container sandbox can exist at all.
LOCAL_CAPABLE = ("win32", "linux", "darwin")

#: For actions that make sense wherever a person is standing -- including the devices where a
#: local sandbox cannot exist, which is exactly where being told nothing would be worst.
ANY_PLATFORM = ("*",)

#: The signatures a container runtime uses when the daemon is there but this user may not use it.
_PERMISSION = re.compile(
    r"permission denied|dial unix.*permission|connect: permission denied|"
    r"got permission denied while trying to connect|access is denied|"
    r"error loading seccomp filter.*operation not permitted", re.I)


class RefusalCase(str, Enum):
    NOT_INSTALLED = "not_installed"
    MEMORY_CEILING_UNENFORCEABLE = "memory_ceiling_unenforceable"
    NOT_RUNNING = "not_running"
    NOT_PERMITTED = "not_permitted"
    INCOMPATIBLE = "incompatible"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    IMAGE_MISSING = "image_missing"
    IMAGE_PLACEHOLDER = "image_placeholder"


@dataclass(frozen=True)
class Action:
    """One thing a person can actually do, where it is actually possible.

    EM3B-R1-REVIEW-0003 / F4: offering a command and checking only that a command was offered is
    not the same as offering one that runs here. An action that names a tool declares it, and is
    withheld where that tool is absent -- so `sudo systemctl start docker` is not put in front of
    someone whose machine has no systemctl.

    What cannot be verified is not dressed up as though it could: a download page is a pointer,
    marked as one, and never presented as something to run.
    """
    text: str
    command: str | None = None
    url: str | None = None
    platforms: tuple = LOCAL_CAPABLE
    #: A program that must exist for this action to be offered at all.
    requires: str | None = None
    #: A pointer rather than a step: shown, never claimed to be executable here.
    informational: bool = False

    def applies_to(self, platform: str) -> bool:
        return "*" in self.platforms or platform in self.platforms

    def available_here(self, which=None) -> bool:
        """Whether the tool this action needs is present. Pointers need nothing."""
        if self.informational or self.requires is None:
            return True
        return (which or shutil.which)(self.requires) is not None


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
        for a in self.actions:
            if a.command and a.informational:
                raise ValueError(
                    f"{self.case.value}: an action cannot be a pointer and a command at once -- "
                    "either it runs here or it is somewhere to go")
        if not self.actions:
            raise ValueError(
                f"{self.case.value}: a refusal must carry something the person can do. There is "
                "no exception -- where nothing can be installed, a clean stop and a way to reach "
                "a person are still things a person can do")

    def render(self) -> str:
        lines = [self.headline, "", self.prevented, ""]
        steps = [a for a in self.actions if not a.informational]
        pointers = [a for a in self.actions if a.informational]
        if steps:
            lines.append("What you can do:")
            for a in steps:
                lines.append(f"  - {a.text}")
                if a.command:
                    lines.append(f"      {a.command}")
        if pointers:
            lines.append("" if steps else "")
            lines.append("Worth knowing:")
            for a in pointers:
                lines.append(f"  - {a.text}")
                if a.url:
                    lines.append(f"      {a.url}")
        if self.actions:
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
    Action("Where to get Docker Desktop", url="https://docs.docker.com/get-docker/",
           platforms=("win32", "darwin"), informational=True),
    Action("Install Podman -- it needs no service running as an administrator",
           command="sudo apt install podman", platforms=("linux",), requires="apt"),
    Action("Install Podman -- it needs no service running as an administrator",
           command="sudo dnf install podman", platforms=("linux",), requires="dnf"),
    Action("Where to get Docker Engine", url="https://docs.docker.com/engine/install/",
           platforms=("linux",), informational=True),
)

_START = (
    Action("Start Docker Desktop and wait until it reports its engine running",
           platforms=("win32", "darwin"), informational=True),
    Action("Start the service", command="sudo systemctl start docker", platforms=("linux",),
           requires="systemctl"),
    Action("Or, with Podman", command="systemctl --user start podman.socket",
           platforms=("linux",), requires="systemctl"),
    Action("If it still fails on Windows: WSL2 or Hyper-V may be switched off, or hardware "
           "virtualization may be disabled in the firmware", platforms=("win32",),
           informational=True),
)

# Deliberately NOT offered: joining the docker group. That is administrator-equivalent power on the
# machine, permanently, and the documentation says so. It is not a fix to hand someone in passing.
_PERMITTED_ACTIONS = (
    Action("Use rootless Podman, which runs under your own account and asks for nothing extra",
           command="sudo apt install podman", platforms=("linux",), requires="apt"),
    Action("Use rootless Podman, which runs under your own account and asks for nothing extra",
           command="sudo dnf install podman", platforms=("linux",), requires="dnf"),
    Action("Ask whoever administers this machine for access to the container runtime, and send "
           "them what this prints", command=RECHECK + " --json", platforms=ANY_PLATFORM),
)

# EM3B-R1-REVIEW-0001 / F4. The first version gave this case no actions at all, on the grounds
# that there is nothing to install on such a device. That was wrong twice over: it broke the rule
# that every refusal carries a way out, and it is not even true. Stopping cleanly, knowing nothing
# was half-done, and being able to say "I need this" are things a person can do -- and how often
# the last one is said is what decides when the remote option gets built.
_NO_LOCAL_SANDBOX_ACTIONS = (
    Action("Nothing was started and nothing was changed, so there is nothing to undo. You can "
           "stop here safely", platforms=ANY_PLATFORM, informational=True),
    Action("Run this on a computer where a container runtime can be installed -- the same command "
           "works there", command=RECHECK, platforms=ANY_PLATFORM),
    Action("Or say that you need to run this here, so the remote option gets built",
           url="https://github.com/agentnode-ai/agentnode/issues", platforms=ANY_PLATFORM,
           informational=True),
)

_MEMORY_ACTIONS = (
    Action("Switch this machine to cgroup v2, where the memory and swap ceiling is accounted for "
           "by default -- most current distributions already are", platforms=("linux",),
           informational=True),
    Action("Or turn on swap accounting for cgroup v1: add  cgroup_enable=memory swapaccount=1  to "
           "the kernel command line, then reboot", platforms=("linux",), informational=True),
    Action("Ask whoever administers this machine to do one of those, and send them what this "
           "prints", command=RECHECK + " --json", platforms=ANY_PLATFORM),
)

_INCOMPATIBLE_ACTIONS = (
    Action("Switch Docker Desktop to Linux containers (right-click the tray icon)",
           platforms=("win32",), informational=True),
    Action("Check what the engine is running", command="docker info --format '{{.OSType}}'",
           requires="docker"),
)


# Withholding a command whose tool is absent can empty a case entirely -- a Linux machine with
# neither apt, dnf nor systemctl would be told nothing at all. These two need no tool but the one
# the person is already running, so there is always a step left: ask again after changing
# something, and reach somebody who can change it.
_ALWAYS = (
    Action("Run the check again after you change anything -- it says what this machine has",
           command=RECHECK, platforms=ANY_PLATFORM),
    Action("Or send that output to whoever administers this machine",
           command=RECHECK + " --json", platforms=ANY_PLATFORM),
)


def _pick(actions, platform: str, which=None) -> tuple:
    """Only what applies here AND can be done here, and never nothing.

    `which` is injectable so a test can measure the withholding on a machine that is not the one
    being described.
    """
    kept = tuple(a for a in actions
                 if a.applies_to(platform) and a.available_here(which))
    if any(not a.informational for a in kept):
        return kept
    return kept + _ALWAYS


def classify(availability, *, platform: str | None = None, placeholder: bool = False,
             probe_error: str = "", which=None) -> Refusal | None:
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
            "Sending the work to a sandbox elsewhere is not built yet, so nothing here pretends "
            "otherwise.",
            actions=_pick(_NO_LOCAL_SANDBOX_ACTIONS, platform, which),
            details=f"platform={platform}")

    backend = getattr(availability, "backend", "none")
    if backend == "none":
        return Refusal(
            RefusalCase.NOT_INSTALLED,
            "The program that creates the sealed workspace is not installed",
            _PREVENTED, actions=_pick(_INSTALL, platform, which),
            details=f"neither docker nor podman is on PATH (platform={platform})")

    if not getattr(availability, "daemon_ok", False):
        if probe_error and _PERMISSION.search(probe_error):
            return Refusal(
                RefusalCase.NOT_PERMITTED,
                f"{backend} is installed, but this account may not use it",
                _PREVENTED + " Your user cannot reach the container runtime's socket.",
                actions=_pick(_PERMITTED_ACTIONS, platform, which),
                details=f"{backend} refused the connection: {probe_error.strip()[:200]}")
        return Refusal(
            RefusalCase.NOT_RUNNING,
            f"{backend} is installed, but it is not running",
            _PREVENTED, actions=_pick(_START, platform, which),
            details=(f"{backend} is on PATH; asking it for its status failed"
                     + (f": {probe_error.strip()[:200]}" if probe_error else "")))

    if getattr(availability, "engine_os", "") not in ("", "linux"):
        return Refusal(
            RefusalCase.INCOMPATIBLE,
            f"{backend} is running, but not in a mode that can run the sandbox",
            _PREVENTED + " The sealed workspace is a Linux image, and this engine is set to run "
            f"{availability.engine_os} containers.",
            actions=_pick(_INCOMPATIBLE_ACTIONS, platform, which),
            details=f"engine OS type is {availability.engine_os!r}, the pinned image needs linux")

    if getattr(availability, "memory_limit_enforceable", None) is not True:
        state = getattr(availability, "memory_limit_enforceable", None)
        return Refusal(
            RefusalCase.MEMORY_CEILING_UNENFORCEABLE,
            f"{backend} cannot hold the memory limit this sandbox asks for",
            _PREVENTED + " The sandbox declares how much memory a program may use. This engine "
            + ("reports that it cannot enforce that ceiling"
               if state is False else "did not say whether it can enforce that ceiling")
            + ", and running someone else's code under a limit that may not bind is the same as "
            "running it under no limit.",
            actions=_pick(_MEMORY_ACTIONS, platform, which),
            details=f"memory_limit_enforceable={state!r} for {backend}")

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
