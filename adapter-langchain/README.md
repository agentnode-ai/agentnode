# agentnode-langchain

LangChain adapter for [AgentNode](https://agentnode.net) — use AgentNode packages as LangChain tools.

## Installation

```bash
pip install agentnode-langchain
```

## Quick Start

```python
from agentnode_langchain import load_tool, load_tools, AgentNodeToolkit

# Load a single tool
tool = load_tool("pdf-reader-pack", api_key="ank_...")

# Load multiple tools
tools = load_tools(["pdf-reader-pack", "web-search-pack"], api_key="ank_...")

# Use with a LangChain agent
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o")
agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools)
result = executor.invoke({"input": "Extract text from report.pdf"})
```

## API

### `load_tool(slug, api_key=None, **kwargs)`

Load a single AgentNode package as a LangChain tool.

### `load_tools(slugs, api_key=None, **kwargs)`

Load multiple packages as LangChain tools.

### `AgentNodeToolkit`

A LangChain toolkit that resolves capabilities to tools automatically.

```python
toolkit = AgentNodeToolkit(
    capabilities=["pdf_extraction", "web_search"],
    api_key="ank_...",
    framework="langchain",
)
tools = toolkit.get_tools()
```

## License

MIT
