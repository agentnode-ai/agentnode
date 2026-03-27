"""
Full crew.py file with @CrewBase, @agent, @task, @crew decorators.
No tool definitions — this is an orchestration file, not a tool file.
Common structure from crewAI project scaffolding (crewai create project).
"""

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from langchain_openai import ChatOpenAI


@CrewBase
class ResearchCrew:
    """Research crew that investigates a topic and produces a report."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self) -> None:
        self.llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0.2,
        )

    @agent
    def researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["researcher"],
            llm=self.llm,
            verbose=True,
            allow_delegation=False,
        )

    @agent
    def analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["analyst"],
            llm=self.llm,
            verbose=True,
            allow_delegation=True,
        )

    @agent
    def writer(self) -> Agent:
        return Agent(
            config=self.agents_config["writer"],
            llm=self.llm,
            verbose=True,
            allow_delegation=False,
        )

    @task
    def research_task(self) -> Task:
        return Task(
            config=self.tasks_config["research_task"],
            agent=self.researcher(),
        )

    @task
    def analysis_task(self) -> Task:
        return Task(
            config=self.tasks_config["analysis_task"],
            agent=self.analyst(),
            context=[self.research_task()],
        )

    @task
    def writing_task(self) -> Task:
        return Task(
            config=self.tasks_config["writing_task"],
            agent=self.writer(),
            context=[self.analysis_task()],
            output_file="report.md",
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
            memory=True,
        )


if __name__ == "__main__":
    topic = "the impact of AI on scientific research"
    result = ResearchCrew().crew().kickoff(inputs={"topic": topic})
    print(result)
