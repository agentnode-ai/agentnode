"""Real-world: Mixed file — CrewAI BaseTool using LangChain under the hood, tested as platform=langchain.
Source: docs.crewai.com LangChainTool integration pattern
"""
import os
from dotenv import load_dotenv
from crewai import Agent
from crewai.tools import BaseTool
from pydantic import Field
from langchain_community.utilities import GoogleSerperAPIWrapper

load_dotenv()

search = GoogleSerperAPIWrapper()


class SearchTool(BaseTool):
    name: str = "Search"
    description: str = "Useful for search-based queries."
    search: GoogleSerperAPIWrapper = Field(default_factory=GoogleSerperAPIWrapper)

    def _run(self, query: str) -> str:
        """Execute the search query and return results."""
        try:
            return self.search.run(query)
        except Exception as e:
            return f"Error performing search: {str(e)}"


researcher = Agent(
    role="Research Analyst",
    goal="Gather current market data and trends",
    backstory="Expert research analyst with years of experience.",
    tools=[SearchTool()],
    verbose=True,
)
