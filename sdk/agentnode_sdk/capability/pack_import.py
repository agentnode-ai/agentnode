"""Import ONE AgentNode registry pack as ONE executable capability (Registry-Pack -> Capability).

Input is what an AgentNode install already holds for a pack: the registry artifact bytes (the
``.tar.gz`` the registry serves) and the pack's ``agentnode.lock`` entry. Output is one signed
``agentnode.capability.v1`` manifest plus one deterministic ``vendored-tree`` blob that the
existing index, approval, worker and audit machinery run unchanged.

Every step refuses rather than repairs, and every refusal is a ``PackRefused`` with a stable
``code``:

1. **Lock binding.** The entry is the one for the requested package key; its ``_integrity`` is
   well-formed and verifies; its version and ``sha256:<hex>`` artifact hash are exact. Lock
   integrity is an UNKEYED drift check, not authentication -- it is one layer next to the
   artifact hash, the manifest/lock comparison, the content digest and the local signature.
   ``trust_level`` is never consulted: it is mutable and not covered by the integrity hash.
2. **Artifact.** The bytes hash to the lock's artifact hash; the archive is a gzip tar of
   regular files and directories only, under exactly one root named ``<package_id>``, within
   member-count, per-file and TOTAL size limits.
3. **Manifest.** ``agentnode.yaml`` is valid under the backend's rules for the admitted subset
   (manifest 0.1/0.2, ``toolpack`` + ``python`` + ``package``) and names the requested id and
   version. 0.3 is refused: the backend validates its entrypoints with the 0.1 rules.
4. **Permissions.** Only network ``none`` with no domains, filesystem ``none``, code_execution
   ``none``, data_access ``input_only``, and no secrets, connector, MCP server or external
   integration. Anything the pack declares beyond that, the frozen sandbox cannot measurably
   grant here -- so it is refused, never silently narrowed.
5. **Lock <-> manifest.** Every security-relevant field the lock records agrees with the
   manifest (type, runtime, entrypoints, tools, flattened permissions, publisher), and nothing
   the lock records for MCP, remote endpoints, credentials or agents is present.
6. **Dependencies and layout.** ``pyproject.toml`` declares no runtime or optional
   dependencies; the runtime package ``src/<module>/`` holds only ``.py`` files; every static
   and constant dynamic import resolves to an ADMITTED module of the sandbox Python's standard
   library or to the pack itself; network and process modules are refused for a pack that
   declared neither; non-constant dynamic imports, ``exec``/``eval``/``compile``, reflective
   routes around the scan, file writes and compiled/native files are refused.
7. **Entrypoints.** Each tool resolves -- by the installer's own semantics (``m.tool`` ->
   ``run``; ``m.tool:f`` -> ``f``) -- to one top-level ``def`` in a shipped module.

Nothing in the pack is EXECUTED on the importing host. The tool table is derived from the
manifest; the generated shim (``_pack_shim``) is re-read with ``ast`` and its embedded table
compared with that derivation. The real MCP handshake happens only in the worker's sandbox.

The executable tree is ``entry.py`` (the generated shim) + the runtime package byte-unchanged +
``agentnode-pack.json`` (content-derived provenance), packed with ``tree_pack``. The SAME
logical pack content gives the SAME tree bytes and digest, even though the registry's gzip
artifact is not byte-reproducible; the artifact-byte hash is bound only in the SIGNED
capability provenance, as the identity of the artifact actually received.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentnode_sdk import lock_integrity
from agentnode_sdk.capability import _pack_shim, tree_pack
from agentnode_sdk.capability import manifest as M
from agentnode_sdk.capability._sandbox_stdlib import SANDBOX_STDLIB

IMPORTER_VERSION = "agentnode.pack-import.v1"
#: The signed-provenance fields that identify the ARTIFACT INSTANCE received, not the logical
#: pack content: the registry's gzip archive is not byte-reproducible (its header carries a
#: build time), so two builds of identical content differ exactly here -- and nowhere else.
#: The lock's integrity hash is one of them because the lock records the artifact hash.
ARTIFACT_INSTANCE_FIELDS = ("artifact_sha256", "artifact_bytes", "lock_integrity")
TREE_PROVENANCE_FILE = "agentnode-pack.json"
SHIM_ENTRY = "entry.py"
INTERPRETER = "python3"

MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_MEMBERS = 500
MAX_MEMBER_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024

# --- the backend's manifest rules for the admitted subset (backend/app/packages/validator.py) ---
# Copied, not imported: the SDK cannot import the backend. Anything outside the admitted subset
# is refused below, so a looser backend rule can never admit more here.
_SLUG = re.compile(r"^[a-z0-9-]{3,60}$")  # validator.py:23
_SEMVER_STRICT = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
)  # capability.v1 versions
_ENTRYPOINT_V1 = re.compile(
    r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$"
)  # validator.py:27
_ENTRYPOINT_V2 = re.compile(
    r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+:[a-z_][a-z0-9_]*$"
)  # :29
_NETWORK_LEVELS = {"none", "restricted", "unrestricted"}  # validator.py:17
_FS_LEVELS = {"none", "temp", "workspace_read", "workspace_write", "any"}  # :18
_EXEC_LEVELS = {"none", "limited_subprocess", "shell"}  # :19
_DATA_LEVELS = {"input_only", "connected_accounts", "persistent"}  # :20
_APPROVAL_LEVELS = {"always", "high_risk_only", "once", "never"}  # :21
_JSON_SCHEMA_TYPES = {
    "string",
    "integer",
    "number",
    "boolean",
    "array",
    "object",
    "null",
}  # :36
_V02_PERMISSION_DEFAULTS = {  # validator.py:55-62 (normalize_manifest, 0.2 only)
    "network": {"level": "none", "allowed_domains": []},
    "filesystem": {"level": "none"},
    "code_execution": {"level": "none"},
    "data_access": {"level": "input_only"},
    "user_approval": {"required": "never"},
    "external_integrations": [],
}

# --- what this slice can honestly run: dependency-free, network-free, secret-free python ---
_ROOT_FILES_REQUIRED = ("agentnode.yaml", "pyproject.toml")
_ROOT_FILES_OPTIONAL = {
    "README",
    "README.md",
    "README.rst",
    "README.txt",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "CHANGELOG.md",
}
_COMPILED_OR_NATIVE = {
    ".pyc",
    ".pyo",
    ".so",
    ".pyd",
    ".dll",
    ".dylib",
    ".exe",
    ".a",
    ".o",
}
#: Standard-library modules that reach the network. A pack that declared network ``none`` may
#: not import them -- the sandbox would refuse the traffic anyway; the import is refused first.
_NETWORK_MODULES = (
    "socket",
    "_socket",
    "ssl",
    "_ssl",
    "socketserver",
    "http.client",
    "http.server",
    "http.cookiejar",
    "urllib.request",
    "urllib.robotparser",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "nntplib",
    "telnetlib",
    "xmlrpc",
    "asyncio",
    "webbrowser",
    "wsgiref",
)
#: Standard-library modules that start other programs. Refused for code_execution ``none``.
_PROCESS_MODULES = (
    "subprocess",
    "_posixsubprocess",
    "multiprocessing",
    "concurrent.futures.process",
    "pty",
    "ctypes",
    "_ctypes",
)
#: Loaders that import by computed name. Refused: an import this importer cannot see is an
#: undeclared dependency it cannot rule out.
_DYNAMIC_IMPORT_MODULES = (
    "importlib.util",
    "importlib.machinery",
    "importlib.abc",
    "imp",
    "runpy",
    "pkgutil",
    "zipimport",
    # reflection doors: the builtins namespace, live frames, and every object in the process
    "builtins",
    "inspect",
    "gc",
)
#: Standard-library modules that turn data into running code. Refused for code_execution ``none``.
_CODE_EXEC_MODULES = (
    "pickle",
    "_pickle",
    "marshal",
    "shelve",
    "code",
    "codeop",
    "pdb",
    "bdb",
)
_DYNAMIC_CODE_CALLS = ("exec", "eval", "compile")

# --- the static scan must not be walkable around (review R0001 F-P5) ---------------------------
# A syntactic scan sees ``exec(...)`` but not ``builtins.exec(...)``, ``getattr(x, "__import__")``
# or ``().__class__.__base__.__subclasses__()``. So the routes TO those objects are closed instead:
# * only an ALLOW-list of stdlib modules may be imported (``_ADMITTED_STDLIB``) -- the stdlib's
#   own code runners (timeit, doctest, cProfile, pydoc.locate, logging.config, ...) cannot be
#   enumerated reliably, so they are simply not admitted;
# * the builtins namespace is unreachable (``builtins``/``__builtins__``/globals/locals/vars);
# * dunder attributes are refused except a short list of ordinary ones, and attribute names that
#   reach frames, the module table, the import machinery, computed code, new processes or a
#   re-exported module (``os.path.os``) are refused in ANY attribute or from-import position;
# * reflective attribute access (getattr & co.) needs a constant, harmless name and may not be
#   passed on or aliased;
# * writing files is refused (``open``, os/io file creation, file log handlers): a written file
#   could be imported as code the scan never saw, and the pack declared filesystem none.
# This is a source gate for dependency-free packs, not a proof that arbitrary Python is inert: the
# sandbox (network none, no third-party import paths) remains the containment.
_REFLECTIVE_NAMES = {
    "__builtins__",
    "__loader__",
    "__spec__",
    "globals",
    "locals",
    "vars",
    "breakpoint",
    "attrgetter",
    "methodcaller",
}
_REFLECTIVE_CALLS = {"getattr", "setattr", "delattr", "hasattr"}
_SAFE_DUNDERS = {
    "__name__",
    "__doc__",
    "__qualname__",
    "__module__",
    "__version__",
    "__all__",
    "__annotations__",
    "__init__",
    "__post_init__",
    "__enter__",
    "__exit__",
    "__iter__",
    "__next__",
    "__len__",
    "__str__",
    "__repr__",
    "__eq__",
    "__ne__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__hash__",
    "__contains__",
    "__getitem__",
    "__setitem__",
    "__call__",
    "__bool__",
}
#: Attribute names that run code or start processes, wherever they appear.
_CODE_EXEC_ATTRIBUTES = {
    "exec",
    "eval",
    "system",
    "popen",
    "startfile",
    "fork",
    "forkpty",
    "posix_spawn",
    "posix_spawnp",
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "CodeType",
    "ProcessPoolExecutor",
    # string annotations are evaluated with eval() by these
    "get_type_hints",
    "ForwardRef",
    "_eval_type",
    "_evaluate",
}
#: Attribute names that reach frames, the module table or the import machinery.
_REFLECTIVE_ATTRIBUTES = {
    "_getframe",
    "_current_frames",
    "f_globals",
    "f_builtins",
    "f_locals",
    "f_back",
    "f_code",
    "tb_frame",
    "gi_frame",
    "gi_code",
    "cr_frame",
    "cr_code",
    "ag_frame",
    "ag_code",
    "co_code",
    "modules",
    "meta_path",
    "path_hooks",
    "path_importer_cache",
    "settrace",
    "setprofile",
    "attrgetter",
    "methodcaller",
    # attribute traversal driven by a string ("0.__init__.__globals__")
    "Formatter",
    "get_field",
    "breakpointhook",
}
#: Writing files. A pack declared filesystem ``none``, and a file it wrote could be imported as code
#: the scan never saw -- so the ways to create or change files are refused. These names are
#: unambiguous wherever they appear; ``open`` also as the builtin name.
_FILESYSTEM_ATTRIBUTES = {
    "open",
    "fdopen",
    "open_code",
    "FileIO",
    "unlink",
    "rmdir",
    "removedirs",
    "makedirs",
    "renames",
    "chmod",
    "lchmod",
    "fchmod",
    "chown",
    "lchown",
    "fchown",
    "symlink",
    "ftruncate",
    "utime",
    "mkfifo",
    "mknod",
    "chdir",
    "fchdir",
    "chroot",
    "putenv",
    "unsetenv",
    "dup2",
    "FileHandler",
    "RotatingFileHandler",
    "TimedRotatingFileHandler",
    "WatchedFileHandler",
    "tofile",
}
#: File-changing names that are ALSO ordinary methods elsewhere (list.remove, str.replace, ...):
#: refused only on a name bound to the os module.
_OS_ONLY_FILESYSTEM_ATTRIBUTES = {
    "remove",
    "rename",
    "replace",
    "mkdir",
    "link",
    "truncate",
    "dup",
    "write",
}
#: Re-exported module objects reached as attributes (``os.path.os``, ``random._os``): a hop to a
#: module whose own rules this scan would no longer see. Refused in any attribute position.
_MODULE_HOP_ATTRIBUTES = {
    "os",
    "_os",
    "sys",
    "_sys",
    "io",
    "_io",
    "builtins",
    "_builtins",
    "posix",
    "nt",
}
#: The standard-library modules a pack may import -- an ALLOW-list, because a deny-list of the
#: stdlib's code runners (timeit, doctest, cProfile, logging.config, pydoc.locate, sqlite3
#: extensions, distutils.spawn, ...) can never be shown complete. Pure computation, text, data
#: formats, dates and math; ``os``/``sys`` only for their ordinary attributes (the attribute rules
#: above refuse process creation, frames and the module table). A submodule is admitted only when it
#: is named here. Anything else in the stdlib is refused as ``dependency.stdlib_not_admitted``.
_ADMITTED_STDLIB = {
    "__future__",
    "abc",
    "array",
    "base64",
    "binascii",
    "bisect",
    "calendar",
    "cmath",
    "collections",
    "collections.abc",
    "contextlib",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "difflib",
    "enum",
    "errno",
    "fnmatch",
    "fractions",
    "functools",
    "graphlib",
    "hashlib",
    "heapq",
    "hmac",
    "html",
    "io",
    "ipaddress",
    "itertools",
    "json",
    "keyword",
    "math",
    "numbers",
    "operator",
    "os",
    "os.path",
    "pprint",
    "random",
    "re",
    "reprlib",
    "secrets",
    "shlex",
    "statistics",
    "string",
    "struct",
    "sys",
    "textwrap",
    "time",
    "types",
    "typing",
    "unicodedata",
    "urllib",
    "urllib.parse",
    "warnings",
    "zlib",
    "zoneinfo",
}
#: Never reachable through getattr & co.: the loaders and the code runners are only allowed as a
#: direct call with a constant argument, which the import scan then resolves.
_NEVER_BY_REFLECTION = {"import_module", "__import__", "exec", "eval", "compile"}

_LOCK_KEYS_ALLOWED = (
    set(lock_integrity.CANONICAL_FIELDS_V3)
    | set(lock_integrity.MUTABLE_FIELDS)
    | {"_integrity", "build_mode", "effective_host_trust_policy_at_install", "pinnable"}
)
#: Lock fields that describe something this slice does not run. Present and non-empty -> refuse.
_LOCK_MUST_BE_EMPTY = (
    "mcp_command",
    "mcp_env_keys",
    "remote_endpoint",
    "connector",
    "agent",
    "assets",
    "mcp_preinstalled",
    "mcp_preinstall",
    "mcp_sandbox_volume",
    "mcp_preinstall_command",
    "mcp_allowed_domains",
    "env_requirements",
    "python_distribution",
    "python_distribution_version",
    "prompts",
    "resources",
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_HASH = re.compile(r"^sha256:([0-9a-f]{64})$")


class PackRefused(Exception):
    """The pack is not imported. ``code`` is stable; ``violations`` lists every policy breach found."""

    def __init__(
        self, code: str, detail: str, violations: list[dict[str, str]] | None = None
    ):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.violations = violations or [{"code": code, "detail": detail}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "refused",
            "code": self.code,
            "detail": self.detail,
            "violations": self.violations,
        }


@dataclass(frozen=True)
class ImportedPack:
    manifest: dict[str, Any]
    tar_bytes: bytes
    tar_sha256: str
    tree_digest: str
    shim_bytes: bytes
    tool_table: list[dict[str, Any]]
    tree_provenance: dict[str, Any]
    registry_provenance: dict[str, Any]


@dataclass
class _Violations:
    items: list[dict[str, str]] = field(default_factory=list)

    def add(self, code: str, detail: str) -> None:
        self.items.append({"code": code, "detail": detail})

    def raise_if_any(self) -> None:
        if self.items:
            first = self.items[0]
            raise PackRefused(first["code"], first["detail"], list(self.items))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


# =========================================================================== 1. lock binding
def _check_lock(
    lock_entry: Any, *, package_key: str, package_id: str, version: str
) -> str:
    """Return the expected artifact sha256 hex, or refuse."""
    if package_key != package_id:
        raise PackRefused(
            "lock.wrong_key",
            f"lock entry key {package_key!r} is not the requested package {package_id!r}",
        )
    if not isinstance(lock_entry, dict):
        raise PackRefused("lock.missing_entry", f"no lock entry for {package_id!r}")
    integrity = lock_entry.get("_integrity")
    if integrity is None:
        raise PackRefused(
            "lock.integrity_missing", "the lock entry carries no _integrity"
        )
    if (
        not isinstance(integrity, dict)
        or integrity.get("algorithm") != "sha256"
        or integrity.get("canonical_version") not in (1, 2, 3)
        or not isinstance(integrity.get("hash"), str)
        or not _HEX64.match(integrity["hash"])
    ):
        raise PackRefused(
            "lock.integrity_malformed",
            "lock _integrity must be {algorithm: sha256, canonical_version: 1|2|3, "
            "hash: 64 lowercase hex}",
        )
    if lock_integrity.verify_entry(package_id, lock_entry).status != "verified":
        raise PackRefused(
            "lock.integrity_mismatch",
            "the lock entry does not match its own _integrity hash (it was changed)",
        )
    unexpected = sorted(set(lock_entry) - _LOCK_KEYS_ALLOWED)
    if unexpected:
        raise PackRefused(
            "lock.unexpected_field", f"lock entry carries unknown fields {unexpected}"
        )
    if lock_entry.get("version") != version:
        raise PackRefused(
            "lock.version_mismatch",
            f"lock version {lock_entry.get('version')!r} is not the requested {version!r}",
        )
    ah = lock_entry.get("artifact_hash")
    if not ah:
        raise PackRefused(
            "lock.artifact_hash_missing", "the lock entry carries no artifact hash"
        )
    m = _ARTIFACT_HASH.match(ah) if isinstance(ah, str) else None
    if m is None:
        raise PackRefused(
            "lock.artifact_hash_malformed", "lock artifact_hash must be sha256:<64 hex>"
        )
    return m.group(1)


# =========================================================================== 2. artifact
def _extract(artifact: bytes, dest: Path, package_id: str) -> Path:
    if len(artifact) > MAX_ARTIFACT_BYTES:
        raise PackRefused(
            "artifact.too_large",
            f"artifact is {len(artifact)} bytes (max {MAX_ARTIFACT_BYTES})",
        )
    try:
        tf = tarfile.open(fileobj=io.BytesIO(artifact), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise PackRefused(
            "artifact.not_gzip_tar", f"artifact is not a gzip tar: {exc}"
        ) from exc
    members: list[tarfile.TarInfo] = []
    seen: set[str] = set()
    total = 0
    with tf:
        while True:
            try:
                member = tf.next()
            except (tarfile.TarError, OSError, EOFError) as exc:
                raise PackRefused(
                    "artifact.not_gzip_tar", f"artifact is not a readable tar: {exc}"
                ) from exc
            if member is None:
                break
            if len(members) >= MAX_MEMBERS:
                raise PackRefused(
                    "artifact.too_many_members",
                    f"artifact has more than {MAX_MEMBERS} members",
                )
            name = member.name
            if not (member.isreg() or member.isdir()):
                raise PackRefused(
                    "artifact.link_or_special",
                    f"only regular files and directories are allowed: {name!r}",
                )
            parts = name.rstrip("/").split("/")
            if (
                name.startswith("/")
                or "\\" in name
                or ":" in name
                or any(p in ("", ".", "..") for p in parts)
            ):
                raise PackRefused(
                    "artifact.unsafe_path", f"unsafe member path {name!r}"
                )
            if parts[0] != package_id:
                raise PackRefused(
                    "artifact.wrong_root",
                    f"every member must live under {package_id + '/'!r}, got {name!r}",
                )
            key = "/".join(parts).lower()
            if key in seen:
                raise PackRefused(
                    "artifact.duplicate_member", f"duplicate member {name!r}"
                )
            seen.add(key)
            if member.isreg():
                if member.size > MAX_MEMBER_BYTES:
                    raise PackRefused(
                        "artifact.member_too_large",
                        f"{name!r} is {member.size} bytes (max {MAX_MEMBER_BYTES})",
                    )
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise PackRefused(
                        "artifact.total_too_large",
                        f"uncompressed content exceeds {MAX_TOTAL_BYTES} bytes",
                    )
            members.append(member)
        for member in members:
            parts = member.name.rstrip("/").split("/")
            target = dest.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = tf.extractfile(member)
            data = handle.read() if handle is not None else b""
            if len(data) != member.size:
                raise PackRefused(
                    "artifact.not_gzip_tar", f"short read for {member.name!r}"
                )
            target.write_bytes(data)
    root = dest / package_id
    if not root.is_dir() or sorted(p.name for p in dest.iterdir()) != [package_id]:
        raise PackRefused(
            "artifact.wrong_root", f"artifact must have exactly one root {package_id!r}"
        )
    return root


# =========================================================================== 3. manifest
def _load_manifest(root: Path) -> tuple[dict[str, Any], bytes]:
    path = root / "agentnode.yaml"
    if not path.is_file():
        raise PackRefused("manifest.missing", "the artifact carries no agentnode.yaml")
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise PackRefused(
            "manifest.unparseable", f"agentnode.yaml does not parse: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise PackRefused("manifest.unparseable", "agentnode.yaml is not a mapping")
    return data, raw


def _normalize(man: dict[str, Any]) -> dict[str, Any]:
    """The backend's 0.2 defaulting (validator.py:77); 0.1 passes through unchanged."""
    if man.get("manifest_version") != "0.2":
        return man
    out = dict(man)
    for key, default in (
        ("runtime", "python"),
        ("install_mode", "package"),
        ("hosting_type", "agentnode_hosted"),
    ):
        out.setdefault(key, default)
    perms = out.get("permissions")
    if perms is None:
        out["permissions"] = json.loads(json.dumps(_V02_PERMISSION_DEFAULTS))
    elif isinstance(perms, dict):
        out["permissions"] = {
            **json.loads(json.dumps(_V02_PERMISSION_DEFAULTS)),
            **perms,
        }
    return out


def _schema_ok(schema: Any, path: str, v: _Violations) -> None:
    if not isinstance(schema, dict):
        v.add("manifest.invalid", f"{path} must be a JSON Schema object")
        return
    t = schema.get("type")
    if t is not None:
        types = [t] if isinstance(t, str) else t if isinstance(t, list) else None
        if types is None or any(x not in _JSON_SCHEMA_TYPES for x in types):
            v.add("manifest.invalid", f"{path}.type is not a valid JSON Schema type")
    props = schema.get("properties")
    if props is not None and (
        not isinstance(props, dict)
        or any(not isinstance(p, dict) for p in props.values())
    ):
        v.add("manifest.invalid", f"{path}.properties must map names to schema objects")
    req = schema.get("required")
    if req is not None and (
        not isinstance(req, list)
        or (isinstance(props, dict) and any(r not in props for r in req))
    ):
        v.add("manifest.invalid", f"{path}.required must list declared properties")


def _validate_manifest(man: dict[str, Any], *, package_id: str, version: str) -> None:
    mv = man.get("manifest_version")
    if mv not in ("0.1", "0.2"):
        raise PackRefused(
            "manifest.unsupported_version",
            f"manifest_version {mv!r} is not importable (0.1 and 0.2 only; 0.3 is "
            f"refused because the backend validates its entrypoints with 0.1 rules)",
        )
    v = _Violations()
    if not isinstance(man.get("package_id"), str) or not _SLUG.match(man["package_id"]):
        v.add("manifest.invalid", "package_id must match [a-z0-9-], 3-60 chars")
    if not isinstance(man.get("version"), str) or not _SEMVER_STRICT.match(
        man["version"]
    ):
        v.add(
            "manifest.invalid",
            "version must be MAJOR.MINOR.PATCH (a capability version is immutable SemVer)",
        )
    name = man.get("name")
    if not isinstance(name, str) or not 3 <= len(name) <= 100:
        v.add("manifest.invalid", "name must be 3-100 chars")
    if not isinstance(man.get("publisher"), str) or not man["publisher"]:
        v.add("manifest.invalid", "publisher is required")
    summary = man.get("summary")
    if not isinstance(summary, str) or not 20 <= len(summary) <= 200:
        v.add("manifest.invalid", "summary must be 20-200 chars")
    v.raise_if_any()
    if man["package_id"] != package_id or man["version"] != version:
        raise PackRefused(
            "manifest.identity_mismatch",
            f"manifest names {man['package_id']}@{man['version']}, "
            f"requested {package_id}@{version}",
        )
    kind = (man.get("package_type"), man.get("runtime"), man.get("install_mode"))
    if (
        kind != ("toolpack", "python", "package")
        or man.get("hosting_type") != "agentnode_hosted"
    ):
        raise PackRefused(
            "manifest.unsupported_kind",
            f"only toolpack/python/package (agentnode_hosted) is importable, got {kind}",
        )


def _tool_table(man: dict[str, Any]) -> list[dict[str, Any]]:
    """Manifest order; installer semantics for entrypoints (installer._resolve_entrypoint)."""
    caps = man.get("capabilities") or {}
    if not isinstance(caps, dict):
        raise PackRefused("manifest.invalid", "capabilities must be a mapping")
    if caps.get("prompts") or caps.get("resources"):
        raise PackRefused(
            "manifest.unsupported_feature",
            "this slice imports tools only; the pack declares prompts or resources",
        )
    tools = caps.get("tools")
    if not isinstance(tools, list) or not tools:
        raise PackRefused(
            "manifest.invalid", "capabilities.tools must have at least 1 tool"
        )
    pkg_ep = man.get("entrypoint") or ""
    v02 = man.get("manifest_version") == "0.2"
    v = _Violations()
    if pkg_ep and not _ENTRYPOINT_V1.match(str(pkg_ep)):
        v.add(
            "entrypoint.invalid",
            f"entrypoint must be a python module path, got {pkg_ep!r}",
        )
    if not v02 and not pkg_ep:
        v.add("entrypoint.invalid", "manifest 0.1 requires a package-level entrypoint")
    if (
        v02
        and len(tools) > 1
        and any(not (isinstance(t, dict) and t.get("entrypoint")) for t in tools)
    ):
        v.add(
            "entrypoint.invalid",
            "manifest 0.2 with several tools requires a per-tool entrypoint",
        )
    table, names = [], set()
    for i, t in enumerate(tools):
        if not isinstance(t, dict):
            v.add("tools.invalid", f"tools[{i}] must be a mapping")
            continue
        tname = t.get("name")
        if not isinstance(tname, str) or not M._TOOL.match(tname):
            v.add(
                "tools.invalid", f"tools[{i}].name is not a valid tool name: {tname!r}"
            )
            continue
        if tname in names:
            v.add("tools.duplicate", f"duplicate tool name {tname!r}")
            continue
        names.add(tname)
        if not t.get("capability_id"):
            v.add("tools.invalid", f"tools[{i}].capability_id is required")
        ep = t.get("entrypoint") or ""
        if ep and (not v02 or not _ENTRYPOINT_V2.match(str(ep))):
            v.add(
                "entrypoint.invalid",
                f"tools[{i}].entrypoint must be module.path:function (0.2 only), got {ep!r}",
            )
            continue
        if not ep and not pkg_ep:
            v.add(
                "entrypoint.invalid",
                f"tools[{i}] has no entrypoint and the pack declares none",
            )
            continue
        module, _, function = (ep or f"{pkg_ep}:run").partition(":")
        for key in ("input_schema", "output_schema"):
            if t.get(key) is not None:
                _schema_ok(t[key], f"tools[{i}].{key}", v)
        table.append(
            {
                "name": tname,
                "description": str(t.get("description") or ""),
                "input_schema": t.get("input_schema") or {"type": "object"},
                "output_schema": t.get("output_schema") or {},
                "module": module,
                "function": function,
            }
        )
    v.raise_if_any()
    return table


# =========================================================================== 4. permissions
def _source_permissions(man: dict[str, Any], v: _Violations) -> dict[str, Any]:
    """The pack's declared permissions, flattened as the lock records them. Policy breaches go to
    *v* (so a refusal names all of them); a malformed permissions block refuses at once."""
    perms = man.get("permissions")
    if not isinstance(perms, dict):
        raise PackRefused("manifest.invalid", "permissions section is required")
    invalid = _Violations()

    def level(section: str, key: str = "level") -> Any:
        sec = perms.get(section)
        return sec.get(key) if isinstance(sec, dict) else None

    net = perms.get("network") if isinstance(perms.get("network"), dict) else {}
    domains = net.get("allowed_domains") or []
    checks = (
        ("network", level("network"), _NETWORK_LEVELS),
        ("filesystem", level("filesystem"), _FS_LEVELS),
        ("code_execution", level("code_execution"), _EXEC_LEVELS),
        ("data_access", level("data_access"), _DATA_LEVELS),
        ("user_approval", level("user_approval", "required"), _APPROVAL_LEVELS),
    )
    for section, value, allowed in checks:
        if value not in allowed:
            invalid.add(
                "manifest.invalid",
                f"permissions.{section} level {value!r} is not one of {sorted(allowed)}",
            )
    if not isinstance(domains, list):
        invalid.add(
            "manifest.invalid", "permissions.network.allowed_domains must be a list"
        )
    invalid.raise_if_any()
    if level("network") != "none" or domains:
        v.add(
            "permission.network",
            f"the pack declares network {level('network')!r} "
            f"(domains {list(domains)}); this slice runs network-free packs only",
        )
    if level("filesystem") != "none":
        v.add(
            "permission.filesystem",
            f"the pack declares filesystem {level('filesystem')!r}; "
            f"this slice grants no filesystem access",
        )
    if level("code_execution") != "none":
        v.add(
            "permission.code_execution",
            f"the pack declares code_execution {level('code_execution')!r}; "
            f"child processes are not granted",
        )
    if level("data_access") != "input_only":
        v.add(
            "permission.data_access",
            f"the pack declares data_access {level('data_access')!r}; "
            f"only input_only is importable",
        )
    if perms.get("external_integrations"):
        v.add(
            "permission.external_integrations",
            "the pack declares external integrations",
        )
    if man.get("env_requirements"):
        v.add(
            "permission.secrets",
            "the pack declares env_requirements (secrets); no secret is brokered here",
        )
    if man.get("connector"):
        v.add(
            "permission.connector",
            "the pack declares a connector (credentials); not importable here",
        )
    if man.get("mcp_server"):
        v.add(
            "permission.mcp_server",
            "the pack declares an mcp_server block; not a python toolpack run",
        )
    for unsupported in ("agent", "assets"):
        if man.get(unsupported):
            v.add("manifest.unsupported_feature", f"the pack declares {unsupported!r}")
    return {
        "network_level": level("network"),
        "filesystem_level": level("filesystem"),
        "code_execution_level": level("code_execution"),
        "data_access_level": level("data_access"),
        "user_approval_level": level("user_approval", "required"),
        "allowed_domains": list(domains),
    }


# =========================================================================== 5. lock <-> manifest
def _check_lock_matches_manifest(
    lock: dict[str, Any], man: dict[str, Any], flat_perms: dict[str, Any]
) -> None:
    def mismatch(fieldname: str, lock_value: Any, manifest_value: Any) -> PackRefused:
        return PackRefused(
            "lock.manifest_mismatch",
            f"lock {fieldname} {lock_value!r} does not match the manifest's {manifest_value!r}",
        )

    for fieldname, expected in (
        ("package_type", man.get("package_type")),
        ("runtime", man.get("runtime")),
        ("entrypoint", man.get("entrypoint") or ""),
    ):
        if lock.get(fieldname) != expected:
            raise mismatch(fieldname, lock.get(fieldname), expected)
    tools = (man.get("capabilities") or {}).get("tools") or []
    expected_tools = sorted(
        (
            {"name": t["name"], "entrypoint": t["entrypoint"]}
            for t in tools
            if isinstance(t, dict) and t.get("entrypoint")
        ),
        key=lambda d: d["name"],
    )
    lock_tools = lock.get("tools") or []
    if (
        not isinstance(lock_tools, list)
        or sorted(
            lock_tools, key=lambda d: str(d.get("name")) if isinstance(d, dict) else ""
        )
        != expected_tools
    ):
        raise mismatch("tools", lock_tools, expected_tools)
    if lock.get("permissions") != flat_perms:
        raise PackRefused(
            "lock.manifest_mismatch",
            f"lock permissions {lock.get('permissions')!r} do not equal the manifest's "
            f"{flat_perms!r} (a lock may not widen or differ from what the pack declares)",
        )
    pub = lock.get("publisher_slug")
    if pub and pub != man.get("publisher"):
        raise mismatch("publisher_slug", pub, man.get("publisher"))
    for fieldname in _LOCK_MUST_BE_EMPTY:
        if lock.get(fieldname):
            raise PackRefused(
                "lock.unreconcilable_field",
                f"lock records {fieldname!r}, which a network-free python toolpack cannot carry",
            )


# =========================================================================== 6. dependencies + layout
def _load_pyproject(root: Path) -> tuple[dict[str, Any], bytes]:
    raw = (root / "pyproject.toml").read_bytes()
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # python 3.10
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError as exc:
            raise PackRefused(
                "dependency.pyproject_unavailable",
                "pyproject.toml cannot be checked on this interpreter (needs tomllib or tomli)",
            ) from exc
    try:
        return tomllib.loads(raw.decode("utf-8")), raw
    except (ValueError, UnicodeDecodeError) as exc:
        raise PackRefused(
            "dependency.pyproject_unparseable", f"pyproject.toml does not parse: {exc}"
        ) from exc


def _check_pyproject(py: dict[str, Any], v: _Violations) -> None:
    project = py.get("project") if isinstance(py.get("project"), dict) else {}
    deps = project.get("dependencies", [])
    if deps:
        v.add(
            "dependency.declared",
            f"pyproject declares runtime dependencies {deps}; this slice "
            f"imports dependency-free packs only",
        )
    if project.get("optional-dependencies"):
        v.add("dependency.optional", "pyproject declares optional-dependencies")
    if py.get("dependency-groups"):
        v.add("dependency.optional", "pyproject declares dependency-groups")
    if "dependencies" in (project.get("dynamic") or []):
        v.add("dependency.declared", "pyproject declares its dependencies as dynamic")


def _layout(root: Path, top: str, v: _Violations) -> list[Path]:
    """Check the allow-listed layout; return the runtime package's files (sorted)."""
    runtime_dir = root / "src" / top
    runtime: list[Path] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if path.is_file() and path.suffix.lower() in _COMPILED_OR_NATIVE:
            v.add("layout.compiled_or_native", f"compiled or native file {rel!r}")
            continue
        if "__pycache__" in parts:
            v.add("layout.compiled_or_native", f"bytecode cache {rel!r}")
            continue
        if parts[0] == "tests" or rel == "src":
            continue
        if parts[0] == "src":
            if parts[1] != top:
                v.add(
                    "layout.unexpected_file",
                    f"{rel!r} is outside the one runtime package src/{top}/",
                )
            elif path.is_file():
                if path.suffix != ".py":
                    v.add(
                        "layout.runtime_non_python",
                        f"runtime package file {rel!r} is not .py "
                        f"(bundled data is not importable in this slice)",
                    )
                else:
                    runtime.append(path)
            continue
        if (
            len(parts) == 1
            and path.is_file()
            and (rel in _ROOT_FILES_REQUIRED or rel in _ROOT_FILES_OPTIONAL)
        ):
            continue
        v.add(
            "layout.unexpected_file",
            f"{rel!r} is not part of the importable pack layout",
        )
    for required in _ROOT_FILES_REQUIRED:
        if not (root / required).is_file():
            v.add("layout.missing_file", f"{required} is missing")
    if not (runtime_dir / "__init__.py").is_file():
        v.add("layout.missing_runtime_package", f"src/{top}/__init__.py is missing")
    return runtime


def _module_name(path: Path, src: Path) -> tuple[str, bool]:
    rel = path.relative_to(src).with_suffix("").as_posix().split("/")
    if rel[-1] == "__init__":
        return ".".join(rel[:-1]), True
    return ".".join(rel), False


def _matches(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def _check_import_name(
    name: str, *, top: str, own: set[str], where: str, v: _Violations
) -> None:
    head = name.split(".")[0]
    if head == top:
        if name not in own and name.rpartition(".")[0] not in own:
            v.add(
                "dependency.unresolved",
                f"{where}: import {name!r} does not resolve inside the pack",
            )
        return
    if head not in SANDBOX_STDLIB:
        v.add(
            "dependency.undeclared",
            f"{where}: import {name!r} is neither the sandbox Python's "
            f"standard library nor the pack itself",
        )
        return
    if _matches(name, _NETWORK_MODULES):
        v.add(
            "permission.network",
            f"{where}: imports network module {name!r} although the pack "
            f"declared network none",
        )
    elif _matches(name, _PROCESS_MODULES):
        v.add(
            "permission.code_execution",
            f"{where}: imports process module {name!r} although the "
            f"pack declared code_execution none",
        )
    elif _matches(name, _DYNAMIC_IMPORT_MODULES):
        v.add("dependency.dynamic_import", f"{where}: imports dynamic loader {name!r}")
    elif _matches(name, _CODE_EXEC_MODULES):
        v.add(
            "permission.code_execution",
            f"{where}: imports {name!r}, which turns data into running code",
        )
    elif name not in _ADMITTED_STDLIB:
        v.add(
            "dependency.stdlib_not_admitted",
            f"{where}: standard-library module {name!r} is not on the admitted list for "
            f"dependency-free, network-free packs",
        )


def _attribute_violation(attr: str) -> str | None:
    """The refusal code for touching attribute *attr* anywhere, or None when it is ordinary."""
    if attr in _CODE_EXEC_ATTRIBUTES:
        return "permission.code_execution"
    if attr in _REFLECTIVE_ATTRIBUTES:
        return "dependency.dynamic_import"
    if attr.startswith("__") and attr.endswith("__") and attr not in _SAFE_DUNDERS:
        return "dependency.dynamic_import"
    if attr in _FILESYSTEM_ATTRIBUTES:
        return "permission.filesystem"
    if attr in _MODULE_HOP_ATTRIBUTES:
        return "dependency.dynamic_import"
    return None


def _os_names(tree: ast.Module) -> set[str]:
    """Every name this module binds to the os module (``import os``, ``import os as o``)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or (
                    alias.name.startswith("os.") and not alias.asname
                ):
                    names.add(alias.asname or "os")
    return names


def _reflection(
    node: ast.AST,
    rel: str,
    v: _Violations,
    call_targets: set[int],
    os_names: set[str] = frozenset(),
) -> None:
    """Close the routes around the syntactic scan (see ``_REFLECTIVE_NAMES``)."""
    line = getattr(node, "lineno", 0)
    if isinstance(node, ast.Name) and node.id == "open":
        v.add(
            "permission.filesystem",
            f"{rel}:{line}: uses open() although the pack declared filesystem none",
        )
    elif (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in os_names
        and node.attr in _OS_ONLY_FILESYSTEM_ATTRIBUTES
    ):
        v.add(
            "permission.filesystem",
            f"{rel}:{line}: os.{node.attr} changes files although the pack declared filesystem none",
        )
    elif isinstance(node, ast.Name) and node.id in _REFLECTIVE_NAMES:
        v.add(
            "dependency.dynamic_import",
            f"{rel}:{line}: uses reflective builtin {node.id!r}",
        )
    elif (
        isinstance(node, ast.Name)
        and node.id in _REFLECTIVE_CALLS
        and id(node) not in call_targets
    ):
        # getattr passed on or aliased (f = getattr; map(getattr, ...)) escapes the constant-name rule
        v.add(
            "dependency.dynamic_import",
            f"{rel}:{line}: passes {node.id!r} on instead of calling it with a constant name",
        )
    elif isinstance(node, ast.Attribute):
        code = _attribute_violation(node.attr)
        if code is not None:
            v.add(code, f"{rel}:{line}: touches attribute {node.attr!r}")
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _REFLECTIVE_CALLS
    ):
        name = node.args[1] if len(node.args) > 1 else None
        if (
            not (isinstance(name, ast.Constant) and isinstance(name.value, str))
            or node.keywords
        ):
            v.add(
                "dependency.dynamic_import",
                f"{rel}:{line}: {node.func.id}() with a computed attribute name",
            )
        else:
            code = _attribute_violation(name.value)
            if name.value in _NEVER_BY_REFLECTION:
                code = "dependency.dynamic_import"
            if code is not None:
                v.add(code, f"{rel}:{line}: {node.func.id}() reaches {name.value!r}")


def _scan_module(
    path: Path, src: Path, top: str, own: set[str], v: _Violations
) -> ast.Module | None:
    rel = path.relative_to(src.parent).as_posix()
    try:
        tree = ast.parse(path.read_bytes().decode("utf-8"), filename=rel)
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        v.add("layout.syntax_error", f"{rel} does not parse: {exc}")
        return None
    modname, is_pkg = _module_name(path, src)
    package = modname if is_pkg else modname.rpartition(".")[0]
    call_targets = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    os_names = _os_names(tree)
    for node in ast.walk(tree):
        _reflection(node, rel, v, call_targets, os_names)
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_import_name(alias.name, top=top, own=own, where=rel, v=v)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".") if package else []
                if node.level - 1 > len(base_parts) - 1:
                    v.add(
                        "dependency.unresolved",
                        f"{rel}: relative import escapes the package",
                    )
                    continue
                base = ".".join(base_parts[: len(base_parts) - (node.level - 1)])
                target = f"{base}.{node.module}" if node.module else base
                if target not in own:
                    v.add(
                        "dependency.unresolved",
                        f"{rel}: relative import {target!r} does not resolve",
                    )
            else:
                _check_import_name(node.module or "", top=top, own=own, where=rel, v=v)
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if _matches(
                        full,
                        _NETWORK_MODULES
                        + _PROCESS_MODULES
                        + _DYNAMIC_IMPORT_MODULES
                        + _CODE_EXEC_MODULES,
                    ):
                        _check_import_name(full, top=top, own=own, where=rel, v=v)
                    elif _attribute_violation(alias.name) is not None:
                        # ``from os import system`` / ``from sys import modules``
                        v.add(
                            _attribute_violation(alias.name),
                            f"{rel}:{node.lineno}: imports {full!r}",
                        )
                    elif alias.name in _REFLECTIVE_NAMES:
                        # ``from operator import attrgetter``
                        v.add(
                            "dependency.dynamic_import",
                            f"{rel}:{node.lineno}: imports reflective {full!r}",
                        )
                    elif (
                        node.module == "os"
                        and alias.name in _OS_ONLY_FILESYSTEM_ATTRIBUTES
                    ):
                        # ``from os import remove``
                        v.add(
                            "permission.filesystem",
                            f"{rel}:{node.lineno}: imports {full!r}, which changes files",
                        )
        elif isinstance(node, ast.Call):
            func = node.func
            fname = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if fname in ("__import__", "import_module"):
                arg = node.args[0] if node.args else None
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and not node.args[1:]
                    and not node.keywords
                ):
                    _check_import_name(arg.value, top=top, own=own, where=rel, v=v)
                else:
                    v.add(
                        "dependency.dynamic_import",
                        f"{rel}:{node.lineno}: {fname}() with a computed name",
                    )
            elif isinstance(func, ast.Name) and fname in _DYNAMIC_CODE_CALLS:
                v.add(
                    "permission.code_execution",
                    f"{rel}:{node.lineno}: {fname}() runs computed code",
                )
        elif (
            isinstance(node, (ast.Name, ast.Attribute))
            and id(node) not in call_targets
            and isinstance(node.ctx, ast.Load)
            and (
                getattr(node, "id", None)
                in ("__import__", "import_module", *_DYNAMIC_CODE_CALLS)
                or getattr(node, "attr", None) in ("__import__", "import_module")
            )
        ):
            # an alias of a loader or of exec/eval (f = __import__; g = eval) hides what is called later
            v.add(
                "dependency.dynamic_import",
                f"{rel}:{node.lineno}: references a dynamic loader by alias",
            )
    return tree


def _check_code(
    root: Path,
    top: str,
    runtime: list[Path],
    table: list[dict[str, Any]],
    v: _Violations,
) -> None:
    src = root / "src"
    own = {_module_name(p, src)[0] for p in runtime}
    trees: dict[str, ast.Module] = {}
    for path in runtime:
        tree = _scan_module(path, src, top, own, v)
        if tree is not None:
            trees[_module_name(path, src)[0]] = tree
    for t in table:
        if t["module"].split(".")[0] != top:
            v.add(
                "entrypoint.unresolved",
                f"tool {t['name']!r} entrypoint {t['module']!r} is outside src/{top}/",
            )
            continue
        tree = trees.get(t["module"])
        if tree is None:
            v.add(
                "entrypoint.unresolved",
                f"tool {t['name']!r}: module {t['module']!r} is not shipped",
            )
            continue
        defs = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == t["function"]
        ]
        if len(defs) != 1:
            v.add(
                "entrypoint.unresolved",
                f"tool {t['name']!r}: {t['module']} has no single top-level def {t['function']!r}",
            )


# =========================================================================== 7. tree + capability
def _write_tree(
    tree_dir: Path,
    root: Path,
    top: str,
    runtime: list[Path],
    shim: bytes,
    provenance: dict[str, Any],
) -> None:
    for path in runtime:
        target = tree_dir / path.relative_to(root / "src")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    (tree_dir / SHIM_ENTRY).write_bytes(shim)
    (tree_dir / TREE_PROVENANCE_FILE).write_bytes(_canonical(provenance) + b"\n")


def import_pack(
    artifact: bytes,
    lock_entry: Any,
    *,
    package_key: str,
    package_id: str,
    version: str,
    namespace: str,
    private_key: Ed25519PrivateKey,
    public_key_bytes: bytes,
) -> ImportedPack:
    """Turn one registry pack into one signed capability, or raise ``PackRefused``.

    *namespace* is the IMPORTING publisher's namespace (the holder of *private_key*); the
    capability id is ``<namespace>/<package_id>``. The pack's own ``publisher`` is carried as
    claimed upstream provenance only and grants nothing.
    """
    try:
        capability_id = M.capability_id(namespace, package_id)
    except ValueError as exc:
        raise PackRefused("import.bad_identity", str(exc)) from exc
    expected_hex = _check_lock(
        lock_entry, package_key=package_key, package_id=package_id, version=version
    )
    if _sha256(artifact) != expected_hex:
        raise PackRefused(
            "artifact.hash_mismatch",
            "the artifact bytes do not hash to the lock's artifact_hash (tampered or wrong artifact)",
        )

    with tempfile.TemporaryDirectory(prefix="agentnode-pack-") as tmp:
        root = _extract(artifact, Path(tmp) / "x", package_id)
        raw_manifest, manifest_bytes = _load_manifest(root)
        man = _normalize(raw_manifest)
        _validate_manifest(man, package_id=package_id, version=version)
        table = _tool_table(man)
        tops = sorted({t["module"].split(".")[0] for t in table})
        if len(tops) != 1:
            raise PackRefused(
                "layout.multiple_runtime_packages",
                f"tools span several packages {tops}",
            )
        top = tops[0]
        # every policy breach is collected first, so a refusal names all of them at once
        v = _Violations()
        flat_perms = _source_permissions(man, v)
        runtime = _layout(root, top, v)
        pyproject_bytes = b""  # a missing pyproject.toml is already a layout violation
        if (root / "pyproject.toml").is_file():
            pyproject, pyproject_bytes = _load_pyproject(root)
            _check_pyproject(pyproject, v)
        _check_code(root, top, runtime, table, v)
        v.raise_if_any()
        _check_lock_matches_manifest(lock_entry, man, flat_perms)

        shim = _pack_shim.render(table)
        if _pack_shim.embedded_table(shim) != json.loads(
            _pack_shim.canonical_table_json(table)
        ):
            raise PackRefused(
                "shim.table_mismatch",
                "the rendered shim does not embed the manifest's tool table",
            )
        source_digest = tree_pack.tree_digest_of_dir(root)
        tree_prov = {
            "schema": "agentnode.pack-capability-tree.v1",
            "importer": IMPORTER_VERSION,
            "shim": {
                "version": _pack_shim.PACK_SHIM_VERSION,
                "template_sha256": _pack_shim.template_sha256(),
            },
            "package_id": package_id,
            "version": version,
            "claimed_publisher": man.get("publisher"),
            "manifest_sha256": _sha256(manifest_bytes),
            "manifest_canonical_sha256": _sha256(_canonical(raw_manifest)),
            "pyproject_sha256": _sha256(pyproject_bytes),
            "source_content_digest": source_digest,
            "runtime_package": f"src/{top}",
            "tools": [t["name"] for t in table],
        }
        tree_dir = Path(tmp) / "tree"
        tree_dir.mkdir()
        _write_tree(tree_dir, root, top, runtime, shim, tree_prov)
        if (tree_dir / SHIM_ENTRY).read_bytes() != shim:
            raise PackRefused(
                "shim.table_mismatch",
                "the shim written to the tree differs from the rendered shim",
            )
        tar_bytes = tree_pack.pack_tree(tree_dir)
        tree_digest = tree_pack.tree_digest_of_dir(tree_dir)

    integrity = lock_entry["_integrity"]
    registry = {
        "kind": "agentnode-registry-pack",
        "package_key": package_key,
        "package_id": package_id,
        "version": version,
        "claimed_upstream_publisher": man.get("publisher"),
        "upstream_signature_status": "present-unverified"
        if lock_entry.get("_signatures")
        else "absent",
        "upstream_authenticity": "not established by this import",
        "artifact_sha256": expected_hex,
        "artifact_bytes": len(artifact),
        "source_content_digest": source_digest,
        "manifest_sha256": tree_prov["manifest_sha256"],
        "manifest_canonical_sha256": tree_prov["manifest_canonical_sha256"],
        "pyproject_sha256": tree_prov["pyproject_sha256"],
        "lock_integrity": {
            "algorithm": integrity["algorithm"],
            "canonical_version": integrity["canonical_version"],
            "hash": integrity["hash"],
        },
        "source_permissions": flat_perms,
        "importer_version": IMPORTER_VERSION,
        "shim_generator_version": _pack_shim.PACK_SHIM_VERSION,
        "shim_template_sha256": _pack_shim.template_sha256(),
        "tree_provenance_sha256": _sha256(_canonical(tree_prov) + b"\n"),
    }
    tools = []
    for t in table:
        tool = {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
            "output_schema": t["output_schema"],
        }
        tool["tool_schema_digest"] = M.tool_schema_digest(tool)
        tools.append(tool)
    source = {
        "transport": "stdio",
        "command": [INTERPRETER, SHIM_ENTRY],
        "package": {
            "kind": "vendored-tree",
            "entry": SHIM_ENTRY,
            "interpreter": INTERPRETER,
            "sha256": _sha256(tar_bytes),
            "tree_digest": tree_digest,
            "registry": registry,
        },
    }
    man_out: dict[str, Any] = {
        "schema": M.CAPABILITY_SCHEMA,
        "capability_id": capability_id,
        "capability_version": version,
        "publisher": namespace,
        "execution_model": "agentnode_executed_mcp",
        "source": source,
        "permissions": {
            "network": {"allowed_domains": []},
            "filesystem": {"level": "none"},
            "data_access": {"level": "input_only"},
            "env_keys": [],
            "code_execution": True,  # the capability runs python; the PACK declared no child processes
            "user_approval": "required",  # never lowered by the pack's own user_approval
        },
        "tools": tools,
        "artifact_digest": M.artifact_digest_for_stdio(source),
        "schema_digest": M.schema_digest(tools),
        "signer_role": "importing_publisher",
    }
    signed = M.sign_manifest(
        man_out, private_key, public_key_bytes, role="importing_publisher"
    )
    return ImportedPack(
        signed,
        tar_bytes,
        _sha256(tar_bytes),
        tree_digest,
        shim,
        table,
        tree_prov,
        registry,
    )
