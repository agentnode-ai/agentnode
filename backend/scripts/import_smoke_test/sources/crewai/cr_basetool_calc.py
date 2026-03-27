"""
Calculator tool closely mirroring the real CalculatorTool from crewAI-examples.
Uses BaseTool with `_run(self, operation: str) -> float`.
Safe evaluation via ast + operator modules (no eval).

Source reference: crewAI-examples/trip_planner/tools/calculator_tools.py
"""

import ast
import operator as op
from typing import Type

from crewai_tools import BaseTool
from pydantic import BaseModel, Field


# operators allowed in safe eval
ALLOWED_OPERATORS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.BitXor: op.xor,
    ast.USub: op.neg,
    ast.Mod: op.mod,
    ast.FloorDiv: op.floordiv,
}


def _safe_eval(node: ast.AST) -> float:
    """Recursively evaluate an AST node using only allowed operators."""
    if isinstance(node, ast.Constant):
        return node.n  # type: ignore[attr-defined]
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in ALLOWED_OPERATORS:
            raise ValueError(f"Operator not allowed: {op_type.__name__}")
        return ALLOWED_OPERATORS[op_type](_safe_eval(node.left), _safe_eval(node.right))
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in ALLOWED_OPERATORS:
            raise ValueError(f"Unary operator not allowed: {op_type.__name__}")
        return ALLOWED_OPERATORS[op_type](_safe_eval(node.operand))
    else:
        raise TypeError(f"Unsupported node type: {type(node).__name__}")


class CalculatorToolInput(BaseModel):
    operation: str = Field(
        ...,
        description=(
            "The mathematical expression to evaluate. "
            "Supports +, -, *, /, **, //, %. "
            "Example: '(2 + 3) * 4 / 2'"
        ),
    )


class CalculatorTool(BaseTool):
    name: str = "Calculator tool"
    description: str = (
        "Useful to perform any mathematical calculations, "
        "like sum, minus, multiplication, division, etc. "
        "The input to this tool should be a mathematical expression, "
        "a couple examples are `200*7` or `5000/2*10`"
    )
    args_schema: Type[BaseModel] = CalculatorToolInput

    def _run(self, operation: str) -> float:
        """
        Evaluate a mathematical expression safely using AST parsing.

        Args:
            operation: A string math expression such as '10 * 3 + 5'

        Returns:
            float result of the expression
        """
        try:
            tree = ast.parse(operation.strip(), mode="eval")
            result = _safe_eval(tree.body)
            return float(result)
        except ZeroDivisionError:
            raise ValueError("Division by zero in expression")
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid expression '{operation}': {e}")
        except Exception as e:
            raise ValueError(f"Could not evaluate '{operation}': {e}")
