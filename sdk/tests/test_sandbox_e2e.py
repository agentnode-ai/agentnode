"""Sprint A — REAL sandbox end-to-end (needs Docker/Podman + the pinned image).

Gated: runs only when AGENTNODE_SANDBOX_E2E=1 AND a real ContainerBackend reports
available (runtime present + pinned image pulled). Otherwise skipped — this never
runs in the normal mocked unit suite.

These tests exercise exactly what P0.0–P0.3 mocked, so they validate the two real
gotchas the mocks could not catch:
  * Bug 1 — the extracted artifact dir (mkdtemp 0700) must be made readable for
    the container's uid 1000 before the /src:ro mount.
  * Bug 2 — the build writes to the /install named volume as --user 1000:1000, so
    the image must pre-own /install (chown 1000:1000).

Run on the Docker host:
    AGENTNODE_SANDBOX_E2E=1 python -m pytest tests/test_sandbox_e2e.py -v
"""
from __future__ import annotations

import os
import subprocess
import textwrap

import pytest

from agentnode_sdk.sandbox import sandbox_volume_name, set_default_backend
from agentnode_sdk.sandbox.container_backend import ContainerBackend

_E2E = os.environ.get("AGENTNODE_SANDBOX_E2E") == "1"

pytestmark = pytest.mark.skipif(
    not _E2E,
    reason="set AGENTNODE_SANDBOX_E2E=1 to run real sandbox E2E (needs Docker/Podman + pinned image)",
)


@pytest.fixture(autouse=True)
def _real_default_backend():
    """Use the REAL ContainerBackend (overrides the conftest fake). Skip loudly if
    no runtime / image — so the operator knows the image isn't pulled yet."""
    be = ContainerBackend()
    av = be.check_available()
    if not av.available:
        pytest.skip(
            f"sandbox not available (runtime/image missing): {av.reason or 'unknown'} "
            "— build+push+pin the image and `agentnode sandbox pull` first"
        )
    set_default_backend(be)
    yield
    set_default_backend(None)


def _runtime() -> str:
    return ContainerBackend().check_available().backend or "docker"


def _rm_volume(name: str) -> None:
    subprocess.run([_runtime(), "volume", "rm", name], capture_output=True)


# --- Headline: real toolpack build into volume + run from volume read-only -----

def test_toolpack_build_and_run_real(tmp_path, monkeypatch):
    from agentnode_sdk import installer
    from tests.hostpolicy import run_python

    # A tiny self-contained toolpack source. run() also reports whether it can see
    # a host secret env var — it must NOT (host env is never passed into the run).
    pkg = tmp_path / "src"
    (pkg / "mytool").mkdir(parents=True)
    (pkg / "mytool" / "__init__.py").write_text("")
    (pkg / "mytool" / "tool.py").write_text(
        "import os\n"
        "def run(x):\n"
        "    return {'doubled': x * 2, 'saw_secret': os.environ.get('AGENTNODE_E2E_HOST_SECRET')}\n"
    )
    (pkg / "setup.py").write_text(textwrap.dedent("""
        from setuptools import setup, find_packages
        setup(name="mytool", version="0.0.1", packages=find_packages())
    """))

    slug, version, ahash = "e2e-pack", "0.0.1", "sha256:" + "a" * 64
    expected_vol = sandbox_volume_name(slug, version, ahash)

    # If a host secret leaks into the container, the test must catch it.
    monkeypatch.setenv("AGENTNODE_E2E_HOST_SECRET", "leak-me")

    # 1. Real containerized build into the volume (validates Bug 1 + Bug 2 fixes).
    volume = installer._container_build_into_volume(slug, version, pkg, ahash)
    assert volume == expected_vol
    try:
        # 2. Real run from the read-only volume via the SDK-free wrapper.
        entry = {
            "version": version, "package_type": "toolpack", "runtime": "python",
            "entrypoint": "mytool.tool:run", "trust_level": "verified",
            "artifact_hash": ahash, "sandboxed": True, "sandbox_volume": volume,
            "permissions": {"network_level": "none"},
        }
        res = run_python(slug, None, entry=entry, x=21)
        assert res.mode_used == "sandbox"
        assert res.success, f"sandbox run failed: {res.error}"
        assert res.result["doubled"] == 42                 # JSON stdin/stdout round-trip
        assert res.result["saw_secret"] is None            # host env isolated
    finally:
        _rm_volume(volume)


def test_run_failclosed_when_volume_removed(tmp_path):
    """A stale lockfile pointing at a missing volume → fail-closed, never host."""
    from tests.hostpolicy import run_python

    slug, version, ahash = "e2e-missing", "0.0.1", "sha256:" + "b" * 64
    entry = {
        "version": version, "package_type": "toolpack", "runtime": "python",
        "entrypoint": "x.y:run", "trust_level": "verified",
        "artifact_hash": ahash, "sandboxed": True,
        "sandbox_volume": sandbox_volume_name(slug, version, ahash),
        "permissions": {"network_level": "none"},
    }
    res = run_python(slug, None, entry=entry)
    assert res.success is False
    assert res.mode_used == "sandbox"
    assert "reinstall" in (res.error or "").lower() or "missing or stale" in (res.error or "").lower()


# --- Real MCP start in the container ------------------------------------------

_MCP_STUB = textwrap.dedent("""
    import sys, json
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if req.get("method") == "initialize":
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": req.get("id"),
                "result": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "serverInfo": {"name": "e2e-stub", "version": "0"}},
            }) + "\\n")
            sys.stdout.flush()
        # notifications/initialized and anything else: no id -> no response
""")


def test_mcp_starts_in_container_real():
    """A pinned, genuinely PREINSTALLED MCP server really starts in the container and
    completes the initialize handshake.

    EM2-E2E-FIXTURE-0002 option B. The earlier version of this test passed a floating
    command with no preinstall fields, which `build_mcp_launch_plan` refuses by design —
    a non-preinstalled MCP would have to fetch at runtime with an open network. That
    refusal is correct and is asserted through the public routing path in
    `test_shipped_default_live_paths.py`; it is not duplicated here.

    The volume is built by the PRODUCTION preinstall path, so the entry carries real
    preinstall intent and the entrypoint is the console script that path selects.
    `mcp-server-time` is pinned exactly because `mcp` itself exposes only a Typer CLI,
    which cannot answer an initialize request.
    """
    from agentnode_sdk import installer
    from agentnode_sdk.lock_integrity import seal_entry
    from agentnode_sdk.runtimes.mcp_launch import build_mcp_launch_plan
    from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess
    from tests.hostpolicy import decision

    slug, version = "e2e-mcp", "1.0"
    manager, package, pkg_version = "pypi", "mcp-server-time", "2026.8.18"

    volume, artifact_hash, preinstall_command = installer._container_build_mcp_volume(
        slug, version, manager, package, pkg_version)
    server = None
    try:
        entry = seal_entry({
            "trust_level": "verified",
            "version": version,
            "mcp_preinstalled": True,
            "mcp_preinstall": {"manager": manager, "package": package,
                               "version": pkg_version, "artifact_hash": artifact_hash},
            "mcp_sandbox_volume": volume,
            "mcp_preinstall_command": list(preinstall_command),
        })
        dec = decision("verified")
        plan = build_mcp_launch_plan(slug, entry, dec, backend_kind="docker")
        assert plan.boundary == "sandbox", plan.boundary

        server = MCPServerProcess(slug, list(preinstall_command),
                                  trust_level="verified", entry=entry)
        # start() performs the initialize request/response itself and raises
        # RuntimeError("... failed to initialize") if the server does not answer, so a
        # start that returns IS a completed handshake. The post-conditions below make
        # that observable rather than implicit.
        server.start(_host_policy_decision=dec, launch_plan=plan)   # container + JSON-RPC

        assert server._container_name
        assert server._container_name.startswith("agentnode-mcp-"), server._container_name
        # the server survived the handshake and is still the live process in the container
        assert server.health_check() is True
    finally:
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass
        subprocess.run(["docker", "volume", "rm", "-f", volume],
                       capture_output=True, timeout=60)


def test_mcp_preinstall_cache_lands_only_in_private_tmp():
    """In-container proof that the managers' caches really land under /tmp and that the
    16 MiB HOME stays empty.

    EM2-CACHE-VARS-0011 required an in-container observation for this: a ProcessSpec
    assertion pins the assignment but cannot show where bytes actually go. This runs the
    SAME spec shape the MCP build uses — clean HOME, 512 MiB /tmp, the two cache
    variables — installs a package with a real dependency tree, and then reports the
    byte counts under each root.
    """
    from agentnode_sdk.sandbox import get_default_backend
    from agentnode_sdk.sandbox.types import MountSpec

    backend = get_default_backend()
    volume = "agentnode-e2e-cacheprobe"
    subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=60)
    probe = (
        "set -e; "
        "uv pip install --target /install 'mcp-server-time==2026.8.18' 1>&2; "
        "echo HOMEBYTES:$(du -sb \"$HOME\" 2>/dev/null | cut -f1); "
        "echo UVBYTES:$(du -sb /tmp/uv-cache 2>/dev/null | cut -f1 || echo 0); "
        "echo HOMECACHE:$(test -e \"$HOME/.cache\" && echo present || echo absent)"
    )
    try:
        spec = backend.build_process_spec(
            ["sh", "-c", probe],
            network="default",
            mounts=[MountSpec(src=volume, dst="/install", read_only=False)],
            env={"UV_CACHE_DIR": "/tmp/uv-cache", "npm_config_cache": "/tmp/npm-cache"},
            limits={"tmp_size": "512m"},
            clean_home=True,
        )
        rc, out, err = backend.run_process(spec, timeout=600)
        assert rc == 0, f"probe failed ({rc}): {(err or out)[-800:]}"

        vals = {}
        for line in (out or "").splitlines():
            for key in ("HOMEBYTES:", "UVBYTES:", "HOMECACHE:"):
                if line.startswith(key):
                    vals[key.rstrip(":")] = line[len(key):].strip()
        assert "UVBYTES" in vals and "HOMEBYTES" in vals, out

        # the cache is where we put it, and it is not trivial
        assert int(vals["UVBYTES"]) > 1_000_000, vals
        # HOME never received a cache directory at all
        assert vals.get("HOMECACHE") == "absent", vals
        # and HOME stayed far below its 16 MiB budget
        assert int(vals["HOMEBYTES"]) < 1_000_000, vals
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=60)


def test_mcp_preinstall_fails_closed_on_an_unusable_cache_path():
    """An unusable cache path must refuse, not silently seal a partial volume.

    The negative half of the correction: the same build with a cache directory pointed
    at a read-only location fails, and the volume is not left behind.
    """
    from agentnode_sdk.sandbox import get_default_backend
    from agentnode_sdk.sandbox.types import MountSpec

    backend = get_default_backend()
    volume = "agentnode-e2e-cachefail"
    subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=60)
    try:
        spec = backend.build_process_spec(
            ["sh", "-c", "set -e; uv pip install --target /install 'mcp-server-time==2026.8.18' 1>&2"],
            network="default",
            mounts=[MountSpec(src=volume, dst="/install", read_only=False)],
            # /proc is not writable inside the container: the manager cannot create a
            # cache there, so the build must fail rather than proceed.
            env={"UV_CACHE_DIR": "/proc/definitely-not-writable"},
            limits={"tmp_size": "512m"},
            clean_home=True,
        )
        rc, out, err = backend.run_process(spec, timeout=300)
        assert rc != 0, "an unusable cache path did not fail the build"
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=60)


# --- npm: the same three checks, for the OTHER supported manager branch ------
#
# EM2-NPM-EVIDENCE-0002, option 1. The final EM-2 review recorded one limitation: npm
# cache placement rested on production-source inspection and a ProcessSpec assertion,
# never on npm actually running in a container. These three close that gap and mirror
# the uv trio above.
#
# A purely local npm fixture cannot reach the production path: `_container_build_mcp_volume`
# installs `package@version` BY NAME from the registry and mounts no /src. The package
# below is therefore an exact registry pin — the smallest dependency closure of the
# candidates, with a bin and no preinstall/install/postinstall lifecycle script. Its
# version lives in `tests/lanes/npm_e2e_pins.json`, not in constraints.txt, which is a
# pip constraints file and cannot express an npm version.


def _npm_pin() -> tuple[str, str, str]:
    import json
    from pathlib import Path

    doc = json.loads(
        (Path(__file__).parent / "lanes" / "npm_e2e_pins.json").read_text(encoding="utf-8")
    )
    pin = doc["pins"]["mcp_server"]
    assert pin["manager"] == "npm", pin
    return pin["package"], pin["version"], pin["bin"]


def _host_npm_cache_state() -> tuple:
    """A cheap fingerprint of the host's own npm cache, so the container build can be
    shown not to have touched it."""
    home = os.path.expanduser("~")
    cache = os.path.join(home, ".npm")
    if not os.path.isdir(cache):
        return (False, None, None)
    entries = tuple(sorted(os.listdir(cache)))
    return (True, os.stat(cache).st_mtime_ns, entries)


def test_npm_mcp_starts_in_container_real(monkeypatch):
    """The npm branch of the PRODUCTION preinstall path: an exactly pinned registry
    package is installed inside the container into a sealed volume, and the MCP it
    provides really starts and completes the initialize handshake.

    The ProcessSpec the production helper actually hands to the runtime is intercepted
    on the way through, so what is asserted is the real one — not a reconstruction.
    """
    from agentnode_sdk import installer
    from agentnode_sdk.lock_integrity import seal_entry
    from agentnode_sdk.runtimes.mcp_launch import build_mcp_launch_plan
    from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess
    from agentnode_sdk.sandbox import get_default_backend
    from tests.hostpolicy import decision

    package, pkg_version, expected_bin = _npm_pin()
    slug, version = "e2e-npm-mcp", "1.0"

    backend = get_default_backend()
    captured: list = []
    real_run_process = backend.run_process

    def _capturing_run_process(spec, *a, **kw):
        captured.append(spec)
        return real_run_process(spec, *a, **kw)

    monkeypatch.setattr(backend, "run_process", _capturing_run_process)

    host_before = _host_npm_cache_state()
    volume, artifact_hash, preinstall_command = installer._container_build_mcp_volume(
        slug, version, "npm", package, pkg_version)
    server = None
    try:
        # --- the intercepted production spec really runs npm install --------------
        assert len(captured) == 1, captured
        spec = captured[0]
        script = " ".join(spec.command)
        assert f"npm install -g --prefix /install '{package}@{pkg_version}'" in script, script
        # it executed: the build returned a hash and an entrypoint, which only the
        # post-install tree-hasher can produce
        assert artifact_hash, "no artifact hash — the install did not run"

        # --- the cache is redirected, HOME keeps its hardened 16 MiB default -------
        assert spec.env["npm_config_cache"] == "/tmp/npm-cache", spec.env
        assert spec.clean_home is True
        assert "home_size" not in spec.limits, spec.limits
        assert spec.limits.get("tmp_size") == "512m", spec.limits
        argv = backend.wrap_command(spec)
        assert "/sandbox-home:rw,size=16m" in argv, argv

        # --- the only mount is the rw install volume; the cache is not mounted -----
        assert [(m.dst, m.read_only) for m in spec.mounts] == [("/install", False)], spec.mounts

        # --- the pinned package produced exactly the expected entrypoint ----------
        assert preinstall_command == ["node", f"/install/bin/{expected_bin}"], preinstall_command

        # --- and it starts, initializes and stays healthy in a real container -----
        entry = seal_entry({
            "trust_level": "verified",
            "version": version,
            "mcp_preinstalled": True,
            "mcp_preinstall": {"manager": "npm", "package": package,
                               "version": pkg_version, "artifact_hash": artifact_hash},
            "mcp_sandbox_volume": volume,
            "mcp_preinstall_command": list(preinstall_command),
        })
        dec = decision("verified")
        plan = build_mcp_launch_plan(slug, entry, dec, backend_kind="docker")
        assert plan.boundary == "sandbox", plan.boundary

        server = MCPServerProcess(slug, list(preinstall_command),
                                  trust_level="verified", entry=entry)
        # start() performs the initialize request/response and raises if unanswered,
        # so a start that returns IS a completed handshake.
        server.start(_host_policy_decision=dec, launch_plan=plan)
        assert server._container_name
        assert server._container_name.startswith("agentnode-mcp-"), server._container_name
        assert server.health_check() is True

        # --- nothing of the host's own npm cache moved ---------------------------
        assert _host_npm_cache_state() == host_before, "the host npm cache changed"
    finally:
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass
        _rm_volume(volume)


def test_npm_preinstall_cache_lands_only_in_private_tmp():
    """In-container observation of npm itself: the cache lands under /tmp/npm-cache on
    the container's own tmpfs, `$HOME/.npm` never appears at all, HOME stays inside its
    16 MiB budget, and nothing survives into the next sandbox.

    The byte bound and the absence check are separate on purpose: a bound alone could
    conceal a small npm cache written into HOME.
    """
    from agentnode_sdk.sandbox import get_default_backend
    from agentnode_sdk.sandbox.types import MountSpec

    package, pkg_version, _bin = _npm_pin()
    backend = get_default_backend()
    volume = "agentnode-e2e-npm-cacheprobe"
    _rm_volume(volume)
    probe = (
        "set -e; "
        f"npm install -g --prefix /install '{package}@{pkg_version}' 1>&2; "
        'echo HOMEBYTES:$(du -sb "$HOME" 2>/dev/null | cut -f1); '
        "echo NPMBYTES:$(du -sb /tmp/npm-cache 2>/dev/null | cut -f1 || echo 0); "
        'echo HOMENPM:$(test -e "$HOME/.npm" && echo present || echo absent); '
        "echo CACHEFS:$(stat -f -c %T /tmp/npm-cache); "
        "echo CACHEMOUNTED:$(grep -c ' /tmp/npm-cache ' /proc/mounts || true)"
    )
    try:
        spec = backend.build_process_spec(
            ["sh", "-c", probe],
            network="default",
            mounts=[MountSpec(src=volume, dst="/install", read_only=False)],
            env={"UV_CACHE_DIR": "/tmp/uv-cache", "npm_config_cache": "/tmp/npm-cache"},
            limits={"tmp_size": "512m"},
            clean_home=True,
        )
        rc, out, err = backend.run_process(spec, timeout=900)
        assert rc == 0, f"npm probe failed ({rc}): {(err or out)[-1200:]}"

        vals = {}
        for line in (out or "").splitlines():
            key, sep, val = line.partition(":")
            if sep and key in ("HOMEBYTES", "NPMBYTES", "HOMENPM", "CACHEFS", "CACHEMOUNTED"):
                vals[key] = val.strip()
        assert {"HOMEBYTES", "NPMBYTES", "HOMENPM", "CACHEFS"} <= set(vals), out

        # npm really cached, and it cached where the build points it
        assert int(vals["NPMBYTES"]) > 100_000, vals
        # $HOME/.npm is absolutely absent — not merely small
        assert vals["HOMENPM"] == "absent", vals
        # and HOME as a whole stayed inside its 16 MiB budget
        assert int(vals["HOMEBYTES"]) < 16 * 1024 * 1024, vals
        # the cache is on the container's own tmpfs, and is not a mount of anything
        assert vals["CACHEFS"] == "tmpfs", vals
        assert vals.get("CACHEMOUNTED", "0") == "0", vals
    finally:
        _rm_volume(volume)

    # --- and it is not shared: a sentinel in one sandbox is invisible to the next ---
    env = {"npm_config_cache": "/tmp/npm-cache"}
    write = backend.build_process_spec(
        ["sh", "-c", "mkdir -p /tmp/npm-cache && echo sentinel > /tmp/npm-cache/AGENTNODE_SENTINEL "
                     "&& echo WROTE:$(cat /tmp/npm-cache/AGENTNODE_SENTINEL)"],
        network="none", mounts=[], env=env, limits={"tmp_size": "512m"}, clean_home=True,
    )
    rc, out, err = backend.run_process(write, timeout=120)
    assert rc == 0 and "WROTE:sentinel" in (out or ""), (rc, out, err)

    read = backend.build_process_spec(
        ["sh", "-c", "test -e /tmp/npm-cache/AGENTNODE_SENTINEL && echo LEAKED || echo ISOLATED"],
        network="none", mounts=[], env=env, limits={"tmp_size": "512m"}, clean_home=True,
    )
    rc, out, err = backend.run_process(read, timeout=120)
    assert rc == 0, (rc, out, err)
    assert "ISOLATED" in (out or ""), f"the npm cache leaked between sandboxes: {out!r}"


def test_npm_preinstall_fails_closed_on_an_unusable_cache_path(monkeypatch):
    """With the npm cache pointed at an unwritable path, the PRODUCTION build must fail
    and leave no sealed volume behind — never fall back to caching in HOME."""
    from agentnode_sdk import installer
    from agentnode_sdk.sandbox import get_default_backend
    from agentnode_sdk.sandbox.container_backend import mcp_sandbox_volume_name

    package, pkg_version, _bin = _npm_pin()
    slug, version = "e2e-npm-cachefail", "1.0"
    backend = get_default_backend()
    bad = "/proc/definitely-not-writable"

    # the path is demonstrably unwritable in this very image, not merely assumed to be
    probe = backend.build_process_spec(
        ["sh", "-c", f"mkdir -p {bad}"], network="none", clean_home=True)
    rc, _o, _e = backend.run_process(probe, timeout=120)
    assert rc != 0, f"{bad} turned out to be writable — the negative control is void"

    real_build_spec = backend.build_process_spec

    def _redirect_cache(command, **kw):
        env = dict(kw.pop("env", None) or {})
        if "npm_config_cache" in env:
            env["npm_config_cache"] = bad
        return real_build_spec(command, env=env, **kw)

    monkeypatch.setattr(backend, "build_process_spec", _redirect_cache)

    volume = mcp_sandbox_volume_name(slug, version, "npm", package, pkg_version)
    try:
        with pytest.raises(RuntimeError, match="MCP pre-install build failed"):
            installer._container_build_mcp_volume(slug, version, "npm", package, pkg_version)
        # nothing sealed: the volume the build would have produced does not exist
        inspect = subprocess.run([_runtime(), "volume", "inspect", volume],
                                 capture_output=True, timeout=60)
        assert inspect.returncode != 0, f"a volume was left behind: {volume}"
    finally:
        _rm_volume(volume)
