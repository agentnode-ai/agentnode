"""Real-world: @tool function mixed with Agent setup in same file.
Source: github.com/crewAIInc/crewAI/issues/949
"""
from crewai_tools import tool
from crewai import Agent


@tool
def file_writer_tool(filename: str, content: str) -> str:
    """Writes given content to a specified file."""
    with open(filename, "w") as file:
        file.write(content)
    return f"Content successfully written to {filename}"


researcher = Agent(
    role="Knowledge Article Writer",
    goal="Create content of professional domains longer than 1000 words",
    backstory="Write articles about Game Design.",
    verbose=True,
    allow_delegation=False,
    max_iter=10,
    tools=[file_writer_tool],
)
