"""Reference Research Agent — LangChain version.

Demonstrates using the AgentNode LangChain adapter to load
installed ANP packs as LangChain StructuredTools.

Prerequisites:
    pip install agentnode-sdk agentnode-langchain langchain langchain-openai
    agentnode install web-search-pack
    agentnode install webpage-extractor-pack
    agentnode install pdf-reader-pack
"""

from __future__ import annotations

from agentnode_langchain import load_tool


def get_tools():
    """Load all 3 starter packs as LangChain tools."""
    return [
        load_tool("web-search-pack"),
        load_tool("webpage-extractor-pack"),
        load_tool("pdf-reader-pack"),
    ]


def main():
    """Demonstrate tool loading and direct invocation."""
    print("Loading AgentNode tools via LangChain adapter...\n")

    tools = get_tools()
    for tool in tools:
        print(f"  Loaded: {tool.name} — {tool.description}")

    print(f"\n{len(tools)} tools ready for LangChain agent integration.")
    print("\nExample: pass these tools to create_react_agent() or AgentExecutor.")
    print("See LangChain docs for agent setup with custom tools.")


if __name__ == "__main__":
    main()
