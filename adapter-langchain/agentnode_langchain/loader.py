"""Convert AgentNode capabilities to LangChain tools. Spec §15. Supports ANP v0.2."""
from __future__ import annotations

import importlib
from typing import Any, Type

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel, create_model

from agentnode_sdk import AgentNode, AgentNodeClient
from agentnode_sdk.exceptions import AgentNodeToolError


def _resolve_entrypoint(entrypoint: str) -> tuple[str, str]:
    """Parse entrypoint into (module_path, function_name).

    "my_pack.tool"           → ("my_pack.tool", "run")
    "my_pack.tool:describe"  → ("my_pack.tool", "describe")
    """
    if ":" in entrypoint:
        module_path, func_name = entrypoint.rsplit(":", 1)
        return module_path, func_name
    return entrypoint, "run"


def load_tool(
    package_slug: str, api_key: str = "", version: str = "",
    tool_name: str | None = None,
) -> StructuredTool:
    """Load an installed ANP package as a LangChain StructuredTool.

    The package MUST already be installed via `agentnode install <slug>`.
    This adapter does NOT auto-install packages.

    Args:
        package_slug: Package slug.
        api_key: Optional API key.
        version: Optional version constraint.
        tool_name: For v0.2 multi-tool packs, the specific tool to load.
    """
    client = AgentNode(api_key=api_key)
    try:
        pkg = client.get_package(package_slug)
        meta = client.get_install_metadata(package_slug, version=version)

        caps = pkg.get("blocks", {}).get("capabilities", [])
        if not caps:
            caps = meta.get("capabilities", [])

        # Find the target capability
        target_cap = None
        if tool_name:
            for cap in caps:
                if cap.get("name") == tool_name:
                    target_cap = cap
                    break
            if not target_cap:
                raise ImportError(
                    f"Tool '{tool_name}' not found in package '{package_slug}'. "
                    f"Available: {[c.get('name') for c in caps]}"
                )
        else:
            target_cap = caps[0] if caps else {}

        # Resolve entrypoint
        tool_ep = target_cap.get("entrypoint") if target_cap else None
        pkg_ep = meta.get("entrypoint") or pkg.get("blocks", {}).get("install", {}).get("entrypoint")

        if tool_ep:
            module_path, func_name = _resolve_entrypoint(tool_ep)
        elif pkg_ep:
            module_path, func_name = _resolve_entrypoint(pkg_ep)
        else:
            module_path = package_slug.replace("-", "_")
            func_name = "run"

        try:
            module = importlib.import_module(module_path)
        except ImportError:
            raise ImportError(
                f"Package '{package_slug}' is not installed locally. "
                f"Run: agentnode install {package_slug}"
            )

        func = getattr(module, func_name, None)
        if func is None:
            raise ImportError(
                f"Function '{func_name}' not found in module '{module_path}' "
                f"for package '{package_slug}'."
            )

        cap_name = target_cap.get("name", package_slug) if target_cap else package_slug
        cap_desc = target_cap.get("description", f"Tool from {package_slug}") if target_cap else f"Tool from {package_slug}"

        return StructuredTool.from_function(
            func=func,
            name=cap_name,
            description=cap_desc,
        )
    finally:
        client.close()


def _json_schema_to_pydantic(name: str, schema: dict | None) -> Type[BaseModel]:
    """Convert a JSON Schema dict to a Pydantic model for LangChain tool args."""
    if not schema or "properties" not in schema:
        return create_model(f"{name}Input")

    fields = {}
    required = set(schema.get("required", []))
    for prop_name, prop_schema in schema.get("properties", {}).items():
        type_map = {"string": str, "integer": int, "number": float, "boolean": bool}
        prop_type = type_map.get(prop_schema.get("type", "string"), str)

        if prop_name in required:
            fields[prop_name] = (prop_type, ...)
        else:
            fields[prop_name] = (prop_type | None, None)

    return create_model(f"{name}Input", **fields)


class AgentNodeTool(BaseTool):
    """A LangChain tool backed by an AgentNode capability. Supports v0.2 per-tool entrypoints."""

    name: str
    description: str
    capability_id: str
    package_slug: str
    entrypoint: str | None = None
    args_schema: Type[BaseModel] | None = None

    def _run(self, **kwargs: Any) -> str:
        """Execute the tool by importing and calling the installed package."""
        if self.entrypoint:
            module_path, func_name = _resolve_entrypoint(self.entrypoint)
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                raise ToolException(
                    f"Package '{self.package_slug}' is not installed locally. "
                    f"Run: agentnode install {self.package_slug}"
                )
            func = getattr(module, func_name, None)
            if func is None:
                raise ToolException(
                    f"Function '{func_name}' not found in '{module_path}'."
                )
            try:
                result = func(**kwargs)
                return str(result) if not isinstance(result, str) else result
            except AgentNodeToolError as e:
                raise ToolException(f"Tool error in {self.name}: {e}")
            except Exception as e:
                raise ToolException(f"Error executing {self.name}: {e}")
        raise ToolException(
            f"No entrypoint configured for {self.package_slug}. "
            f"Run: agentnode install {self.package_slug}"
        )


class AgentNodeToolkit:
    """Load tools from AgentNode based on capability resolution."""

    def __init__(
        self,
        base_url: str = "https://api.agentnode.net/v1",
        api_key: str | None = None,
    ):
        self.client = AgentNodeClient(base_url=base_url, api_key=api_key)

    def get_tools(
        self,
        capabilities: list[str],
        framework: str = "langchain",
        limit: int = 5,
    ) -> list[BaseTool]:
        """Resolve capabilities and return LangChain tools.

        For v0.2 packs with per-tool entrypoints, each tool gets its own
        entrypoint. For v0.1 packs, all tools share the package entrypoint.
        """
        result = self.client.resolve(capabilities, framework=framework, limit=limit)
        tools: list[BaseTool] = []

        for pkg in result.results:
            meta = self.client.get_install_metadata(pkg.slug)
            for cap in meta.capabilities:
                # v0.2: use per-tool entrypoint if available, else package-level
                ep = cap.entrypoint if hasattr(cap, "entrypoint") and cap.entrypoint else meta.entrypoint

                args_model = None
                if hasattr(cap, "input_schema") and cap.input_schema:
                    args_model = _json_schema_to_pydantic(cap.name, cap.input_schema)

                tool = AgentNodeTool(
                    name=cap.name,
                    description=f"{cap.name} from {pkg.slug} ({cap.capability_id})",
                    capability_id=cap.capability_id,
                    package_slug=pkg.slug,
                    entrypoint=ep,
                    args_schema=args_model,
                )
                tools.append(tool)

        return tools

    def close(self):
        self.client.close()


def load_tools(
    capabilities: list[str],
    base_url: str = "https://api.agentnode.net/v1",
    api_key: str | None = None,
    framework: str = "langchain",
) -> list[BaseTool]:
    """Convenience function to resolve capabilities and load as LangChain tools."""
    toolkit = AgentNodeToolkit(base_url=base_url, api_key=api_key)
    try:
        return toolkit.get_tools(capabilities, framework=framework)
    finally:
        toolkit.close()
