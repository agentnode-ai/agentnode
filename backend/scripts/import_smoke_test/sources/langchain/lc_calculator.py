"""
Calculator tool using eval-based math evaluation.
Very common in beginner LangChain tutorials — eval is a known risk but people use it anyway.
"""

from langchain.tools import tool


@tool
def calculate(expression: str) -> dict:
    """
    Evaluate a mathematical expression and return the result.

    Supports basic arithmetic: +, -, *, /, **, //, %
    Examples: "2 + 2", "10 * 3.5", "2 ** 8"

    Args:
        expression: A string containing a math expression.

    Returns:
        dict with result or error message.
    """
    # people know this is unsafe but use it anyway in personal projects
    allowed_chars = set("0123456789+-*/.() ")
    cleaned = expression.strip()

    for ch in cleaned:
        if ch not in allowed_chars:
            return {"error": f"Invalid character in expression: {ch!r}", "result": None}

    try:
        result = eval(cleaned)  # noqa: S307
        return {
            "expression": expression,
            "result": result,
            "result_type": type(result).__name__,
            "error": None,
        }
    except ZeroDivisionError:
        return {"error": "Division by zero", "expression": expression, "result": None}
    except Exception as e:
        return {"error": str(e), "expression": expression, "result": None}
