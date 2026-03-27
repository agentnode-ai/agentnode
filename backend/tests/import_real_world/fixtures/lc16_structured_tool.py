from langchain.tools import StructuredTool


def _calculate(expression: str) -> dict:
    """Evaluate a math expression safely."""
    allowed = set("0123456789+-*/.() ")
    if not all(c in allowed for c in expression):
        return {"error": "Invalid characters", "result": None}
    try:
        result = eval(expression)  # noqa: S307
        return {"result": result}
    except Exception as e:
        return {"error": str(e), "result": None}


calculator = StructuredTool.from_function(
    func=_calculate,
    name="calculator",
    description="Evaluate mathematical expressions",
)
