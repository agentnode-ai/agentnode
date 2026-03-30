"""AgentNode tools exposed as CrewAI BaseTool instances."""
from __future__ import annotations

import json
from typing import Any, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, create_model

from agentnode_sdk import AgentNodeRuntime


class EmptyInput(BaseModel):
    """Fallback schema when tool has no parameters or schema is invalid."""

    pass


def _build_args_schema(input_schema: dict) -> Type[BaseModel]:
    """Build a Pydantic model from a JSON Schema dict.

    Returns EmptyInput on missing/invalid schema.
    """
    if not input_schema or not isinstance(input_schema, dict):
        return EmptyInput

    properties = input_schema.get("properties", {})
    if not properties:
        return EmptyInput

    required = set(input_schema.get("required", []))

    fields: dict[str, Any] = {}
    for name, prop in properties.items():
        json_type = prop.get("type", "string")
        py_type: type = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "object": dict,
            "array": list,
        }.get(json_type, str)

        description = prop.get("description", "")
        default = prop.get("default", ...)

        if name in required and default is ...:
            fields[name] = (py_type, Field(..., description=description))
        else:
            if default is ...:
                default = None
                py_type = py_type | None  # type: ignore[operator]
            fields[name] = (py_type, Field(default=default, description=description))

    try:
        return create_model("ToolInput", **fields)
    except Exception:
        return EmptyInput


class AgentNodeTool(BaseTool):
    """CrewAI tool backed by AgentNodeRuntime.handle()."""

    name: str = ""
    description: str = ""
    args_schema: Type[BaseModel] = EmptyInput

    _runtime: Any = None
    _tool_name: str = ""

    def __init__(self, runtime: AgentNodeRuntime, spec: dict, **kwargs: Any):
        tool_name = spec.get("name", "")
        schema = _build_args_schema(spec.get("input_schema", {}))

        super().__init__(
            name=tool_name,
            description=spec.get("description", ""),
            args_schema=schema,
            **kwargs,
        )
        self._runtime = runtime
        self._tool_name = tool_name

    def _run(self, **kwargs: Any) -> str:
        """Delegate to runtime.handle(). Never raises."""
        try:
            result = self._runtime.handle(self._tool_name, kwargs)
            return json.dumps(result)
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)})


def get_crewai_tools(
    *,
    api_key: str | None = None,
    minimum_trust_level: str = "verified",
) -> list[AgentNodeTool]:
    """Create CrewAI tools from AgentNodeRuntime.

    Returns a list of BaseTool instances — one per AgentNode meta-tool.
    """
    runtime = AgentNodeRuntime(api_key=api_key, minimum_trust_level=minimum_trust_level)
    specs = runtime.as_generic_tools()
    return [AgentNodeTool(runtime, spec) for spec in specs]
