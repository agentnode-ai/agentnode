"""
File mixing a @tool function with an Agent() instantiation.
People sometimes define a tool and wire it into an agent in the same file —
creates import complexity and side effects at load time.
"""

import os

import requests
from crewai import Agent
from crewai_tools import tool
from langchain_openai import ChatOpenAI


@tool("Wikipedia Summary")
def get_wikipedia_summary(topic: str, sentences: int = 3) -> dict:
    """
    Fetch a summary from Wikipedia for a given topic.

    Args:
        topic: The topic to look up on Wikipedia
        sentences: Number of sentences to return (1–10)

    Returns:
        dict with title, summary, url, and related topics
    """
    sentences = max(1, min(sentences, 10))
    url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + topic.replace(" ", "_")

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "CrewAI-Agent/1.0 (research tool)"},
            timeout=10,
        )
        if resp.status_code == 404:
            return {"error": f"Wikipedia page not found for: {topic}", "topic": topic}
        resp.raise_for_status()

        data = resp.json()
        extract = data.get("extract", "")
        # truncate to requested sentences
        sent_list = [s.strip() for s in extract.split(".") if s.strip()]
        truncated = ". ".join(sent_list[:sentences]) + ("." if sent_list else "")

        return {
            "title": data.get("title", topic),
            "summary": truncated,
            "full_extract": extract,
            "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
            "thumbnail": data.get("thumbnail", {}).get("source", ""),
            "topic": topic,
            "error": None,
        }
    except Exception as e:
        return {"error": str(e), "topic": topic}


# Agent instantiation in the same file — side effect at import time
# This is exactly the kind of pattern that causes issues in the import pipeline.
research_agent = Agent(
    role="Research Specialist",
    goal="Find accurate and comprehensive information about any topic",
    backstory=(
        "You are a meticulous research specialist with years of experience "
        "gathering information from reliable sources. You always verify facts "
        "and provide well-sourced, objective summaries."
    ),
    tools=[get_wikipedia_summary],
    llm=ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.1,
    ),
    verbose=True,
    allow_delegation=False,
    max_iter=5,
)
