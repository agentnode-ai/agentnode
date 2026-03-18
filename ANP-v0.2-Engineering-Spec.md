# ANP v0.2 Engineering Spec

Compatible extension of ANP v0.1. This document specifies exact code changes.

## 1. Entrypoint Format

### 1.1 Unified Format

All entrypoints use the same format: `module.path` or `module.path:function`.

```
module.path          = shorthand for module.path:run
module.path:function = explicit function reference
```

Examples:
```
pdf_reader_pack.tool            → import pdf_reader_pack.tool; call run()
csv_analyzer_pack.tool:describe → import csv_analyzer_pack.tool; call describe()
csv_analyzer_pack.tool:run      → import csv_analyzer_pack.tool; call run()
```

Invalid:
```
describe                        → no module path
/path/to/file.py                → filesystem path
csv_analyzer_pack/tool.py       → slash separator
```

### 1.2 Entrypoint Rules by manifest_version

**v0.1** (unchanged):
- Package-level `entrypoint` is REQUIRED
- Format: `module.path` (no `:function` suffix)
- Tool-level `entrypoint` is ignored if present
- All tools invoked via `module.run()`

**v0.2**:
- If pack declares exactly 1 tool: package-level `entrypoint` is sufficient
- If pack declares >1 tool: each tool MUST have its own `entrypoint`
- Package-level `entrypoint` becomes OPTIONAL when all tools have their own
- Tool-level format: `module.path:function` (`:function` required for tool-level)
- If tool has no entrypoint AND package-level exists → use `package_entrypoint:run`
- NO silent fallback for multi-tool packs. Validator rejects if >1 tool and any tool lacks entrypoint.

### 1.3 Regex Validation

```python
# v0.1 (unchanged)
ENTRYPOINT_PATTERN_V1 = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$")

# v0.2 tool-level: module.path:function_name
ENTRYPOINT_PATTERN_V2 = re.compile(
    r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+:[a-z_][a-z0-9_]*$"
)
```

## 2. Manifest Normalization

### 2.1 normalize_manifest()

New function in `backend/app/packages/validator.py`.

Called ONLY for `manifest_version: "0.2"`. v0.1 manifests pass through unchanged.

```python
MANIFEST_DEFAULTS = {
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "dependencies": [],
    "tags": [],
    "categories": [],
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
        "external_integrations": [],
    },
    "security": {
        "signature": "",
        "provenance": {"source_repo": "", "commit": "", "build_system": "manual"},
    },
    "support": {"homepage": "", "issues": ""},
    "deprecation_policy": "6-months-notice",
}

def normalize_manifest(manifest: dict) -> dict:
    """Apply v0.2 defaults to compact manifests. Only for manifest_version 0.2."""
    if manifest.get("manifest_version") != "0.2":
        return manifest

    m = {**manifest}
    for key, default in MANIFEST_DEFAULTS.items():
        if key not in m:
            m[key] = default
        elif isinstance(default, dict) and isinstance(m[key], dict):
            merged = {**default, **m[key]}
            m[key] = merged

    # Ensure capabilities has resources/prompts arrays
    caps = m.get("capabilities", {})
    caps.setdefault("resources", [])
    caps.setdefault("prompts", [])
    m["capabilities"] = caps

    return m
```

### 2.2 Publish Flow Order

```
1. normalize_manifest()     — apply defaults (v0.2 only)
2. validate_manifest()      — validate the normalized manifest
3. store manifest_raw       — store the NORMALIZED form in DB
```

### 2.3 Validator Changes

```python
async def validate_manifest(manifest, session=None):
    version = manifest.get("manifest_version")

    # Accept both "0.1" and "0.2"
    if version not in ("0.1", "0.2"):
        errors.append("manifest_version MUST be '0.1' or '0.2'")

    # v0.2: validate tool-level entrypoints
    if version == "0.2":
        tools = manifest.get("capabilities", {}).get("tools", [])
        pkg_entrypoint = manifest.get("entrypoint", "")

        if len(tools) > 1:
            # Multi-tool: every tool MUST have own entrypoint
            for i, tool in enumerate(tools):
                tool_ep = tool.get("entrypoint", "")
                if not tool_ep:
                    errors.append(
                        f"tools[{i}].entrypoint is required when pack has multiple tools"
                    )
                elif not ENTRYPOINT_PATTERN_V2.match(tool_ep):
                    errors.append(
                        f"tools[{i}].entrypoint must be module.path:function (got '{tool_ep}')"
                    )
        elif len(tools) == 1:
            # Single tool: package-level entrypoint or tool-level
            tool_ep = tools[0].get("entrypoint", "")
            if tool_ep and not ENTRYPOINT_PATTERN_V2.match(tool_ep):
                errors.append(
                    f"tools[0].entrypoint must be module.path:function (got '{tool_ep}')"
                )
            if not tool_ep and not pkg_entrypoint:
                errors.append("Either package-level or tool-level entrypoint is required")

        # Package-level entrypoint is optional in v0.2 if all tools have their own
        all_tools_have_ep = all(t.get("entrypoint") for t in tools)
        if not pkg_entrypoint and not all_tools_have_ep:
            errors.append("Package-level entrypoint required when not all tools define their own")

    else:
        # v0.1: package-level entrypoint required, old validation
        entrypoint = manifest.get("entrypoint", "")
        if not entrypoint:
            errors.append("entrypoint is required")
        elif not ENTRYPOINT_PATTERN_V1.match(entrypoint):
            errors.append(f"entrypoint must be a valid Python module path")
```

## 3. Database Changes

### 3.1 Migration: Add entrypoint to capabilities table

```sql
ALTER TABLE capabilities ADD COLUMN entrypoint TEXT;
```

Alembic migration file: `add_capability_entrypoint.py`

```python
def upgrade():
    op.add_column("capabilities", sa.Column("entrypoint", sa.Text(), nullable=True))

def downgrade():
    op.drop_column("capabilities", "entrypoint")
```

### 3.2 ORM Model Change

File: `backend/app/packages/models.py`, class `Capability`:

```python
class Capability(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "capabilities"

    package_version_id = Column(...)
    capability_type = Column(...)
    capability_id = Column(...)
    name = Column(...)
    description = Column(...)
    input_schema = Column(JSONB, nullable=True)
    output_schema = Column(JSONB, nullable=True)
    entrypoint = Column(Text, nullable=True)  # NEW: v0.2 per-tool entrypoint

    package_version = relationship(...)
```

### 3.3 Publish Service Change

File: `backend/app/packages/service.py`, in the capabilities creation loop:

```python
# Current (line 216-224):
for tool in capabilities.get("tools", []):
    session.add(Capability(
        package_version_id=pv.id,
        capability_type="tool",
        capability_id=tool["capability_id"],
        name=tool["name"],
        description=tool.get("description"),
        input_schema=tool.get("input_schema"),
        output_schema=tool.get("output_schema"),
    ))

# Changed:
for tool in capabilities.get("tools", []):
    session.add(Capability(
        package_version_id=pv.id,
        capability_type="tool",
        capability_id=tool["capability_id"],
        name=tool["name"],
        description=tool.get("description"),
        input_schema=tool.get("input_schema"),
        output_schema=tool.get("output_schema"),
        entrypoint=tool.get("entrypoint"),  # NEW
    ))
```

Also add normalization call at the top of `publish_package()`:

```python
async def publish_package(manifest, publisher_id, session, artifact_bytes=None):
    # NEW: normalize before validation
    from app.packages.validator import normalize_manifest
    manifest = normalize_manifest(manifest)

    # existing: validate
    valid, errors, warnings = await validate_manifest(manifest, session)
    ...
```

## 4. API Response Changes

### 4.1 CapabilityBlock Schema

File: `backend/app/packages/schemas.py`:

```python
# Current:
class CapabilityBlock(BaseModel):
    name: str
    capability_id: str
    capability_type: str
    description: str | None

# Changed:
class CapabilityBlock(BaseModel):
    name: str
    capability_id: str
    capability_type: str
    description: str | None
    entrypoint: str | None = None          # NEW
    input_schema: dict | None = None       # NEW
    output_schema: dict | None = None      # NEW
```

### 4.2 Assembler Change

File: `backend/app/packages/assembler.py`, line 52-58:

```python
# Current:
for cap in version.capabilities:
    blocks_caps.append(CapabilityBlock(
        name=cap.name,
        capability_id=cap.capability_id,
        capability_type=cap.capability_type,
        description=cap.description,
    ))

# Changed:
for cap in version.capabilities:
    blocks_caps.append(CapabilityBlock(
        name=cap.name,
        capability_id=cap.capability_id,
        capability_type=cap.capability_type,
        description=cap.description,
        entrypoint=cap.entrypoint,           # NEW
        input_schema=cap.input_schema,        # NEW
        output_schema=cap.output_schema,      # NEW
    ))
```

## 5. SDK Changes

### 5.1 AgentNodeToolError

File: `sdk/agentnode_sdk/exceptions.py` — add:

```python
class AgentNodeToolError(Exception):
    """Base error for ANP tool execution failures.

    Pack authors should raise this (or a subclass) on tool errors.
    Adapters catch this to produce framework-appropriate error responses.
    """
    pass
```

Export from `sdk/agentnode_sdk/__init__.py`:
```python
from .exceptions import AgentNodeToolError
```

### 5.2 SDK load_tool() — Support tool_name

File: `sdk/agentnode_sdk/installer.py`, replace `load_tool()`:

```python
def load_tool(slug: str, tool_name: str | None = None) -> Any:
    """Load a tool function from an installed package.

    Args:
        slug: Package slug.
        tool_name: Specific tool name for multi-tool packs.
            If None, returns module.run for single-tool packs.

    Returns:
        The callable tool function (not the module).
    """
    data = read_lockfile()
    pkg = data.get("packages", {}).get(slug)
    if not pkg:
        raise ImportError(f"Package '{slug}' not installed.")

    tools = pkg.get("tools", [])

    if tool_name:
        # Find specific tool
        tool = next((t for t in tools if t["name"] == tool_name), None)
        if not tool:
            available = [t["name"] for t in tools]
            raise ImportError(
                f"Tool '{tool_name}' not found in '{slug}'. Available: {available}"
            )
        return _resolve_entrypoint(tool["entrypoint"])

    # No tool_name specified
    if len(tools) > 1:
        available = [t["name"] for t in tools]
        raise ImportError(
            f"Package '{slug}' has multiple tools: {available}. "
            f"Specify tool_name."
        )

    if tools and tools[0].get("entrypoint"):
        return _resolve_entrypoint(tools[0]["entrypoint"])

    # Fallback: v0.1 style module.run
    entrypoint = pkg.get("entrypoint", "")
    if not entrypoint:
        raise ImportError(f"No entrypoint for '{slug}'.")
    module = importlib.import_module(entrypoint)
    return module.run


def _resolve_entrypoint(entrypoint_str: str):
    """Import module and return the specific function.

    Handles both 'module.path:function' and 'module.path' (defaults to run).
    """
    if ":" in entrypoint_str:
        module_path, func_name = entrypoint_str.rsplit(":", 1)
    else:
        module_path = entrypoint_str
        func_name = "run"

    module = importlib.import_module(module_path)
    func = getattr(module, func_name, None)
    if func is None:
        raise ImportError(f"Function '{func_name}' not found in '{module_path}'.")
    return func
```

## 6. Lockfile Changes

### 6.1 New Lockfile Format (v0.2)

```json
{
  "lockfile_version": "0.2",
  "updated_at": "2026-03-17T10:00:00Z",
  "packages": {
    "csv-analyzer-pack": {
      "version": "1.1.0",
      "package_type": "toolpack",
      "entrypoint": "csv_analyzer_pack.tool",
      "capability_ids": ["csv_analysis", "data_cleaning"],
      "artifact_hash": "sha256:abc123...",
      "installed_at": "2026-03-17T10:00:00Z",
      "source": "cli",
      "tools": [
        {
          "name": "describe_csv",
          "entrypoint": "csv_analyzer_pack.tool:describe",
          "capability_id": "csv_analysis"
        },
        {
          "name": "filter_csv",
          "entrypoint": "csv_analyzer_pack.tool:filter_rows",
          "capability_id": "data_cleaning"
        }
      ]
    },
    "pdf-reader-pack": {
      "version": "1.0.0",
      "package_type": "toolpack",
      "entrypoint": "pdf_reader_pack.tool",
      "capability_ids": ["pdf_extraction"],
      "artifact_hash": "sha256:def456...",
      "installed_at": "2026-03-17T09:00:00Z",
      "source": "cli",
      "tools": []
    }
  }
}
```

Backward compatible: `tools` defaults to `[]`. Old lockfiles without `tools` still work.
Lockfile version bumps to "0.2" but old "0.1" is still readable.

### 6.2 CLI writes tools from install-info API response

The install-info API response must include the tools list. CLI writes it to lockfile.

## 7. LangChain Adapter Changes

File: `adapter-langchain/agentnode_langchain/loader.py`

### 7.1 load_tool() — Support tool_name

```python
def load_tool(
    package_slug: str,
    tool_name: str | None = None,
    api_key: str = "",
    version: str = "",
) -> StructuredTool:
    """Load a tool from an installed ANP package.

    For multi-tool packs, tool_name is required.
    For single-tool packs, tool_name is optional.
    """
    client = AgentNode(api_key=api_key)
    try:
        pkg = client.get_package(package_slug)
        caps = pkg.get("blocks", {}).get("capabilities", [])

        if tool_name:
            cap = next((c for c in caps if c["name"] == tool_name), None)
            if not cap:
                available = [c["name"] for c in caps]
                raise ValueError(
                    f"Tool '{tool_name}' not found. Available: {available}"
                )
        elif len(caps) == 1:
            cap = caps[0]
        else:
            available = [c["name"] for c in caps]
            raise ValueError(
                f"Package has multiple tools: {available}. Specify tool_name."
            )

        # Resolve the function
        ep = cap.get("entrypoint")
        if ep:
            func = _resolve_entrypoint(ep)
        else:
            # v0.1 fallback
            pkg_ep = pkg.get("blocks", {}).get("install", {}).get("entrypoint", "")
            module = importlib.import_module(pkg_ep)
            func = module.run

        # Build args schema if input_schema is available
        args_schema = None
        if cap.get("input_schema"):
            args_schema = _json_schema_to_pydantic(cap["name"], cap["input_schema"])

        return StructuredTool.from_function(
            func=func,
            name=cap["name"],
            description=cap.get("description", f"Tool from {package_slug}"),
            args_schema=args_schema,
        )
    finally:
        client.close()


def _resolve_entrypoint(entrypoint_str: str):
    if ":" in entrypoint_str:
        module_path, func_name = entrypoint_str.rsplit(":", 1)
    else:
        module_path, func_name = entrypoint_str, "run"
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        raise ImportError(
            f"Module '{module_path}' not installed. Run: agentnode install <pack>"
        )
    func = getattr(module, func_name, None)
    if func is None:
        raise ImportError(f"Function '{func_name}' not found in '{module_path}'")
    return func
```

### 7.2 AgentNodeTool._run() — Use entrypoint from capability

```python
class AgentNodeTool(BaseTool):
    entrypoint: str | None = None

    def _run(self, **kwargs):
        if self.entrypoint:
            func = _resolve_entrypoint(self.entrypoint)
            result = func(**kwargs)
            return str(result) if not isinstance(result, str) else result
        raise ToolException(f"No entrypoint for {self.package_slug}")
```

## 8. MCP Adapter Changes

File: `adapter-mcp/agentnode_mcp/server.py`

### 8.1 Multi-tool support

Each tool in a multi-tool pack becomes a separate MCP tool.

```python
def _load_pack_tools(slug: str) -> list[dict]:
    """Load tools from a pack. Returns list of {name, func, schema, description}."""
    module_name = slug.replace("-", "_") + ".tool"
    module = importlib.import_module(module_name)

    # Try to read lockfile for tool-level entrypoints
    lock = _read_lockfile()
    pkg_lock = lock.get("packages", {}).get(slug, {})
    tools_list = pkg_lock.get("tools", [])

    if tools_list:
        # v0.2: multiple tools with explicit entrypoints
        result = []
        for tool_info in tools_list:
            ep = tool_info.get("entrypoint", "")
            if ":" in ep:
                mod_path, func_name = ep.rsplit(":", 1)
                mod = importlib.import_module(mod_path)
                func = getattr(mod, func_name)
            else:
                func = module.run

            result.append({
                "name": f"{slug}_{tool_info['name']}",
                "func": func,
                "schema": _get_run_params(func),
                "description": f"{tool_info['name']} from {slug}",
            })
        return result

    # v0.1 fallback: single tool from run()
    if hasattr(module, "run"):
        return [{
            "name": slug,
            "func": module.run,
            "schema": _get_run_params(module.run),
            "description": (getattr(module.run, "__doc__", "") or "").strip().split("\n")[0],
        }]

    return []
```

### 8.2 create_server() uses per-tool entries

```python
def create_server(pack_slugs: list[str]) -> Server:
    app = Server("agentnode")

    # Load all tools from all packs
    all_tools: dict[str, dict] = {}  # name -> {func, schema, description}
    for slug in pack_slugs:
        try:
            tools = _load_pack_tools(slug)
            for t in tools:
                all_tools[t["name"]] = t
        except Exception as e:
            logger.error(f"Failed to load {slug}: {e}")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=name, description=t["description"], inputSchema=t["schema"])
            for name, t in all_tools.items()
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name not in all_tools:
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown: {name}"}))]
        try:
            result = all_tools[name]["func"](**arguments)
            return [TextContent(type="text", text=json.dumps(result, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    return app
```

## 9. Install-Info API Response Change

The `GET /v1/packages/{slug}/install-info` response must include the tools list
so CLI and SDK can write it to the lockfile.

Add to the response:

```json
{
  "package_slug": "csv-analyzer-pack",
  "latest_version": "1.1.0",
  "artifact_url": "...",
  "artifact_hash": "sha256:...",
  "entrypoint": "csv_analyzer_pack.tool",
  "tools": [
    {
      "name": "describe_csv",
      "entrypoint": "csv_analyzer_pack.tool:describe",
      "capability_id": "csv_analysis"
    },
    {
      "name": "filter_csv",
      "entrypoint": "csv_analyzer_pack.tool:filter_rows",
      "capability_id": "data_cleaning"
    }
  ],
  "deprecated": false
}
```

## 10. Backward Compatibility Rules

1. `manifest_version: "0.1"` manifests are validated with existing rules, unchanged
2. `manifest_version: "0.2"` manifests are normalized then validated with extended rules
3. Lockfile `tools: []` is valid — v0.1 packs have empty tools array
4. All adapters check for tool-level entrypoint first, fall back to `module.run()`
5. API responses include `entrypoint: null` for v0.1 capabilities (no breaking change)
6. Existing published packages are not affected — they keep manifest_version "0.1"

## 11. Implementation Order

### Phase 1 — Backend (no breaking changes)

1. Alembic migration: `capabilities.entrypoint` column
2. `normalize_manifest()` function in validator.py
3. Validator: accept "0.2", validate tool entrypoints
4. Publish service: store tool entrypoints in Capability rows
5. Schema: add entrypoint/input_schema/output_schema to CapabilityBlock
6. Assembler: include new fields in response
7. Install-info endpoint: include tools in response

### Phase 2 — Consumers

8. SDK: `AgentNodeToolError` + updated `load_tool(slug, tool_name)`
9. LangChain adapter: `_resolve_entrypoint()` + `load_tool(slug, tool_name)`
10. MCP adapter: multi-tool per pack
11. CLI lockfile: write tools array from install-info

### Phase 3 — Reference packs

12. Migrate csv-analyzer-pack to v0.2 (multi-tool)
13. Migrate browser-automation-pack to v0.2 (multi-tool)
14. Migrate sql-generator-pack to v0.2 (multi-tool)

## 12. Additional Files Found During Verification

These were NOT in the original spec draft but MUST be changed:

### 12.1 backend/app/install/schemas.py — CapabilityInfo

```python
# Current (line 20-23):
class CapabilityInfo(BaseModel):
    name: str
    capability_id: str
    capability_type: str

# Changed:
class CapabilityInfo(BaseModel):
    name: str
    capability_id: str
    capability_type: str
    entrypoint: str | None = None  # NEW
```

Also update `InstallResponse` to include tools:

```python
class ToolInfo(BaseModel):
    name: str
    entrypoint: str
    capability_id: str

class InstallResponse(BaseModel):
    # ... existing fields ...
    tools: list[ToolInfo] = []  # NEW
```

### 12.2 backend/app/install/router.py — install-info endpoint (line 87-94)

```python
# Current:
capabilities = [
    CapabilityInfo(
        name=c.name,
        capability_id=c.capability_id,
        capability_type=c.capability_type,
    )
    for c in pv.capabilities
]

# Changed:
capabilities = [
    CapabilityInfo(
        name=c.name,
        capability_id=c.capability_id,
        capability_type=c.capability_type,
        entrypoint=c.entrypoint,  # NEW
    )
    for c in pv.capabilities
]
```

### 12.3 sdk/agentnode_sdk/models.py — CapabilityInfo dataclass

```python
# Current (line 64-68):
@dataclass
class CapabilityInfo:
    name: str
    capability_id: str
    capability_type: str

# Changed:
@dataclass
class CapabilityInfo:
    name: str
    capability_id: str
    capability_type: str
    entrypoint: str | None = None  # NEW
```

### 12.4 cli/src/lockfile.ts — LockEntry interface

```typescript
// Current (line 9-17):
export interface LockEntry {
  version: string;
  package_type: string;
  entrypoint: string;
  capability_ids: string[];
  artifact_hash: string;
  installed_at: string;
  source: string;
}

// Changed:
export interface ToolEntry {
  name: string;
  entrypoint: string;
  capability_id: string;
}

export interface LockEntry {
  version: string;
  package_type: string;
  entrypoint: string;
  capability_ids: string[];
  artifact_hash: string;
  installed_at: string;
  source: string;
  tools: ToolEntry[];  // NEW — empty for v0.1 packs
}
```

### 12.5 cli/src/installer.ts — ArtifactMeta + lockfile write

```typescript
// ArtifactMeta needs tools:
interface ArtifactMeta {
  // ... existing fields ...
  tools: Array<{ name: string; entrypoint: string; capability_id: string }>;
}

// Lockfile write (line 113-121) — add tools:
updateLockEntry(slug, {
  version,
  package_type: meta.package_type,
  entrypoint: meta.entrypoint,
  capability_ids: meta.capability_ids || [],
  artifact_hash: `sha256:${localHash}`,
  installed_at: new Date().toISOString(),
  source: "cli",
  tools: meta.tools || [],  // NEW
});
```

Also `verifyEntrypoint()` (line 229-264) must handle `module.path:function` format:
strip the `:function` suffix before converting to file path.

## 13. Complete File Change List (Verified)

### Phase 1 — Backend (13 files)
1. `backend/app/packages/models.py` — Capability.entrypoint column
2. `backend/app/packages/validator.py` — normalize_manifest() + v0.2 rules
3. `backend/app/packages/service.py` — store tool entrypoints + call normalize
4. `backend/app/packages/schemas.py` — CapabilityBlock + entrypoint/schemas
5. `backend/app/packages/assembler.py` — include new fields
6. `backend/app/install/schemas.py` — CapabilityInfo.entrypoint + ToolInfo
7. `backend/app/install/router.py` — pass entrypoint in CapabilityInfo
8. `backend/alembic/versions/` — new migration file

### Phase 2 — SDK + Adapters (5 files)
9. `sdk/agentnode_sdk/exceptions.py` — AgentNodeToolError
10. `sdk/agentnode_sdk/models.py` — CapabilityInfo.entrypoint
11. `sdk/agentnode_sdk/installer.py` — load_tool(slug, tool_name)
12. `adapter-langchain/agentnode_langchain/loader.py` — _resolve_entrypoint
13. `adapter-mcp/agentnode_mcp/server.py` — multi-tool per pack

### Phase 3 — CLI (3 files)
14. `cli/src/lockfile.ts` — ToolEntry + LockEntry.tools
15. `cli/src/installer.ts` — ArtifactMeta.tools + lockfile write + verifyEntrypoint
16. `cli/src/commands/install.ts` — pass tools through from API

### Phase 4 — Reference packs (3 packs, 2 files each = 6 files)
17-22. csv-analyzer-pack, browser-automation-pack, sql-generator-pack
       (agentnode.yaml + tool.py each)

### NOT changed (verified):
- `backend/app/resolution/engine.py` — matches by capability_id, doesn't need entrypoint
- `backend/app/shared/meili.py` — search index doesn't need per-tool entrypoints
- `backend/app/search/router.py` — filters by capability_id, unchanged
- `web/src/app/packages/[slug]/page.tsx` — reads capabilities, works with new fields automatically (entrypoint shown if present, null otherwise)

**Total: 22 files across 4 phases.**

## 14. Minimal v0.2 Manifest Example

```yaml
manifest_version: "0.2"
package_id: "csv-analyzer-pack"
package_type: "toolpack"
name: "CSV Analyzer Pack"
publisher: "agentnode"
version: "1.1.0"
summary: "Analyze and filter CSV files."

capabilities:
  tools:
    - name: "describe_csv"
      capability_id: "csv_analysis"
      entrypoint: "csv_analyzer_pack.tool:describe"
      description: "Return summary statistics for a CSV file"
      input_schema:
        type: object
        properties:
          file_path: { type: string, description: "Path to CSV file" }
        required: ["file_path"]

    - name: "head_csv"
      capability_id: "csv_analysis"
      entrypoint: "csv_analyzer_pack.tool:head"
      description: "Return the first N rows of a CSV file"
      input_schema:
        type: object
        properties:
          file_path: { type: string }
          n: { type: integer, default: 5 }
        required: ["file_path"]

    - name: "filter_csv"
      capability_id: "data_cleaning"
      entrypoint: "csv_analyzer_pack.tool:filter_rows"
      description: "Filter rows by a column condition"
      input_schema:
        type: object
        properties:
          file_path: { type: string }
          column: { type: string }
          value: { type: string }
          operator: { type: string, default: "==" }
        required: ["file_path", "column", "value"]

tags: ["csv", "analysis", "data"]
compatibility:
  frameworks: ["generic"]
```

Everything else gets defaults: runtime=python, permissions=all none, etc.

Full normalized form (90+ lines) is what the backend stores in manifest_raw.
