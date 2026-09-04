"""EM-3B: the program that runs INSIDE the sandbox and reports what it can see.

This is the only vantage that answers the question the suite is actually asking -- not "what was
the container configured with" but "what does untrusted code find when it gets there". It ships as
source and is handed to the payload as ``python -c <source>``, the way the runners already pass
their wrappers: no mount to add, and nothing to write on a read-only root.

Three rules it obeys, because the alternative is a suite that lies in the reassuring direction:

* **Every reading is isolated.** One reading that raises records its own error and the rest still
  run. A reading that failed is reported as a failed reading, never as an absent danger.
* **It never reads a value out of the environment.** Names only. A probe that printed a secret to
  prove the secret was there would be the exact leak the sandbox exists to prevent.
* **It measures, it does not conclude.** Every judgement is made by the checks on the host side,
  from these readings. The probe has no idea what "pass" would mean.
"""
from __future__ import annotations

PROBE_VERSION = "em3b.probe.1"

# Socket paths that would hand the host over if any of them were reachable from in here.
RUNTIME_SOCKETS = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/var/run/podman/podman.sock",
    "/run/podman/podman.sock",
    "/run/user/1000/podman/podman.sock",
    "/var/run/crio/crio.sock",
    "/run/containerd/containerd.sock",
)

# Things a host home directory has and a clean one does not.
HOST_ARTEFACTS = (".ssh", ".aws", ".gnupg", ".config", ".docker", ".kube", ".netrc",
                  ".agentnode", ".bash_history", ".gitconfig")

PROBE_SOURCE = r'''
import json, os, socket, sys, time

V = "em3b.probe.1"
SOCKETS = %(sockets)r
HOST_ARTEFACTS = %(artefacts)r
opts = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
R = {"probe_version": V}


def take(name, fn):
    """Run one reading. A reading that raises records its own failure and nothing else's."""
    try:
        R[name] = fn()
    except Exception as exc:                                    # noqa: BLE001 - deliberate
        R[name] = {"_error": type(exc).__name__, "_detail": str(exc)[:200]}


def read_text(path, limit=65536):
    with open(path, "r", errors="replace") as fh:
        return fh.read(limit)


def identity():
    return {"uid": os.getuid(), "gid": os.getgid(), "euid": os.geteuid(),
            "pid": os.getpid(), "hostname": socket.gethostname(),
            "python": sys.version.split()[0]}


def proc_status():
    wanted = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs", "Seccomp")
    out = {}
    for line in read_text("/proc/self/status").splitlines():
        key, _, value = line.partition(":")
        if key in wanted:
            out[key] = value.strip()
    return out


def containment_markers():
    """Signals that this is not the host's process tree."""
    out = {"dockerenv": os.path.exists("/.dockerenv"),
           "containerenv": os.path.exists("/run/.containerenv")}
    try:
        out["self_cgroup"] = read_text("/proc/self/cgroup", 4096).strip()[:400]
    except Exception as exc:                                    # noqa: BLE001
        out["self_cgroup_error"] = type(exc).__name__
    try:
        # In the host's PID namespace this is the init system. In a fresh namespace it is us,
        # or at least not the host's init.
        out["pid1_comm"] = read_text("/proc/1/comm", 128).strip()
    except Exception as exc:                                    # noqa: BLE001
        out["pid1_comm_error"] = type(exc).__name__
    return out


def rootfs_write():
    """Try to write where a read-only root must refuse. Cleans up if it wrongly succeeds."""
    path = "/agentnode-conformance-probe-write-test"
    try:
        with open(path, "w") as fh:
            fh.write("x")
    except Exception as exc:                                    # noqa: BLE001
        return {"denied": True, "error": type(exc).__name__}
    try:
        os.unlink(path)
    except Exception:                                           # noqa: BLE001
        pass
    return {"denied": False, "error": None}


def mounts():
    out = []
    for line in read_text("/proc/mounts").splitlines()[:200]:
        parts = line.split()
        if len(parts) >= 4:
            out.append({"source": parts[0], "target": parts[1], "fstype": parts[2],
                        "options": parts[3][:120]})
    return out


def sockets():
    out = {}
    for path in SOCKETS:
        entry = {"exists": os.path.exists(path)}
        if entry["exists"]:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect(path)          # connect only; no API call is ever made through it
                s.close()
                entry["connect"] = "CONNECTED"
            except Exception as exc:                            # noqa: BLE001
                entry["connect"] = "refused:" + type(exc).__name__
        else:
            entry["connect"] = "absent"
        out[path] = entry
    return out


def home():
    path = os.environ.get("HOME", "")
    out = {"path": path, "is_dir": os.path.isdir(path) if path else False}
    if out["is_dir"]:
        entries = sorted(os.listdir(path))[:100]
        out["entries"] = entries
        out["host_artefacts"] = [a for a in HOST_ARTEFACTS if a in entries]
        try:
            st = os.statvfs(path)
            out["fs_bytes"] = st.f_blocks * st.f_frsize
        except Exception as exc:                                # noqa: BLE001
            out["fs_bytes_error"] = type(exc).__name__
        try:
            probe = os.path.join(path, ".conformance-write-test")
            with open(probe, "w") as fh:
                fh.write("x")
            os.unlink(probe)
            out["writable"] = True
        except Exception:                                       # noqa: BLE001
            out["writable"] = False
    return out


def cgroup_limits():
    out = {}
    for key, path in (("memory_max", "/sys/fs/cgroup/memory.max"),
                      ("memory_max_v1", "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
                      ("pids_max", "/sys/fs/cgroup/pids.max"),
                      ("pids_max_v1", "/sys/fs/cgroup/pids/pids.max"),
                      ("cpu_max", "/sys/fs/cgroup/cpu.max"),
                      ("cpu_quota_v1", "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
                      ("cpu_period_v1", "/sys/fs/cgroup/cpu/cpu.cfs_period_us")):
        try:
            out[key] = read_text(path, 256).strip()
        except Exception:                                       # noqa: BLE001
            pass
    return out


def filesystems():
    out = {}
    for path in ("/", "/tmp", os.environ.get("HOME", "/tmp")):
        try:
            st = os.statvfs(path)
            out[path] = {"bytes": st.f_blocks * st.f_frsize,
                         "free": st.f_bavail * st.f_frsize}
        except Exception as exc:                                # noqa: BLE001
            out[path] = {"_error": type(exc).__name__}
    return out


def env_names():
    """NAMES ONLY. A probe that printed a value would be the leak this suite exists to catch."""
    return sorted(os.environ.keys())


def network():
    """Bounded connection attempts. Each one is 'did this reach anything', never what came back."""
    out = {}
    def attempt(key, fn):
        start = time.time()
        try:
            fn()
            out[key] = "REACHED"
        except Exception as exc:                                # noqa: BLE001
            out[key] = "blocked:" + type(exc).__name__
        out[key + "_seconds"] = round(time.time() - start, 2)

    attempt("dns_example_com", lambda: socket.getaddrinfo("example.com", 443,
                                                          type=socket.SOCK_STREAM))
    for host in ("1.1.1.1", "8.8.8.8"):
        def connect(h=host):
            s = socket.create_connection((h, 443), timeout=opts.get("net_timeout", 3))
            s.close()
        attempt("direct_" + host.replace(".", "_"), connect)
    proxy = opts.get("proxy")
    if proxy:
        def via_proxy():
            host, _, port = proxy.partition(":")
            s = socket.create_connection((host, int(port or 8888)),
                                         timeout=opts.get("net_timeout", 3))
            s.close()
        attempt("proxy_reachable", via_proxy)
    return out


take("identity", identity)
take("proc_status", proc_status)
take("containment", containment_markers)
take("rootfs_write", rootfs_write)
take("mounts", mounts)
take("runtime_sockets", sockets)
take("home", home)
take("cgroup", cgroup_limits)
take("filesystems", filesystems)
take("env_names", env_names)
if opts.get("network", True):
    take("network", network)

print("AGENTNODE_CONFORMANCE " + json.dumps(R))
''' % {"sockets": RUNTIME_SOCKETS, "artefacts": HOST_ARTEFACTS}

MARKER = "AGENTNODE_CONFORMANCE "


def parse(stdout: str) -> dict:
    """Pull the readings out of the payload's stdout, or raise with what was there instead."""
    import json as _json
    for line in stdout.splitlines():
        if line.startswith(MARKER):
            return _json.loads(line[len(MARKER):])
    raise ValueError("the probe produced no readings line; stdout was: " + stdout[-400:].strip())


# --------------------------------------------------------------------------------------------
# The egress matrix. Run on the internal network with the proxy alongside, it answers the one
# question a hostname allowlist has to answer: is the boundary the topology, or a suggestion?
# Direct routes must be gone, the sealed name must work through the proxy, and an unsealed one
# must not. "BYPASS" is the word for a direct route that survived; nothing else is.
EGRESS_MATRIX_SOURCE = r'''
import json, os, socket, ssl, urllib.request

for key in list(os.environ):
    if key.lower() in ("http_proxy", "https_proxy", "no_proxy"):
        os.environ.pop(key, None)
R = {}
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def direct_ip(host, key):
    try:
        s = socket.create_connection((host, 443), timeout=8)
        s.close()
        R[key] = "BYPASS"
    except Exception as exc:
        R[key] = "blocked:" + type(exc).__name__


def direct_name(url, key):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPSHandler(context=ctx))
        r = opener.open(url, timeout=10)
        R[key] = "BYPASS:" + str(r.status)
    except Exception as exc:
        R[key] = "blocked:" + type(exc).__name__


def via_proxy(url, key):
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": "http://egress-proxy:8888"}),
            urllib.request.HTTPSHandler(context=ctx))
        r = opener.open(url, timeout=15)
        R[key] = "ALLOWED:" + str(r.status)
    except Exception as exc:
        R[key] = "refused:" + type(exc).__name__


direct_ip("1.1.1.1", "direct_1_1_1_1")
direct_ip("8.8.8.8", "direct_8_8_8_8")
direct_name("https://%(allowed)s", "direct_unproxied")
via_proxy("https://%(allowed)s", "allowed_via_proxy")
via_proxy("https://%(denied)s", "denied_via_proxy")
print("AGENTNODE_EGRESS_MATRIX " + json.dumps(R))
'''

EGRESS_MARKER = "AGENTNODE_EGRESS_MATRIX "


def egress_matrix_source(allowed: str, denied: str) -> str:
    return EGRESS_MATRIX_SOURCE % {"allowed": allowed, "denied": denied}


def parse_egress(stdout: str) -> dict:
    import json as _json
    for line in stdout.splitlines():
        if line.startswith(EGRESS_MARKER):
            return _json.loads(line[len(EGRESS_MARKER):])
    raise ValueError("the egress matrix produced no result line; stdout was: "
                     + stdout[-400:].strip())
