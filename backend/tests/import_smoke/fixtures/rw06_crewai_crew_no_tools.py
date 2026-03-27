"""Real-world: CrewAI crew file with Agents/Tasks/Crew — NO tool definitions.
Source: github.com/crewAIInc/crewAI-examples surprise_travel/crew.py
"""
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import SerperDevTool, ScrapeWebsiteTool
from pydantic import BaseModel, Field
from typing import List, Optional


class Activity(BaseModel):
    name: str = Field(..., description="Name of the activity")
    location: str = Field(..., description="Location of the activity")
    description: str = Field(..., description="Description of the activity")


class Itinerary(BaseModel):
    name: str = Field(..., description="Name of the itinerary")
    activities: List[Activity] = Field(..., description="List of activities")
    hotel: str = Field(..., description="Hotel information")


@CrewBase
class SurpriseTravelCrew:
    """SurpriseTravel crew"""
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def personalized_activity_planner(self) -> Agent:
        return Agent(
            config=self.agents_config["personalized_activity_planner"],
            tools=[SerperDevTool(), ScrapeWebsiteTool()],
            verbose=True,
        )

    @task
    def personalized_activity_planning_task(self) -> Task:
        return Task(
            config=self.tasks_config["personalized_activity_planning_task"],
            agent=self.personalized_activity_planner(),
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=2,
        )
