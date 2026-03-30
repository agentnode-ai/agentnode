# agentnode-crewai

AgentNode adapter for CrewAI. Exposes AgentNode runtime tools as native CrewAI `BaseTool` instances.

## Installation

```bash
pip install agentnode-crewai
```

## Usage

```python
from crewai import Agent
from agentnode_crewai import get_crewai_tools

tools = get_crewai_tools()
agent = Agent(role="assistant", tools=tools, ...)
```

Each tool delegates to `AgentNodeRuntime.handle()` — no extra logic, no duplication.
