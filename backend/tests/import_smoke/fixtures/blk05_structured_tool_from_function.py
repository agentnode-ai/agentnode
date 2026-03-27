from langchain.tools import StructuredTool
from pydantic import BaseModel, Field


class CalculatorInput(BaseModel):
    expression: str = Field(description="Math expression to evaluate")


def evaluate_expression(expression: str) -> dict:
    """Evaluate a mathematical expression safely."""
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return {"result": result, "expression": expression}
    except Exception as e:
        return {"error": str(e), "expression": expression}


calculator = StructuredTool.from_function(
    func=evaluate_expression,
    name="calculator",
    description="Evaluate math expressions",
    args_schema=CalculatorInput,
)
