"""Convert AgentNode capabilities to LangChain tools. Spec §15."""
from __future__ import annotations

import importlib
from typing import Any, Type

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel, create_model

from agentnode_sdk import AgentNode, AgentNodeClient


def load_tool(
    package_slug: str, api_key: str = "", version: str = ""
) -> StructuredTool:
    """Load an installed ANP package as a LangChain StructuredTool.

    The package MUST already be installed via `agentnode install <slug>`.
    This adapter does NOT auto-install packages.
    """
    client = AgentNode(api_key=api_key)
    try:
        pkg = client.get_package(package_slug)
        entrypoint = pkg.get("blocks", {}).get("install", {}).get("entrypoint")
        if not entrypoint:
            # Fallback: try top-level entrypoint or derive from slug
            meta = client.get_install_metadata(package_slug, version=version)
            entrypoint = meta.get("entrypoint") or package_slug.replace("-", "_")

        try:
            module = importlib.import_module(entrypoint)
        except ImportError:
            raise ImportError(
                f"Package '{package_slug}' is not installed locally. "
                f"Run: agentnode install {package_slug}"
            )

        # Get capability info for name/description
        caps = pkg.get("blocks", {}).get("capabilities", [])
        if not caps:
            meta = meta if "meta" in dir() else client.get_install_metadata(
                package_slug, version=version
            )
            caps = meta.get("capabilities", [])

        cap_name = caps[0]["name"] if caps else package_slug
        cap_desc = (
            caps[0].get("description", f"Tool from {package_slug}")
            if caps
            else f"Tool from {package_slug}"
        )

        return StructuredTool.from_function(
            func=module.run,
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
    """A LangChain tool backed by an AgentNode capability."""

    name: str
    description: str
    capability_id: str
    package_slug: str
    entrypoint: str | None = None
    args_schema: Type[BaseModel] | None = None

    def _run(self, **kwargs: Any) -> str:
        """Execute the tool by importing and calling the installed package."""
        if self.entrypoint:
            try:
                module = importlib.import_module(self.entrypoint)
                result = module.run(**kwargs)
                return str(result) if not isinstance(result, str) else result
            except ImportError:
                raise ToolException(
                    f"Package '{self.package_slug}' is not installed locally. "
                    f"Run: agentnode install {self.package_slug}"
                )
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
        """Resolve capabilities and return LangChain tools."""
        result = self.client.resolve(capabilities, framework=framework, limit=limit)
        tools: list[BaseTool] = []

        for pkg in result.results:
            meta = self.client.get_install_metadata(pkg.slug)
            for cap in meta.capabilities:
                tool = AgentNodeTool(
                    name=cap.name,
                    description=f"{cap.name} from {pkg.slug} ({cap.capability_id})",
                    capability_id=cap.capability_id,
                    package_slug=pkg.slug,
                    entrypoint=meta.entrypoint,
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
