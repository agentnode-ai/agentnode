"""AgentNode Demo: Low-capability model + AgentNode tools = powerful agent.

Shows how a small LLM (Haiku, Mistral 7B, etc.) gains real capabilities
through AgentNode's tool registry — search, install, execute, all automatic.

Usage:
    python demo/agent_demo.py "Zähle die Wörter in diesem Text: Hallo Welt wie geht es dir"
    python demo/agent_demo.py "Fasse diesen Text zusammen: <langer text>"
    python demo/agent_demo.py "Übersetze 'Good morning' ins Deutsche"
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Fix Windows terminal encoding for Unicode output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load .env from demo/ directory
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from agentnode_sdk.client import AgentNodeClient

# ---------------------------------------------------------------------------
# LLM Backends
# ---------------------------------------------------------------------------

def _call_anthropic(messages: list[dict], tools: list[dict]) -> dict:
    """Call Claude Haiku via Anthropic API."""
    import anthropic
    client = anthropic.Anthropic()

    # Convert tools to Anthropic format
    anthropic_tools = []
    for t in tools:
        anthropic_tools.append({
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        })

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=(
            "Du bist ein hilfreicher Assistent. Du hast Zugriff auf AgentNode-Tools. "
            "Nutze die Tools um Aufgaben zu erledigen. Antworte auf Deutsch."
        ),
        messages=messages,
        tools=anthropic_tools if anthropic_tools else None,
    )
    return _parse_anthropic_response(response)


def _call_openrouter(messages: list[dict], tools: list[dict]) -> dict:
    """Call a small model via OpenRouter (OpenAI-compatible)."""
    import httpx

    model = os.environ.get("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct")
    api_key = os.environ["OPENROUTER_API_KEY"]

    # Convert messages to OpenAI format
    oai_messages = [{"role": "system", "content": (
        "Du bist ein hilfreicher Assistent. Du hast Zugriff auf AgentNode-Tools. "
        "Nutze die Tools um Aufgaben zu erledigen. Antworte auf Deutsch."
    )}]
    for m in messages:
        oai_messages.append(m)

    body: dict = {
        "model": model,
        "messages": oai_messages,
        "max_tokens": 1024,
    }
    if tools:
        # OpenAI-format tools
        oai_tools = []
        for t in tools:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            })
        body["tools"] = oai_tools

    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://agentnode.net",
            "X-Title": "AgentNode Demo",
        },
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    return _parse_openai_response(resp.json())


def _parse_anthropic_response(response) -> dict:
    """Parse Anthropic response into unified format."""
    result: dict = {"text": None, "tool_calls": []}
    for block in response.content:
        if block.type == "text":
            result["text"] = block.text
        elif block.type == "tool_use":
            result["tool_calls"].append({
                "id": block.id,
                "name": block.name,
                "arguments": block.input,
            })
    result["stop_reason"] = response.stop_reason
    return result


def _parse_openai_response(data: dict) -> dict:
    """Parse OpenAI/OpenRouter response into unified format."""
    choice = data["choices"][0]
    msg = choice["message"]
    result: dict = {"text": msg.get("content"), "tool_calls": []}
    for tc in msg.get("tool_calls", []) or []:
        args = tc["function"].get("arguments", "{}")
        if isinstance(args, str):
            args = json.loads(args)
        result["tool_calls"].append({
            "id": tc["id"],
            "name": tc["function"]["name"],
            "arguments": args,
        })
    result["stop_reason"] = choice.get("finish_reason")
    return result


# ---------------------------------------------------------------------------
# Tool Registry — maps AgentNode tools to LLM tool definitions
# ---------------------------------------------------------------------------

# Tools we offer to the LLM. Each maps to an AgentNode package.
AVAILABLE_TOOLS = [
    {
        "name": "count_words",
        "description": "Zählt Wörter, Sätze und Zeichen in einem Text. Gibt Statistiken zurück.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Der zu analysierende Text"},
            },
            "required": ["text"],
        },
        "agentnode_slug": "word-counter-pack",
        "agentnode_tool": None,
    },
    {
        "name": "analyze_csv",
        "description": "Analysiert CSV-Daten: Spalten, Zeilen, Statistiken, Zusammenfassung.",
        "parameters": {
            "type": "object",
            "properties": {
                "csv_data": {"type": "string", "description": "CSV-Daten als String"},
            },
            "required": ["csv_data"],
        },
        "agentnode_slug": "csv-analyzer-pack",
        "agentnode_tool": None,
    },
    {
        "name": "translate_text",
        "description": "Übersetzt Text in eine Zielsprache.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Der zu übersetzende Text"},
                "target_language": {"type": "string", "description": "Zielsprache (z.B. 'de', 'en', 'fr')"},
            },
            "required": ["text", "target_language"],
        },
        "agentnode_slug": "text-translator-pack",
        "agentnode_tool": None,
    },
    {
        "name": "summarize_text",
        "description": "Erstellt eine Zusammenfassung eines Textes.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Der zusammenzufassende Text"},
                "max_sentences": {"type": "integer", "description": "Maximale Anzahl Sätze", "default": 3},
            },
            "required": ["text"],
        },
        "agentnode_slug": "document-summarizer-pack",
        "agentnode_tool": None,
    },
    {
        "name": "process_json",
        "description": "Verarbeitet und transformiert JSON-Daten. Kann filtern, sortieren, extrahieren.",
        "parameters": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "JSON-Daten als String"},
                "operation": {"type": "string", "description": "Operation: 'format', 'validate', 'extract'"},
            },
            "required": ["data"],
        },
        "agentnode_slug": "json-processor-pack",
        "agentnode_tool": None,
    },
    {
        "name": "lint_code",
        "description": "Prüft Code auf Fehler und Stil-Probleme.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Der zu prüfende Code"},
                "language": {"type": "string", "description": "Programmiersprache (z.B. 'python', 'javascript')"},
            },
            "required": ["code", "language"],
        },
        "agentnode_slug": "code-linter-pack",
        "agentnode_tool": None,
    },
    {
        "name": "build_regex",
        "description": "Erstellt und testet reguläre Ausdrücke basierend auf einer Beschreibung.",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Beschreibung was der Regex matchen soll"},
                "test_strings": {"type": "string", "description": "Komma-getrennte Test-Strings"},
            },
            "required": ["description"],
        },
        "agentnode_slug": "regex-builder-pack",
        "agentnode_tool": None,
    },
]


# ---------------------------------------------------------------------------
# Agent Loop
# ---------------------------------------------------------------------------

class AgentNodeAgent:
    """Minimal agent: LLM reasoning + AgentNode tool execution."""

    def __init__(self, provider: str = "anthropic"):
        self.provider = provider
        self.an = AgentNodeClient(
            base_url=os.environ.get("AGENTNODE_API_URL", "https://api.agentnode.net"),
            api_key=os.environ.get("AGENTNODE_API_KEY"),
        )
        self._installed: set[str] = set()
        self._call_llm = _call_anthropic if provider == "anthropic" else _call_openrouter

    def _ensure_installed(self, slug: str) -> bool:
        """Install a tool if not yet installed."""
        if slug in self._installed:
            return True

        print(f"  [AgentNode] Installing {slug}...", end=" ", flush=True)
        t0 = time.monotonic()
        try:
            result = self.an.install(slug)
            elapsed = time.monotonic() - t0
            if result.installed or result.already_installed:
                self._installed.add(slug)
                status = "already installed" if result.already_installed else f"installed v{result.version}"
                print(f"{status} ({elapsed:.1f}s)")
                return True
            else:
                print(f"FAILED: {result.message}")
                return False
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"ERROR: {e} ({elapsed:.1f}s)")
            return False

    def _resolve_tool_name(self, slug: str) -> str | None:
        """Read the first tool name from the lockfile for a package."""
        try:
            from agentnode_sdk.installer import read_lockfile
            data = read_lockfile()
            entry = data.get("packages", {}).get(slug, {})
            tools = entry.get("tools", [])
            if tools:
                return tools[0].get("name")
        except Exception:
            pass
        return None

    def _execute_tool(self, tool_call: dict) -> str:
        """Execute a tool call via AgentNode SDK."""
        name = tool_call["name"]
        args = tool_call["arguments"]

        # Find the tool definition
        tool_def = next((t for t in AVAILABLE_TOOLS if t["name"] == name), None)
        if not tool_def:
            return json.dumps({"error": f"Unknown tool: {name}"})

        slug = tool_def["agentnode_slug"]

        # Install if needed
        if not self._ensure_installed(slug):
            return json.dumps({"error": f"Failed to install {slug}"})

        # Resolve the actual tool name from the lockfile
        tool_name = self._resolve_tool_name(slug)

        # Execute
        print(f"  [AgentNode] Running {slug}:{tool_name or 'default'}({', '.join(f'{k}={repr(v)[:50]}' for k,v in args.items())})...")
        t0 = time.monotonic()
        try:
            result = self.an.run_tool(slug, tool_name, **args)
            elapsed = time.monotonic() - t0
            if result.success:
                print(f"  [AgentNode] Done ({elapsed:.1f}s, {result.mode_used} mode)")
                return json.dumps(result.result, ensure_ascii=False, default=str) if result.result is not None else "{}"
            else:
                print(f"  [AgentNode] Failed: {result.error}")
                return json.dumps({"error": result.error})
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"  [AgentNode] Exception: {e} ({elapsed:.1f}s)")
            return json.dumps({"error": str(e)})

    def run(self, task: str, max_turns: int = 5) -> str:
        """Run the agent loop."""
        model_name = "Claude Haiku" if self.provider == "anthropic" else os.environ.get("OPENROUTER_MODEL", "mistral-7b")
        print(f"\n{'='*60}")
        print(f"  AgentNode Demo Agent")
        print(f"  Model: {model_name}")
        print(f"  Tools: {len(AVAILABLE_TOOLS)} AgentNode packages available")
        print(f"{'='*60}")
        print(f"\n  Task: {task}\n")

        # Build tool definitions for LLM (without agentnode_slug)
        llm_tools = [
            {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
            for t in AVAILABLE_TOOLS
        ]

        messages: list[dict] = [{"role": "user", "content": task}]

        for turn in range(max_turns):
            print(f"  --- Turn {turn + 1} ---")

            # Call LLM
            t0 = time.monotonic()
            response = self._call_llm(messages, llm_tools)
            llm_time = time.monotonic() - t0
            print(f"  [LLM] Response in {llm_time:.1f}s")

            # If LLM returned text and no tool calls, we're done
            if not response["tool_calls"]:
                final = response.get("text", "")
                print(f"\n  Final Answer:\n  {final}\n")
                return final

            # Process tool calls
            if response.get("text"):
                print(f"  [LLM] Thinking: {response['text'][:100]}...")

            # Build assistant message with tool use
            if self.provider == "anthropic":
                # For Anthropic: add assistant message then tool results
                assistant_content = []
                if response.get("text"):
                    assistant_content.append({"type": "text", "text": response["text"]})
                for tc in response["tool_calls"]:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["arguments"],
                    })
                messages.append({"role": "assistant", "content": assistant_content})

                # Execute each tool and add results
                tool_results = []
                for tc in response["tool_calls"]:
                    result_str = self._execute_tool(tc)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": result_str,
                    })
                messages.append({"role": "user", "content": tool_results})

            else:
                # For OpenRouter/OpenAI format
                assistant_msg: dict = {"role": "assistant", "content": response.get("text")}
                if response["tool_calls"]:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                        }
                        for tc in response["tool_calls"]
                    ]
                messages.append(assistant_msg)

                for tc in response["tool_calls"]:
                    result_str = self._execute_tool(tc)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })

        print("  [Agent] Max turns reached.")
        return response.get("text", "")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    # Determine provider — default to OpenRouter
    provider = "openrouter"
    if os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("OPENROUTER_API_KEY"):
        provider = "anthropic"
    if "--anthropic" in sys.argv:
        provider = "anthropic"
        sys.argv.remove("--anthropic")

    # Get task
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        # Default demo tasks
        task = input("\n  Gib eine Aufgabe ein: ").strip()
        if not task:
            task = "Zähle die Wörter in diesem Text: 'Die AgentNode Plattform ermöglicht es KI-Modellen, neue Fähigkeiten aus einem Tool-Registry zu installieren und auszuführen.'"

    # Check prerequisites
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Set it in demo/.env or environment.")
        sys.exit(1)
    if provider == "openrouter" and not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY not set. Set it in demo/.env or environment.")
        sys.exit(1)

    agent = AgentNodeAgent(provider=provider)
    result = agent.run(task)

    print(f"{'='*60}")
    print("  Demo complete.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
