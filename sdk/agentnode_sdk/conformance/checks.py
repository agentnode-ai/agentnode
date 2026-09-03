"""EM-3B: the checks, as pure functions of what was gathered.

Every check takes a :class:`Context` -- the probe's readings, the runtime's view, the argv, the
results of the bounded stress runs and the host-side observations -- and returns exactly one
:class:`CheckResult`. Nothing here executes anything, which is what makes each check testable on a
synthetic context: the suite can be shown to report ``fail`` on a bad backend without needing a bad
backend, and to report ``probe_error`` rather than ``fail`` when its own reading is missing.

That distinction is the point. A suite that cannot tell "the backend did not do this" from "I could
not see whether the backend did this" will eventually accuse a correct backend, and worse, will
eventually clear a broken one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentnode_sdk.conformance.report import CheckResult, Vantage

# Mount targets a hardened container legitimately has. Anything else is a bind mount somebody
# asked for, and has to be in the declared set to be acceptable.
_ORDINARY_TARGETS = {
    "/", "/proc", "/sys", "/dev", "/dev/pts", "/dev/mqueue", "/dev/shm", "/dev/console",
    "/etc/hosts", "/etc/hostname", "/etc/resolv.conf", "/tmp", "/sys/fs/cgroup",
    "/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger",
    "/proc/acpi", "/proc/kcore", "/proc/keys", "/proc/timer_list", "/proc/scsi",
    "/sys/firmware", "/sys/devices/virtual/powercap",
}


@dataclass(frozen=True)
class Context:
    """Everything that was gathered, and nothing that was concluded."""
    readings: dict = field(default_factory=dict)
    probe_failure: str | None = None
    inspect: dict = field(default_factory=dict)
    argv: list = field(default_factory=list)
    declared: dict = field(default_factory=dict)
    stress: dict = field(default_factory=dict)
    host: dict = field(default_factory=dict)


def _read(ctx: Context, name: str):
    """Return (value, problem). ``problem`` set means nothing was measured, not that it failed."""
    if ctx.probe_failure:
        return None, f"the probe did not run: {ctx.probe_failure}"
    if name not in ctx.readings:
        return None, f"the probe produced no {name!r} reading"
    value = ctx.readings[name]
    if isinstance(value, dict) and "_error" in value:
        return None, (f"the {name!r} reading failed inside the sandbox: "
                      f"{value['_error']}: {value.get('_detail', '')}"[:200])
    return value, None


def _argv_value(argv: list, flag: str) -> str | None:
    for i, item in enumerate(argv):
        if item == flag and i + 1 < len(argv):
            return argv[i + 1]
        if item.startswith(flag + "="):
            return item.split("=", 1)[1]
    return None


def _bytes(text: str) -> int | None:
    m = re.fullmatch(r"(\d+)\s*([kmgKMG])?[bB]?", (text or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    return n * {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}.get((m.group(2) or "").lower(), 1)


# --------------------------------------------------------------------------- the checks

def check_identity(ctx: Context) -> CheckResult:
    ident = ctx.host.get("runtime_version")
    image = ctx.host.get("image")
    if not ident:
        return CheckResult.not_checked(
            "identity", "The backend names itself and its version", "identity",
            "no runtime version was obtained; the backend was not reachable")
    return CheckResult.measured(
        "identity", "The backend names itself and its version", "identity",
        bool(ident and image), Vantage.OUTSIDE,
        f"the runtime reports {ident!r} and the image is pinned by digest {image!r}",
        detail={"runtime_version": ident, "image": image})


def check_outside_host_process(ctx: Context) -> CheckResult:
    c, problem = _read(ctx, "containment")
    ident, problem2 = _read(ctx, "identity")
    if problem or problem2:
        return CheckResult.probe_error(
            "outside-host-process", "The work runs outside the host process", "execution",
            problem or problem2)
    markers = [k for k in ("dockerenv", "containerenv") if c.get(k)]
    cgroup = str(c.get("self_cgroup", ""))
    pid1 = c.get("pid1_comm", "")
    ok = bool(markers) or "docker" in cgroup or "libpod" in cgroup or pid1 not in ("systemd", "init")
    return CheckResult.measured(
        "outside-host-process", "The work runs outside the host process", "execution", ok,
        Vantage.INSIDE,
        f"inside: markers {markers or 'none'}, pid 1 is {pid1!r}, hostname {ident.get('hostname')!r}",
        detail={"markers": markers, "cgroup": cgroup[:200], "pid1": pid1})


def check_not_root(ctx: Context) -> CheckResult:
    ident, problem = _read(ctx, "identity")
    if problem:
        return CheckResult.probe_error("not-root", "The payload does not run as root", "user",
                                       problem)
    uid, euid = ident.get("uid"), ident.get("euid")
    return CheckResult.measured(
        "not-root", "The payload does not run as root", "user",
        uid not in (0, None) and euid not in (0, None), Vantage.INSIDE,
        f"inside: uid={uid} euid={euid} gid={ident.get('gid')}",
        detail={"uid": uid, "euid": euid})


def check_read_only_root(ctx: Context) -> CheckResult:
    w, problem = _read(ctx, "rootfs_write")
    if problem:
        return CheckResult.probe_error("read-only-root", "The root filesystem is read-only",
                                       "filesystem", problem)
    denied = bool(w.get("denied"))
    outside = ctx.inspect.get("HostConfig", {}).get("ReadonlyRootfs")
    vantage = Vantage.BOTH if outside is not None else Vantage.INSIDE
    evidence = ("inside: writing to / was refused with " + str(w.get("error"))
                if denied else "inside: a write to / SUCCEEDED, so the root is writable")
    if outside is not None:
        evidence += f"; the runtime reports ReadonlyRootfs={outside}"
    return CheckResult.measured("read-only-root", "The root filesystem is read-only", "filesystem",
                                denied and (outside is not False), vantage, evidence,
                                detail={"inside_denied": denied, "runtime_says": outside})


def check_declared_mounts_only(ctx: Context) -> CheckResult:
    mounts, problem = _read(ctx, "mounts")
    if problem:
        return CheckResult.probe_error("declared-mounts-only", "Only the declared mounts are there",
                                       "mounts", problem)
    declared = set(ctx.declared.get("mount_targets", ())) | {ctx.declared.get("home_path", "")}
    unexpected = []
    for m in mounts:
        target = m.get("target", "")
        if target in _ORDINARY_TARGETS or target in declared:
            continue
        if target.startswith(("/proc/", "/sys/", "/dev/")):
            continue
        unexpected.append(target)
    return CheckResult.measured(
        "declared-mounts-only", "Only the declared mounts are there", "mounts",
        not unexpected, Vantage.INSIDE,
        ("inside: no mount beyond the ordinary container set and the declared ones"
         if not unexpected else f"inside: unexpected mount targets {unexpected}"),
        detail={"unexpected": unexpected, "declared": sorted(t for t in declared if t)})


def check_no_runtime_socket(ctx: Context) -> CheckResult:
    socks, problem = _read(ctx, "runtime_sockets")
    if problem:
        return CheckResult.probe_error(
            "no-runtime-socket", "No container-runtime socket is reachable", "socket", problem)
    reachable = [p for p, v in socks.items() if v.get("connect") == "CONNECTED"]
    present = [p for p, v in socks.items() if v.get("exists")]
    return CheckResult.measured(
        "no-runtime-socket", "No container-runtime socket is reachable", "socket",
        not reachable, Vantage.INSIDE,
        (f"inside: none of the {len(socks)} known runtime socket paths could be connected to"
         if not reachable else
         f"inside: CONNECTED to {reachable} -- that path hands over the host"),
        detail={"present": present, "reachable": reachable})


def check_capabilities_dropped(ctx: Context) -> CheckResult:
    status, problem = _read(ctx, "proc_status")
    if problem:
        return CheckResult.probe_error("capabilities-dropped", "All capabilities are dropped",
                                       "capabilities", problem)
    eff = status.get("CapEff", "")
    try:
        value = int(eff, 16)
    except (TypeError, ValueError):
        return CheckResult.probe_error("capabilities-dropped", "All capabilities are dropped",
                                       "capabilities",
                                       f"CapEff was not a hex mask: {eff!r}")
    return CheckResult.measured(
        "capabilities-dropped", "All capabilities are dropped", "capabilities",
        value == 0, Vantage.INSIDE,
        f"inside: /proc/self/status CapEff={eff} ({'empty' if value == 0 else 'NOT empty'})",
        detail={k: status.get(k) for k in ("CapEff", "CapPrm", "CapBnd", "CapAmb")})


def check_no_new_privileges(ctx: Context) -> CheckResult:
    status, problem = _read(ctx, "proc_status")
    if problem:
        return CheckResult.probe_error("no-new-privileges", "Privileges cannot be regained",
                                       "privileges", problem)
    nnp = status.get("NoNewPrivs")
    return CheckResult.measured(
        "no-new-privileges", "Privileges cannot be regained", "privileges",
        str(nnp).strip() == "1", Vantage.INSIDE,
        f"inside: /proc/self/status NoNewPrivs={nnp}", detail={"NoNewPrivs": nnp})


def check_network_mode(ctx: Context) -> CheckResult:
    net, problem = _read(ctx, "network")
    if problem:
        return CheckResult.probe_error("network-mode", "The network mode is what was asked for",
                                       "network", problem)
    mode = ctx.declared.get("network", "none")
    reached = [k for k, v in net.items() if v == "REACHED"]
    if mode == "none":
        return CheckResult.measured(
            "network-mode", "The network mode is what was asked for", "network",
            not reached, Vantage.INSIDE,
            ("inside: with network=none, name resolution and both direct connections were refused"
             if not reached else f"inside: network=none was asked for, yet {reached} were reached"),
            detail=dict(net))
    return CheckResult.not_checked(
        "network-mode", "The network mode is what was asked for", "network",
        f"this run declared network={mode!r}; this check measures the none case")


def check_egress_allowlist(ctx: Context) -> CheckResult:
    matrix = ctx.host.get("egress_matrix")
    if matrix is None:
        return CheckResult.not_checked(
            "egress-allowlist", "Only the sealed destinations are reachable", "egress",
            "no egress run was performed: it needs a container runtime and an internal network, "
            "so it is measured on Linux CI rather than wherever the suite happens to run")
    bypassed = [k for k, v in matrix.items() if str(v).startswith("BYPASS")]
    allowed_ok = str(matrix.get("allowed_via_proxy", "")).startswith("ALLOWED")
    denied_ok = not str(matrix.get("denied_via_proxy", "")).startswith("ALLOWED")
    return CheckResult.measured(
        "egress-allowlist", "Only the sealed destinations are reachable", "egress",
        not bypassed and allowed_ok and denied_ok, Vantage.INSIDE,
        (f"inside the internal network: direct routes {'all blocked' if not bypassed else bypassed}, "
         f"the sealed destination {'was reachable through the proxy' if allowed_ok else 'was NOT'}, "
         f"an unsealed one {'was refused' if denied_ok else 'was ALLOWED'}"),
        detail=dict(matrix))


def _cgroup_limit(ctx, check_id, title, family, keys, expected, unit):
    cg, problem = _read(ctx, "cgroup")
    if problem:
        return CheckResult.probe_error(check_id, title, family, problem)
    raw = next((cg[k] for k in keys if k in cg), None)
    if raw is None:
        return CheckResult.probe_error(
            check_id, title, family,
            f"none of the cgroup files {list(keys)} was readable inside the sandbox")
    if expected is None:
        return CheckResult.not_checked(check_id, title, family,
                                       "the run declared no such limit, so there is none to verify")
    return raw, cg


def check_limit_memory(ctx: Context) -> CheckResult:
    got = _cgroup_limit(ctx, "limit-memory", "The memory limit is enforced", "limits",
                        ("memory_max", "memory_max_v1"), ctx.declared.get("memory"), "bytes")
    if isinstance(got, CheckResult):
        return got
    raw, _cg = got
    expected = _bytes(ctx.declared.get("memory", ""))
    try:
        actual = int(str(raw).strip())
    except ValueError:
        return CheckResult.probe_error("limit-memory", "The memory limit is enforced", "limits",
                                       f"the cgroup memory file read {raw!r}")
    stress = ctx.stress.get("memory")
    killed = stress.get("killed") if isinstance(stress, dict) else None
    ok = actual == expected and (killed is not False)
    evidence = (f"inside: the cgroup memory ceiling is {actual} bytes against the declared "
                f"{expected} bytes")
    if killed is not None:
        evidence += ("; an allocation past that ceiling was stopped"
                     if killed else "; an allocation past that ceiling was NOT stopped")
    return CheckResult.measured("limit-memory", "The memory limit is enforced", "limits", ok,
                                Vantage.INSIDE, evidence,
                                detail={"cgroup": actual, "declared": expected, "stress": stress})


def check_limit_pids(ctx: Context) -> CheckResult:
    got = _cgroup_limit(ctx, "limit-pids", "The process limit is enforced", "limits",
                        ("pids_max", "pids_max_v1"), ctx.declared.get("pids"), "processes")
    if isinstance(got, CheckResult):
        return got
    raw, _cg = got
    expected = ctx.declared.get("pids")
    ok = str(raw).strip() == str(expected).strip()
    return CheckResult.measured(
        "limit-pids", "The process limit is enforced", "limits", ok, Vantage.INSIDE,
        f"inside: the cgroup process ceiling is {str(raw).strip()!r} against the declared "
        f"{expected!r}", detail={"cgroup": str(raw).strip(), "declared": expected})


def check_limit_cpu(ctx: Context) -> CheckResult:
    cg, problem = _read(ctx, "cgroup")
    if problem:
        return CheckResult.probe_error("limit-cpu", "The processor limit is enforced", "limits",
                                       problem)
    raw = cg.get("cpu_max") or cg.get("cpu_quota_v1")
    if raw is None:
        return CheckResult.probe_error("limit-cpu", "The processor limit is enforced", "limits",
                                       "no cgroup cpu file was readable inside the sandbox")
    unlimited = str(raw).strip().split()[0] in ("max", "-1")
    return CheckResult.measured(
        "limit-cpu", "The processor limit is enforced", "limits", not unlimited, Vantage.INSIDE,
        f"inside: the cgroup processor setting reads {str(raw).strip()!r} against the declared "
        f"{ctx.declared.get('cpus')!r}",
        detail={"cgroup": str(raw).strip(), "declared": ctx.declared.get("cpus")})


def check_limit_disk(ctx: Context) -> CheckResult:
    fs, problem = _read(ctx, "filesystems")
    if problem:
        return CheckResult.probe_error("limit-disk", "The writable space is bounded", "limits",
                                       problem)
    tmp = (fs.get("/tmp") or {}).get("bytes")
    expected = _bytes(ctx.declared.get("tmp_size", ""))
    if tmp is None:
        return CheckResult.probe_error("limit-disk", "The writable space is bounded", "limits",
                                       "the size of /tmp could not be read inside the sandbox")
    ok = expected is not None and tmp <= expected * 1.05
    return CheckResult.measured(
        "limit-disk", "The writable space is bounded", "limits", ok, Vantage.INSIDE,
        f"inside: /tmp holds {tmp} bytes against the declared {expected} bytes",
        detail={"tmp_bytes": tmp, "declared": expected, "filesystems": fs})


def check_limit_wallclock(ctx: Context) -> CheckResult:
    s = ctx.stress.get("wallclock")
    if not isinstance(s, dict):
        return CheckResult.not_checked(
            "limit-wallclock", "A run that will not finish is stopped", "limits",
            "no timeout run was performed")
    return CheckResult.measured(
        "limit-wallclock", "A run that will not finish is stopped", "limits",
        bool(s.get("killed")), Vantage.OUTSIDE,
        (f"a payload asked to sleep {s.get('sleep')}s under a {s.get('timeout')}s ceiling was "
         f"{'stopped' if s.get('killed') else 'NOT stopped'} after {s.get('elapsed')}s"),
        detail=dict(s))


def check_clean_home(ctx: Context) -> CheckResult:
    home, problem = _read(ctx, "home")
    if problem:
        return CheckResult.probe_error("clean-home", "The home directory is fresh and small",
                                       "home", problem)
    artefacts = home.get("host_artefacts") or []
    entries = home.get("entries") or []
    size = home.get("fs_bytes")
    expected = _bytes(ctx.declared.get("home_size", ""))
    small = expected is None or (size is not None and size <= expected * 1.05)
    ok = not artefacts and home.get("is_dir") and small
    return CheckResult.measured(
        "clean-home", "The home directory is fresh and small", "home", ok, Vantage.INSIDE,
        (f"inside: HOME is {home.get('path')!r} holding {len(entries)} entries in {size} bytes, "
         f"and none of the host's home artefacts is present"
         if ok else
         f"inside: HOME is {home.get('path')!r} and carries {artefacts or entries} "
         f"in {size} bytes"),
        detail={"entries": entries[:20], "host_artefacts": artefacts, "bytes": size,
                "declared": expected})


def check_secrets_only_by_release(ctx: Context) -> CheckResult:
    names, problem = _read(ctx, "env_names")
    if problem:
        return CheckResult.probe_error(
            "secrets-only-by-release", "Only released variables are inside", "secrets", problem)
    allowed = set(ctx.declared.get("env_names", ())) | {
        "HOME", "HOSTNAME", "PATH", "LANG", "LC_ALL", "PWD", "SHLVL", "TERM", "PYTHONPATH",
        "PYTHON_VERSION", "PYTHON_SHA256", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
        "GPG_KEY", "PYTHON_PIP_VERSION", "PYTHON_GET_PIP_URL", "PYTHON_GET_PIP_SHA256",
        "PYTHON_SETUPTOOLS_VERSION", "_",
    }
    leaked = sorted(n for n in names if n not in allowed)
    return CheckResult.measured(
        "secrets-only-by-release", "Only released variables are inside", "secrets",
        not leaked, Vantage.INSIDE,
        ("inside: the environment holds only the image's own variables and the ones this run "
         "released by name"
         if not leaked else f"inside: unexpected variable NAMES are present: {leaked}"),
        detail={"unexpected_names": leaked, "released": sorted(ctx.declared.get("env_names", ()))})


def check_secrets_refused_without_egress(ctx: Context) -> CheckResult:
    refused = ctx.host.get("passthrough_refused")
    if refused is None:
        return CheckResult.not_checked(
            "secrets-refused-without-egress", "Passing a name needs the restricted network",
            "secrets", "the refusal path was not exercised")
    return CheckResult.claimed(
        "secrets-refused-without-egress", "Passing a name needs the restricted network", "secrets",
        bool(refused),
        ("the SDK refused to build a command that passes a variable name on a network other than "
         "the destination-limited one. This is the SDK refusing, not the boundary enforcing, so it "
         "is recorded as self-reported"
         if refused else "the SDK built such a command instead of refusing"),
        detail={"refused": refused})


def check_credentials_destroyed(ctx: Context) -> CheckResult:
    left = ctx.host.get("leftovers")
    if left is None:
        return CheckResult.not_checked(
            "credentials-destroyed", "Nothing of the run survives it", "credentials",
            "the runtime could not be asked what remained after the run")
    remaining = [x for x in (left.get("containers", []) + left.get("networks", [])) if x]
    return CheckResult.measured(
        "credentials-destroyed", "Nothing of the run survives it", "credentials",
        not remaining, Vantage.OUTSIDE,
        ("the runtime lists no container, network or proxy belonging to this run afterwards"
         if not remaining else f"the runtime still lists {remaining} after the run"),
        detail=dict(left))


def check_cancel_and_kill(ctx: Context) -> CheckResult:
    s = ctx.stress.get("wallclock")
    left = ctx.host.get("leftovers")
    if not isinstance(s, dict) or left is None:
        return CheckResult.not_checked(
            "cancel-and-kill", "A stopped run leaves nothing behind", "lifecycle",
            "no stopped run was observed, or the runtime could not be asked what remained")
    ok = bool(s.get("killed")) and not left.get("containers")
    return CheckResult.measured(
        "cancel-and-kill", "A stopped run leaves nothing behind", "lifecycle", ok, Vantage.OUTSIDE,
        (f"the run was stopped after {s.get('elapsed')}s and the runtime lists "
         f"{left.get('containers') or 'no container'} from it afterwards"),
        detail={"stress": s, "leftovers": left})


def check_backend_loss(ctx: Context) -> CheckResult:
    lost = ctx.host.get("backend_loss")
    if lost is None:
        return CheckResult.not_checked(
            "backend-loss", "Losing the backend refuses rather than falls back", "availability",
            "the backend-loss path was not exercised")
    return CheckResult.claimed(
        "backend-loss", "Losing the backend refuses rather than falls back", "availability",
        bool(lost.get("refused")),
        ("a backend pointed at a runtime that does not exist reported itself unavailable and "
         f"refused with {lost.get('error_type')!r} instead of running anything on the host"
         if lost.get("refused") else
         f"a backend with no runtime did not refuse: {lost}"),
        detail=dict(lost))


def check_log_retention(ctx: Context) -> CheckResult:
    contract = ctx.host.get("retention")
    if contract is None:
        return CheckResult.not_applicable(
            "log-retention", "The log and retention contract holds", "logging",
            "this backend runs on the machine it was started from: it sends no content anywhere, "
            "so there is no retention period to keep. A remote backend has one and this check "
            "will apply to it")
    return CheckResult.claimed(
        "log-retention", "The log and retention contract holds", "logging",
        bool(contract.get("declared")),
        f"the backend declares {contract!r}; a declaration is not a measurement",
        detail=dict(contract))


def check_errors_are_usable(ctx: Context) -> CheckResult:
    message = ctx.host.get("refusal_message")
    if message is None:
        return CheckResult.not_checked(
            "errors-are-usable", "A refusal says what to do next", "errors",
            "no refusal was produced to inspect")
    has_action = bool(re.search(r"install|pull|start|set |run |see |agentnode ", message, re.I))
    long_enough = len(message.strip()) > 30
    looks_like_a_secret = bool(re.search(r"[A-Za-z0-9_-]{32,}", message))
    ok = has_action and long_enough and not looks_like_a_secret
    return CheckResult.claimed(
        "errors-are-usable", "A refusal says what to do next", "errors", ok,
        (f"the refusal reads {message.strip()[:120]!r}: it names a next step and carries nothing "
         "that looks like a secret"
         if ok else
         f"the refusal reads {message.strip()[:120]!r}: "
         + ("it names no next step" if not has_action else "")
         + (" it is too short to help" if not long_enough else "")
         + (" it contains something shaped like a secret" if looks_like_a_secret else "")),
        detail={"length": len(message), "names_an_action": has_action,
                "secret_shaped": looks_like_a_secret})


#: Every check, in report order. ``required`` marks the ones a conformant report must carry.
REGISTRY = (
    ("identity", check_identity, True),
    ("outside-host-process", check_outside_host_process, True),
    ("not-root", check_not_root, True),
    ("read-only-root", check_read_only_root, True),
    ("declared-mounts-only", check_declared_mounts_only, True),
    ("no-runtime-socket", check_no_runtime_socket, True),
    ("capabilities-dropped", check_capabilities_dropped, True),
    ("no-new-privileges", check_no_new_privileges, True),
    ("network-mode", check_network_mode, True),
    ("egress-allowlist", check_egress_allowlist, True),
    ("limit-memory", check_limit_memory, True),
    ("limit-pids", check_limit_pids, True),
    ("limit-cpu", check_limit_cpu, True),
    ("limit-disk", check_limit_disk, True),
    ("limit-wallclock", check_limit_wallclock, True),
    ("clean-home", check_clean_home, True),
    ("secrets-only-by-release", check_secrets_only_by_release, True),
    ("secrets-refused-without-egress", check_secrets_refused_without_egress, True),
    ("credentials-destroyed", check_credentials_destroyed, True),
    ("cancel-and-kill", check_cancel_and_kill, True),
    ("backend-loss", check_backend_loss, True),
    ("log-retention", check_log_retention, True),
    ("errors-are-usable", check_errors_are_usable, True),
)


def run_all(ctx: Context) -> tuple:
    """Every check, in order. A check that raises becomes a probe error, never a backend verdict."""
    out = []
    for check_id, fn, required in REGISTRY:
        try:
            result = fn(ctx)
        except Exception as exc:                                    # noqa: BLE001 - deliberate
            result = CheckResult.probe_error(
                check_id, check_id.replace("-", " ").capitalize(), "suite",
                f"the check itself raised {type(exc).__name__}: {str(exc)[:160]}. This is a defect "
                "in the suite, not a finding about the backend")
        if result.required != required:
            result = CheckResult(result.check_id, result.title, result.family, result.outcome,
                                 result.assurance, result.vantage, result.evidence, required,
                                 result.detail, result.attestation)
        out.append(result)
    return tuple(out)
