"""AgentNode Runtime — LLM Agent Integration Layer.

Zero-config integration for LLM agents: automatic tool registration,
system prompt injection, and tool-loop execution.

Note: upgrade is a distribution/relationship type, NOT an execution model.
Runner and Policy ignore it. UI shows as "Add-on".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentnode_sdk.installer import read_lockfile
from agentnode_sdk.models import PromptArgumentSpec, PromptSpec, ResourceSpec
from agentnode_sdk.policy import check_run as _policy_check_run
from agentnode_sdk.policy import check_install as _policy_check_install
from agentnode_sdk.policy import audit_decision as _policy_audit
from agentnode_sdk.policy import _trust_meets_minimum


# ---------------------------------------------------------------------------
# Internal typed models
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """Neutral tool definition. JSON conversion only at edges."""

    name: str
    description: str
    input_schema: dict


@dataclass
class ParsedToolCall:
    """Parsed tool call from an LLM response."""

    name: str
    arguments: dict


@dataclass
class ToolError:
    """Structured error with machine-readable code."""

    code: str
    message: str


@dataclass
class ToolResult:
    """Internal result type. Converted to dict at public boundary."""

    success: bool
    result: dict | None = None
    error: ToolError | None = None
    metadata: dict | None = None


# ---------------------------------------------------------------------------
# Trust helper — delegates to policy._trust_meets_minimum (Phase B)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _result_to_dict(result: ToolResult) -> dict:
    """Convert ToolResult to public dict. Merges metadata to top-level."""
    d: dict[str, Any] = {"success": result.success}
    if result.result is not None:
        d["result"] = result.result
    if result.error is not None:
        d["error"] = {"code": result.error.code, "message": result.error.message}
    if result.metadata:
        d.update(result.metadata)
    return d


# ---------------------------------------------------------------------------
# Tool specifications
# ---------------------------------------------------------------------------

_TOOL_SPECS = [
    ToolSpec(
        name="agentnode_capabilities",
        description=(
            "List installed AgentNode capabilities. Call this first to check "
            "what tools are already available before searching for new ones."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
    ToolSpec(
        name="agentnode_search",
        description=(
            "Search the AgentNode registry for capabilities you don't have. "
            "Use when you need a tool for a specific task like reading PDFs, "
            "analyzing data, or browsing websites."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="agentnode_install",
        description=(
            "Install a specific AgentNode package by slug. Use after searching. "
            "Only packages meeting your trust level are allowed."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Package slug to install"},
            },
            "required": ["slug"],
        },
    ),
    ToolSpec(
        name="agentnode_run",
        description=(
            "Execute an installed AgentNode capability and return the result. "
            "If the package is not installed, use agentnode_install first. "
            "After receiving the result, present it to the user — do not call "
            "this tool again with the same arguments."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Package slug"},
                "tool_name": {
                    "type": "string",
                    "description": "Tool name for multi-tool packages",
                },
                "arguments": {
                    "type": "object",
                    "description": (
                        "Arguments to pass to the tool. "
                        "Example: {\"inputs\": {\"text\": \"hello world\"}}"
                    ),
                    "default": {},
                },
            },
            "required": ["slug"],
        },
    ),
    ToolSpec(
        name="agentnode_acquire",
        description=(
            "Find and install the best matching capability in one step. "
            "Shows what was selected. Use when the need is clear and you "
            "don't need to browse options."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "capability": {
                    "type": "string",
                    "description": "Capability description",
                },
            },
            "required": ["capability"],
        },
    ),
]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You have access to AgentNode tools. You MUST use them to complete tasks — "
    "never answer from memory when a tool call is required.\n\n"
    "Available tools: agentnode_capabilities, agentnode_search, agentnode_install, "
    "agentnode_run, agentnode_acquire.\n\n"
    "Workflow:\n"
    "1. To list installed tools → call agentnode_capabilities\n"
    "2. To find new tools → call agentnode_search, then agentnode_install to install\n"
    "3. To execute a tool → call agentnode_run with the slug and arguments\n"
    "4. After agentnode_run returns output, present the result to the user in text\n\n"
    "Rules:\n"
    "- ALWAYS call the tool rather than describing what you would do\n"
    "- When asked to search AND install, do both — search first, then install the result\n"
    "- When asked to run a tool, call agentnode_run — do not just call agentnode_capabilities\n"
    "- After receiving tool results, respond with a text summary for the user\n"
    "- Do not invent tool names or package slugs\n"
    "- Never repeat the same tool call with identical arguments"
)


# ---------------------------------------------------------------------------
# Provider loop functions (may raise — caller catches)
# ---------------------------------------------------------------------------

def _make_call_key(name: str, args: dict) -> str:
    """Stable key for dedup: tool name + sorted JSON args."""
    return name + ":" + json.dumps(args, sort_keys=True)


def _sanitize_tool_arguments(raw: str) -> dict:
    """Parse tool-call arguments JSON, tolerating open-source model quirks.

    Some models (e.g. Gemma 4 31B) leak special token fragments like ``<|\\``
    or ``<|end_of_turn|>`` into JSON string values, producing invalid escapes.
    This function strips those artifacts and retries before giving up.
    """
    if not raw:
        return {}

    # Fast path: valid JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    import re

    # Strip special-token artifacts: <|...|>, <|\, |>, etc.
    cleaned = re.sub(r"<\|[^>]*\|?>", "", raw)
    # Remove orphan <|\ that didn't close
    cleaned = re.sub(r"<\|\\?", "", cleaned)
    # Fix invalid \X escapes (not one of \", \\, \/, \b, \f, \n, \r, \t, \u)
    cleaned = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Last resort: try to extract a JSON object
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError(
        f"Cannot parse tool arguments after sanitization: {raw[:120]}",
        raw, 0,
    )


class _SyntheticMessage:
    """Minimal message object returned when the provider crashes after
    successful tool rounds.  Carries the last tool result as content so
    callers still receive a usable response."""

    def __init__(self, content: str):
        self.content = content
        self.tool_calls = None
        self.role = "assistant"


def _synthesize_from_tool_results(messages: list[dict]) -> _SyntheticMessage:
    """Build a synthetic assistant message from the last tool result in
    the conversation history.  Used when the model successfully executed
    tools but the provider crashed before the model could produce a text
    summary."""
    # Walk backwards to find the last tool result
    for msg in reversed(messages):
        if msg.get("role") == "tool":
            try:
                data = json.loads(msg["content"])
                result = data.get("result", data)
                return _SyntheticMessage(json.dumps(result, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError):
                return _SyntheticMessage(str(msg.get("content", "")))
    return _SyntheticMessage("")


def _run_openai_loop(
    runtime: Any,
    *,
    client: Any,
    messages: list[dict],
    model: str,
    max_tool_rounds: int,
) -> Any:
    """OpenAI-compatible tool loop with repeat-call detection."""
    tools = runtime.as_openai_tools()
    call_cache: dict[str, str] = {}  # call_key -> JSON result

    # Disable parallel tool calls — many OpenAI-compatible providers
    # (NVIDIA NIM, Ollama) only support single tool calls per turn.
    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "parallel_tool_calls": False,
    }

    last_msg = None
    completed_rounds = 0  # rounds with successful tool execution
    nudged = False  # whether we already sent a tool-use nudge
    for _ in range(max_tool_rounds):
        # Retry on transient provider errors (503 overloaded, 429 rate limit)
        response = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(**create_kwargs)
                break
            except Exception as api_err:
                err_str = str(api_err)
                retryable = (
                    "503" in err_str or "overloaded" in err_str
                    or "429" in err_str or "rate_limit" in err_str
                    # Some providers reject the model's own invalid JSON
                    # with 400 — retry gives the model another chance
                    or "Invalid \\escape" in err_str
                    or "invalid_request_error" in err_str
                )
                if retryable:
                    import time as _time
                    _time.sleep(2 ** attempt)
                    continue
                # If we already completed tool rounds, don't crash —
                # synthesize a response from the last tool results
                if completed_rounds > 0:
                    return _synthesize_from_tool_results(messages)
                raise
        if response is None:
            if completed_rounds > 0:
                return _synthesize_from_tool_results(messages)
            raise RuntimeError("Provider unavailable after 3 retries")
        if not response.choices:
            if completed_rounds > 0:
                return _synthesize_from_tool_results(messages)
            raise RuntimeError("Provider returned empty choices")
        last_msg = response.choices[0].message
        if last_msg is None:
            if completed_rounds > 0:
                return _synthesize_from_tool_results(messages)
            raise RuntimeError("Provider returned empty message")

        if not last_msg.tool_calls:
            # If the model answered with text but never called any tools,
            # nudge it once to actually use the available tools.
            if completed_rounds == 0 and not nudged:
                nudged = True
                messages.append(last_msg)
                messages.append({
                    "role": "user",
                    "content": (
                        "You have AgentNode tools available. Please use them "
                        "to complete the task instead of answering from memory. "
                        "Call the appropriate agentnode_* tool now."
                    ),
                })
                continue
            # Continuation nudge: if the model called some tools but
            # stopped with a text response, nudge once to continue.
            # This rescues "search-then-stop" patterns where the model
            # searches but doesn't install.
            if completed_rounds > 0 and not nudged:
                nudged = True
                messages.append(last_msg)
                messages.append({
                    "role": "user",
                    "content": (
                        "You completed one step but the task is not finished. "
                        "Please continue by calling the next agentnode_* tool. "
                        "For example, after searching, call agentnode_install. "
                        "After checking capabilities, call agentnode_run."
                    ),
                })
                continue
            return last_msg

        # Append assistant message with tool calls
        messages.append(last_msg)

        # Check for repeated identical calls
        all_repeated = True
        for tc in last_msg.tool_calls:
            try:
                args = _sanitize_tool_arguments(tc.function.arguments)
            except json.JSONDecodeError:
                # Totally unparseable — return error to model so it can retry
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({
                        "success": False,
                        "error": {
                            "code": "invalid_arguments",
                            "message": (
                                "Your tool call arguments contained invalid JSON. "
                                "Please retry with valid JSON. Do not include "
                                "special tokens or unescaped characters in string values."
                            ),
                        },
                    }),
                })
                all_repeated = False
                continue
            clean_name = AgentNodeRuntime._sanitize_tool_name(tc.function.name)
            call_key = _make_call_key(clean_name, args)

            if call_key in call_cache:
                # Return cached result with a stop hint
                cached = json.loads(call_cache[call_key])
                cached["note"] = (
                    "Duplicate call. This exact result was already returned. "
                    "Present it to the user now — do not call this tool again."
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(cached),
                })
            else:
                all_repeated = False
                result = runtime.handle(clean_name, args)
                result_json = json.dumps(result)
                call_cache[call_key] = result_json
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })

        if not all_repeated:
            completed_rounds += 1

        # If every call was a repeat, give one more chance to produce text
        if all_repeated:
            bail_response = client.chat.completions.create(**create_kwargs)
            return bail_response.choices[0].message

    # Max rounds reached — return last LLM response
    return last_msg


def _anthropic_content_to_dicts(content: Any) -> list[dict]:
    """Convert Anthropic ContentBlock objects to plain dicts."""
    result = []
    for block in content:
        if hasattr(block, "type"):
            if block.type == "text":
                result.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                result.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
    return result


def _run_anthropic_loop(
    runtime: Any,
    *,
    client: Any,
    messages: list[dict],
    model: str,
    max_tool_rounds: int,
) -> Any:
    """Anthropic-compatible tool loop with repeat-call detection."""
    tools = runtime.as_anthropic_tools()
    call_cache: dict[str, str] = {}  # call_key -> JSON result

    # Extract system from messages (Anthropic uses separate param)
    system = None
    filtered: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            system = m.get("content", "")
        else:
            filtered.append(m)

    create_kwargs: dict[str, Any] = {
        "model": model,
        "tools": tools,
        "max_tokens": 4096,
    }
    if system:
        create_kwargs["system"] = system

    response = None
    completed_rounds = 0
    nudged = False
    for _ in range(max_tool_rounds):
        create_kwargs["messages"] = filtered
        response = client.messages.create(**create_kwargs)

        # Check for tool use blocks
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            if not nudged:
                nudged = True
                filtered.append({
                    "role": "assistant",
                    "content": _anthropic_content_to_dicts(response.content),
                })
                nudge_text = (
                    "You have AgentNode tools available. Please use them "
                    "to complete the task instead of answering from memory. "
                    "Call the appropriate agentnode_* tool now."
                ) if completed_rounds == 0 else (
                    "You completed one step but the task is not finished. "
                    "Please continue by calling the next agentnode_* tool. "
                    "For example, after searching, call agentnode_install. "
                    "After checking capabilities, call agentnode_run."
                )
                filtered.append({"role": "user", "content": nudge_text})
                continue
            return response

        # Dispatch tool calls and build result messages
        tool_results = []
        all_repeated = True
        for tu in tool_uses:
            call_key = _make_call_key(tu.name, tu.input)

            if call_key in call_cache:
                cached = json.loads(call_cache[call_key])
                cached["note"] = (
                    "Duplicate call. This exact result was already returned. "
                    "Present it to the user now — do not call this tool again."
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(cached),
                })
            else:
                all_repeated = False
                result = runtime.handle(tu.name, tu.input)
                result_json = json.dumps(result)
                call_cache[call_key] = result_json
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_json,
                })

        if not all_repeated:
            completed_rounds += 1

        # Append assistant + tool results for next round
        filtered.append({
            "role": "assistant",
            "content": _anthropic_content_to_dicts(response.content),
        })
        filtered.append({"role": "user", "content": tool_results})

        # If all calls were repeats, give one more round then bail
        if all_repeated:
            create_kwargs["messages"] = filtered
            response = client.messages.create(**create_kwargs)
            return response

    return response


def _run_gemini_loop(
    runtime: Any,
    *,
    client: Any,
    messages: list[dict],
    model: str,
    max_tool_rounds: int,
) -> Any:
    """Google Gemini tool loop with repeat-call detection.

    Uses google-genai SDK (``from google import genai``).
    ``client`` must be a ``genai.Client`` instance.
    ``messages`` uses the same ``[{"role": ..., "content": ...}]`` format;
    this function converts to Gemini ``types.Content`` internally.
    """
    from google.genai import types as gtypes

    # Build Gemini tool declarations from runtime specs
    func_decls = []
    for ts in runtime.tool_specs():
        func_decls.append(gtypes.FunctionDeclaration(
            name=ts.name,
            description=ts.description,
            parameters_json_schema=ts.input_schema,
        ))
    tools = [gtypes.Tool(function_declarations=func_decls)]

    # Convert messages to Gemini contents
    system_instruction = None
    contents: list[Any] = []
    for m in messages:
        role = m.get("role", "user")
        text = m.get("content", "")
        if role == "system":
            system_instruction = text
        elif role == "assistant" or role == "model":
            contents.append(gtypes.Content(
                role="model",
                parts=[gtypes.Part.from_text(text=text)],
            ))
        else:
            contents.append(gtypes.Content(
                role="user",
                parts=[gtypes.Part.from_text(text=text)],
            ))

    config_kwargs: dict[str, Any] = {
        "tools": tools,
        "automatic_function_calling": gtypes.AutomaticFunctionCallingConfig(
            disable=True,
        ),
    }
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction

    call_cache: dict[str, str] = {}
    response = None
    completed_rounds = 0
    nudged = False

    for _ in range(max_tool_rounds):
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=gtypes.GenerateContentConfig(**config_kwargs),
        )

        # Check for function calls in response
        candidate = response.candidates[0] if response.candidates else None
        if not candidate or not candidate.content or not candidate.content.parts:
            return response

        function_calls = [
            p for p in candidate.content.parts if p.function_call
        ]
        if not function_calls:
            if not nudged:
                nudged = True
                contents.append(candidate.content)
                nudge_text = (
                    "You have AgentNode tools available. Please use them "
                    "to complete the task instead of answering from memory. "
                    "Call the appropriate agentnode_* tool now."
                ) if completed_rounds == 0 else (
                    "You completed one step but the task is not finished. "
                    "Please continue by calling the next agentnode_* tool. "
                    "For example, after searching, call agentnode_install. "
                    "After checking capabilities, call agentnode_run."
                )
                contents.append(gtypes.Content(
                    role="user",
                    parts=[gtypes.Part.from_text(text=nudge_text)],
                ))
                continue
            return response

        # Append assistant response to conversation
        contents.append(candidate.content)

        # Execute function calls and collect results
        result_parts: list[Any] = []
        all_repeated = True

        for part in function_calls:
            fc = part.function_call
            args = dict(fc.args) if fc.args else {}
            call_key = _make_call_key(fc.name, args)

            if call_key in call_cache:
                cached = json.loads(call_cache[call_key])
                cached["note"] = (
                    "Duplicate call. This exact result was already returned. "
                    "Present it to the user now — do not call this tool again."
                )
                result_parts.append(gtypes.Part.from_function_response(
                    name=fc.name,
                    response=cached,
                ))
            else:
                all_repeated = False
                result = runtime.handle(fc.name, args)
                result_json = json.dumps(result)
                call_cache[call_key] = result_json
                result_parts.append(gtypes.Part.from_function_response(
                    name=fc.name,
                    response=result,
                ))

        if not all_repeated:
            completed_rounds += 1

        # Append function results as user turn
        contents.append(gtypes.Content(role="user", parts=result_parts))

        # If all calls were repeated, give one more chance
        if all_repeated:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=gtypes.GenerateContentConfig(**config_kwargs),
            )
            return response

    return response


_PROVIDERS: dict[str, Any] = {
    "openai": _run_openai_loop,
    "anthropic": _run_anthropic_loop,
    "gemini": _run_gemini_loop,
}


# ---------------------------------------------------------------------------
# AgentNodeRuntime
# ---------------------------------------------------------------------------

class AgentNodeRuntime:
    """LLM Agent integration layer for AgentNode capabilities.

    Provides tool definitions, system prompt, and a dispatch/loop engine
    for connecting LLM agents to AgentNode's package ecosystem.
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        api_key: str | None = None,
        minimum_trust_level: str = "verified",
    ):
        self._minimum_trust_level = minimum_trust_level
        if client is not None:
            self._client = client
        else:
            from agentnode_sdk.client import AgentNodeClient

            self._client = AgentNodeClient(api_key=api_key or "")

    # --- Tool definitions (intern typed, extern JSON) ---

    def tool_specs(self) -> list[ToolSpec]:
        """Return internal typed tool specifications."""
        return list(_TOOL_SPECS)

    def prompt_specs(self) -> list[PromptSpec]:
        """Return prompt assets from all installed packages.

        Discovery only — prompts are NOT executable by the runtime.
        The host/agent decides how to use them.
        """
        lockfile = read_lockfile()
        packages = lockfile.get("packages", {})
        specs: list[PromptSpec] = []
        for slug, info in packages.items():
            for p in info.get("prompts", []):
                name = p.get("name")
                template = p.get("template")
                if not name or not template:
                    continue
                arguments = None
                if p.get("arguments"):
                    arguments = [
                        PromptArgumentSpec(
                            name=a["name"],
                            description=a.get("description"),
                            required=a.get("required", False),
                        )
                        for a in p["arguments"]
                        if a.get("name")
                    ]
                specs.append(PromptSpec(
                    name=name,
                    capability_id=p.get("capability_id", ""),
                    template=template,
                    description=p.get("description"),
                    arguments=arguments,
                ))
        return specs

    def resource_specs(self) -> list[ResourceSpec]:
        """Return resource assets from all installed packages.

        Discovery only — resources are NOT auto-loaded or injected.
        URI is identity, not a load instruction (S10).
        """
        lockfile = read_lockfile()
        packages = lockfile.get("packages", {})
        specs: list[ResourceSpec] = []
        for slug, info in packages.items():
            for r in info.get("resources", []):
                name = r.get("name")
                uri = r.get("uri")
                if not name or not uri:
                    continue
                specs.append(ResourceSpec(
                    name=name,
                    capability_id=r.get("capability_id", ""),
                    uri=uri,
                    description=r.get("description"),
                    mime_type=r.get("mime_type"),
                ))
        return specs

    def as_openai_tools(self) -> list[dict]:
        """Tool definitions in OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": ts.name,
                    "description": ts.description,
                    "parameters": ts.input_schema,
                },
            }
            for ts in _TOOL_SPECS
        ]

    def as_anthropic_tools(self) -> list[dict]:
        """Tool definitions in Anthropic format."""
        return [
            {
                "name": ts.name,
                "description": ts.description,
                "input_schema": ts.input_schema,
            }
            for ts in _TOOL_SPECS
        ]

    def as_gemini_tools(self) -> list:
        """Tool definitions in Google Gemini format.

        Returns a list with one ``types.Tool`` wrapping all function
        declarations.  Requires ``google-genai`` to be installed.
        """
        from google.genai import types as gtypes

        decls = [
            gtypes.FunctionDeclaration(
                name=ts.name,
                description=ts.description,
                parameters_json_schema=ts.input_schema,
            )
            for ts in _TOOL_SPECS
        ]
        return [gtypes.Tool(function_declarations=decls)]

    def as_generic_tools(self) -> list[dict]:
        """Tool definitions in generic/baseline format."""
        return [
            {
                "name": ts.name,
                "description": ts.description,
                "input_schema": ts.input_schema,
            }
            for ts in _TOOL_SPECS
        ]

    # --- System prompt ---

    def system_prompt(self) -> str:
        """AgentNode system prompt block. Append to existing prompts."""
        return _SYSTEM_PROMPT

    # --- Bundle (tools + prompt, always generic format) ---

    def tool_bundle(self) -> dict:
        """Combined tools (generic format) and system prompt."""
        return {
            "tools": self.as_generic_tools(),
            "system_prompt": self.system_prompt(),
        }

    # --- Executor (public API, never throws) ---

    @staticmethod
    def _sanitize_tool_name(name: str) -> str:
        """Clean tool name from model quirks.

        Some models append markdown fences, newlines, or other artifacts
        to tool names (e.g. ``agentnode_search\\n```json``).
        """
        import re
        # Strip whitespace, newlines, markdown fences
        cleaned = re.sub(r"[\n\r`]", "", name).strip()
        # Remove trailing non-identifier chars (e.g. punctuation)
        cleaned = re.sub(r"[^a-z0-9_].*$", "", cleaned, flags=re.IGNORECASE)
        return cleaned or name

    def handle(self, tool_name: str, arguments: dict | None = None) -> dict:
        """Dispatch a tool call. Returns structured dict. Never throws."""
        tool_name = self._sanitize_tool_name(tool_name)
        args = arguments or {}
        handlers = {
            "agentnode_capabilities": self._handle_capabilities,
            "agentnode_search": self._handle_search,
            "agentnode_install": self._handle_install,
            "agentnode_run": self._handle_run,
            "agentnode_acquire": self._handle_acquire,
        }
        handler = handlers.get(tool_name)
        # Fuzzy match: some models abbreviate or misspell tool names
        # (e.g. "agentnode_caps" → "agentnode_capabilities")
        if handler is None and tool_name.startswith("agentnode"):
            normalized = tool_name.replace(" ", "_").lower().strip()
            # Try prefix match first
            for name, h in handlers.items():
                if name.startswith(normalized) or normalized.startswith(name):
                    handler = h
                    break
            # Try substring match if prefix didn't work
            if handler is None:
                # Map common abbreviations
                key = normalized.replace("agentnode_", "")
                _FUZZY_MAP = {
                    "cap": "agentnode_capabilities",
                    "caps": "agentnode_capabilities",
                    "capabilities": "agentnode_capabilities",
                    "list": "agentnode_capabilities",
                    "search": "agentnode_search",
                    "find": "agentnode_search",
                    "query": "agentnode_search",
                    "install": "agentnode_install",
                    "add": "agentnode_install",
                    "get": "agentnode_install",
                    "run": "agentnode_run",
                    "exec": "agentnode_run",
                    "execute": "agentnode_run",
                    "acquire": "agentnode_acquire",
                    "req": "agentnode_search",
                }
                matched_name = _FUZZY_MAP.get(key)
                if matched_name:
                    handler = handlers.get(matched_name)
        if handler is None:
            return _result_to_dict(ToolResult(
                success=False,
                error=ToolError(
                    code="unknown_tool",
                    message=f"Unknown tool: {tool_name}. "
                    f"Available: {list(handlers.keys())}",
                ),
            ))
        try:
            result = handler(args)
            return _result_to_dict(result)
        except Exception as exc:
            return _result_to_dict(ToolResult(
                success=False,
                error=ToolError(code="internal_error", message=str(exc)),
            ))

    # --- Auto-loop (optional, requires provider SDK) ---

    def run(
        self,
        *,
        provider: str,
        client: Any,
        messages: list[dict],
        model: str = "",
        max_tool_rounds: int = 8,
        inject_system_prompt: bool = True,
    ) -> Any:
        """Run an LLM tool loop. Returns provider response. Never throws."""
        if not model:
            try:
                from agentnode_sdk.compatibility import recommend_model
                rec = recommend_model(provider)
                if rec:
                    model = rec
            except Exception:
                pass  # Fall through to provider loop which handles empty model

        if inject_system_prompt:
            prompt_block = self.system_prompt()
            has_system = False
            for m in messages:
                if m.get("role") == "system":
                    m["content"] = m["content"] + "\n\n" + prompt_block
                    has_system = True
                    break
            if not has_system:
                messages.insert(0, {"role": "system", "content": prompt_block})

        loop_fn = _PROVIDERS.get(provider)
        if loop_fn is None:
            return {
                "success": False,
                "error": {
                    "code": "unknown_provider",
                    "message": (
                        f"Unknown provider: {provider}. "
                        "Use 'openai', 'anthropic', or 'gemini'."
                    ),
                },
            }

        try:
            return loop_fn(
                self,
                client=client,
                messages=messages,
                model=model,
                max_tool_rounds=max_tool_rounds,
            )
        except Exception as exc:
            msg = str(exc)
            code = "loop_error"

            # Classify common provider errors for better diagnostics
            msg_lower = msg.lower()
            if "authentication" in msg_lower or "401" in msg or "api key" in msg_lower:
                code = "authentication_error"
                msg = f"API key invalid or missing. {msg}"
            elif "rate limit" in msg_lower or "429" in msg or "quota" in msg_lower:
                code = "rate_limit_error"
                msg = f"Rate limited by provider. Retry after a short wait. {msg}"
            elif "not found" in msg_lower or "404" in msg:
                code = "model_not_found"
                msg = f"Model not available on this provider. {msg}"
            elif "single tool-calls" in msg_lower or "parallel" in msg_lower:
                code = "parallel_tool_error"
                msg = (
                    f"Provider rejected parallel tool calls. "
                    f"This is handled automatically — if you see this, "
                    f"please report it. {msg}"
                )
            elif "timeout" in msg_lower or "timed out" in msg_lower:
                code = "timeout_error"
                msg = f"Provider did not respond in time. {msg}"

            return {
                "success": False,
                "error": {
                    "code": code,
                    "message": msg,
                },
            }

    # --- Internal handlers ---

    def _handle_capabilities(self, args: dict) -> ToolResult:
        """List installed packages from lockfile. Purely local."""
        lockfile = read_lockfile()
        packages = lockfile.get("packages", {})
        pkg_list = []
        for slug, info in packages.items():
            tools = [t.get("name", "") for t in info.get("tools", [])]
            pkg_list.append({
                "slug": slug,
                "version": info.get("version", ""),
                "trust_level": info.get("trust_level", "unverified"),  # P1-SDK9
                "tools": tools,
                "capability_ids": info.get("capability_ids", []),
            })
        hint = ""
        if pkg_list:
            hint = (
                "If you need to run one of these tools, call agentnode_run "
                f"with the package slug (e.g. slug='{pkg_list[0]['slug']}'). "
                "Do NOT just describe the result — call the tool."
            )
        else:
            hint = (
                "No packages installed. Use agentnode_search to find packages, "
                "then agentnode_install to install them."
            )
        return ToolResult(
            success=True,
            result={
                "installed_count": len(pkg_list),
                "packages": pkg_list,
                "hint": hint,
            },
        )

    def _handle_search(self, args: dict) -> ToolResult:
        """Search registry. Max 5 results."""
        query = args.get("query", "")
        if not query:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="missing_parameter",
                    message="'query' is required.",
                ),
            )
        search_result = self._client.search(query=query, per_page=5)
        results = [
            {
                "slug": hit.slug,
                "name": hit.name,
                "summary": hit.summary,
                "trust_level": hit.trust_level,
                "version": hit.latest_version,
                "downloads": hit.download_count,
                "capabilities": hit.capability_ids,
            }
            for hit in search_result.hits[:5]
        ]
        hint = ""
        if results:
            hint = (
                f"IMPORTANT: Do NOT respond to the user yet. "
                f"Your next step is to call agentnode_install with "
                f"slug='{results[0]['slug']}' to install this package. "
                f"After installing, use agentnode_run to execute it."
            )
        return ToolResult(
            success=True,
            result={
                "total": search_result.total,
                "results": results,
                "next_action": f"call agentnode_install(slug='{results[0]['slug']}')" if results else None,
                "hint": hint,
            },
        )

    def _handle_install(self, args: dict) -> ToolResult:
        """Install package with trust enforcement."""
        slug = args.get("slug", "")
        if not slug:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="missing_parameter",
                    message="'slug' is required.",
                ),
            )

        # Check if already installed
        lockfile = read_lockfile()
        existing = lockfile.get("packages", {}).get(slug)
        if existing:
            tools = [t.get("name", "") for t in existing.get("tools", [])]
            return ToolResult(
                success=True,
                result={
                    "slug": slug,
                    "version": existing.get("version", ""),
                    "message": (
                        f"Package '{slug}' is already installed. "
                        "Use agentnode_run to execute it — do not install again."
                    ),
                    "trust_level": existing.get("trust_level", "unverified"),  # P1-SDK9
                    "already_installed": True,
                    "available_tools": tools,
                },
            )

        # Policy check deferred to client.install() which has real metadata
        # (trust_level, permissions from API). We only audit the install
        # intent here. The actual check_install() runs in client.install()
        # with real data — see BD-10, PHASE-A.

        require_trusted = self._minimum_trust_level in ("trusted", "curated")
        require_verified = self._minimum_trust_level == "verified"
        install_result = self._client.install(
            slug,
            require_trusted=require_trusted,
            require_verified=require_verified,
        )

        if not install_result.installed:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="install_failed",
                    message=install_result.message,
                ),
            )

        # Post-check for curated minimum (client only enforces trusted+)
        if (
            self._minimum_trust_level == "curated"
            and install_result.trust_level
            and not _trust_meets_minimum(install_result.trust_level, "curated")
        ):
            return ToolResult(
                success=False,
                error=ToolError(
                    code="trust_blocked",
                    message=(
                        f"Package '{slug}' has trust_level='{install_result.trust_level}' "
                        f"but policy requires '{self._minimum_trust_level}'. "
                        "Cannot install automatically. Inform the user about the trust requirement."
                    ),
                ),
            )

        return ToolResult(
            success=True,
            result={
                "slug": install_result.slug,
                "version": install_result.version,
                "message": install_result.message,
                "trust_level": install_result.trust_level,
            },
        )

    def _handle_run(self, args: dict) -> ToolResult:
        """Execute an installed tool."""
        slug = args.get("slug", "")
        if not slug:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="missing_parameter",
                    message="'slug' is required.",
                ),
            )

        tool_name = args.get("tool_name")
        arguments = args.get("arguments", {})

        # Tolerate flat argument structures: if the LLM puts tool
        # arguments at the top level (e.g. {"slug": "x", "inputs": {...}})
        # instead of nesting them inside "arguments", collect the extras.
        if not arguments:
            _reserved = {"slug", "tool_name", "arguments"}
            extras = {k: v for k, v in args.items() if k not in _reserved}
            if extras:
                arguments = extras

        # Validate package exists and check multi-tool
        lockfile = read_lockfile()
        packages = lockfile.get("packages", {})
        entry = packages.get(slug)
        if entry is None:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="not_installed",
                    message=(
                        f"Package '{slug}' is not installed. "
                        f"Use agentnode_install with slug='{slug}' first, "
                        "then retry agentnode_run."
                    ),
                ),
            )

        tools = entry.get("tools", [])
        if len(tools) > 1 and not tool_name:
            available = [t.get("name", "") for t in tools]
            return ToolResult(
                success=False,
                error=ToolError(
                    code="tool_name_required",
                    message="This package has multiple tools. Provide tool_name.",
                ),
                metadata={"available_tools": available},
            )

        # Auto-select single tool
        if len(tools) == 1 and not tool_name:
            tool_name = tools[0].get("name")

        # Policy check (pre-execution)
        decision = _policy_check_run(slug, tool_name, arguments, entry, interactive=True)
        _policy_audit(
            decision, "runtime_run", slug,
            tool_name=tool_name,
            trust_level=entry.get("trust_level"),
        )
        if decision.action == "deny":
            return ToolResult(
                success=False,
                error=ToolError("policy_denied", decision.reason),
            )
        if decision.action == "prompt":
            return ToolResult(
                success=False,
                error=ToolError(
                    "policy_prompt",
                    f"Policy requires approval: {decision.reason}",
                ),
            )

        run_result = self._client.run_tool(slug, tool_name, **arguments)
        if not run_result.success:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="run_failed",
                    message=run_result.error or "Tool execution failed.",
                ),
            )
        return ToolResult(
            success=True,
            result={
                "output": run_result.result,
                "duration_ms": run_result.duration_ms,
                "action": "present_to_user",
                "hint": (
                    "Task complete. Present this result to the user in your "
                    "next text response. Do not call any more tools."
                ),
            },
        )

    def _handle_acquire(self, args: dict) -> ToolResult:
        """Search + install in one step."""
        capability = args.get("capability", "")
        if not capability:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="missing_parameter",
                    message="'capability' is required.",
                ),
            )

        require_trusted = self._minimum_trust_level in ("trusted", "curated")
        require_verified = self._minimum_trust_level == "verified"

        install_result = self._client.resolve_and_install(
            [capability],
            require_trusted=require_trusted,
            require_verified=require_verified,
        )

        if not install_result.installed:
            return ToolResult(
                success=False,
                error=ToolError(
                    code="acquire_failed",
                    message=install_result.message,
                ),
            )

        # Get alternatives count via resolve
        alternatives_count = 0
        try:
            resolve_result = self._client.resolve([capability])
            alternatives_count = max(0, resolve_result.total - 1)
        except Exception:
            pass

        return ToolResult(
            success=True,
            result={
                "selected": {
                    "slug": install_result.slug,
                    "name": install_result.slug,
                    "version": install_result.version,
                    "trust_level": install_result.trust_level,
                },
                "alternatives_count": alternatives_count,
            },
        )
