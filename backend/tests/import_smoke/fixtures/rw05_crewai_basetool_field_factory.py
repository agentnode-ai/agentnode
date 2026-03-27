"""Real-world: CrewAI BaseTool with Field(default_factory) and self references.
Source: github.com/crewAIInc/crewAI-examples/issues/222
"""
from langchain_community.tools.gmail.create_draft import GmailCreateDraft
from langchain_community.agent_toolkits import GmailToolkit
from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from typing import Type


class CreateDraftSchema(BaseModel):
    """Input for CreateDraftTool."""
    email: str = Field(..., description="The recipient email address.")
    subject: str = Field(..., description="The subject of the email.")
    message: str = Field(..., description="The body of the email.")


class CreateDraftTool(BaseTool):
    """Tool that creates an email draft."""
    name: str = "create_draft"
    description: str = (
        "Useful to create an email draft."
        " The input should be a JSON with 'email', 'subject', and 'message' fields."
    )
    args_schema: Type[CreateDraftSchema] = CreateDraftSchema
    draft_creator: GmailCreateDraft = Field(
        default_factory=lambda: GmailCreateDraft(api_resource=GmailToolkit().api_resource)
    )

    def _run(self, query) -> str:
        """Execute the draft creation and return results."""
        try:
            result = self.draft_creator.run(query)
            return f"Draft created: {result}"
        except Exception as e:
            return f"Error creating draft: {str(e)}"
