"""CalculatorTool, deterministic arithmetic for the agent.

Two modes
---------
1. **Named operations** (preferred path). The agent specifies a structured
   operation (`yoy_growth`, `cagr`, `ratio`, `mean`, etc.) and operands. The
   wrapper performs the math via plain Python and emits a human-readable
   formula for citations.

2. **Safe-eval expressions** (escape hatch). The agent supplies a string
   like `"(96.9 - 60.9) / 60.9 * 100"`. We parse it via `ast.parse(mode='eval')`
   and walk the AST, allowing only numeric literals and arithmetic ops:
   {Add, Sub, Mult, Div, Mod, Pow, FloorDiv, UAdd, USub}. **Never calls
   `eval()`**. values are computed by recursive AST descent.

Required: 100% test coverage. This is the integrity backbone for any number
the agent reports, hallucinated arithmetic in finance = project failure.
"""

from __future__ import annotations

import ast
import statistics
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Public schemas
# ---------------------------------------------------------------------------


class CalculatorOp(str, Enum):
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    YOY_GROWTH = "yoy_growth"     # (current - previous) / previous * 100
    PERCENT_OF = "percent_of"     # part / whole * 100
    RATIO = "ratio"               # numerator / denominator
    MARGIN = "margin"             # numerator / denominator * 100
    CAGR = "cagr"                 # (end/start)^(1/years) - 1, returned as %
    SUM = "sum"
    MEAN = "mean"
    MEDIAN = "median"
    MIN = "min"
    MAX = "max"
    EXPRESSION = "expression"     # safe_eval


class CalculatorRequest(BaseModel):
    """Input to CalculatorTool.

    Required fields per operation:
      ADD/SUBTRACT/MULTIPLY/DIVIDE       -> operands: list of 2 floats
      YOY_GROWTH/PERCENT_OF/RATIO/MARGIN -> operands: list of 2 floats [current/part, previous/whole]
      CAGR                                -> operands: [start, end] + years: int
      SUM/MEAN/MEDIAN/MIN/MAX             -> operands: list of any length
      EXPRESSION                          -> expression: string
    """

    operation: CalculatorOp
    operands: list[float] = Field(default_factory=list)
    expression: str | None = None
    years: int | None = None  # for CAGR

    @model_validator(mode="after")
    def _validate_shape(self) -> "CalculatorRequest":
        op = self.operation
        n = len(self.operands)

        if op == CalculatorOp.EXPRESSION:
            if not self.expression:
                raise ValueError("EXPRESSION operation requires `expression`")
            return self

        if op == CalculatorOp.CAGR:
            if n != 2:
                raise ValueError("CAGR requires operands=[start, end]")
            if self.years is None or self.years <= 0:
                raise ValueError("CAGR requires years > 0")
            return self

        if op in {
            CalculatorOp.ADD, CalculatorOp.SUBTRACT, CalculatorOp.MULTIPLY,
            CalculatorOp.DIVIDE, CalculatorOp.YOY_GROWTH, CalculatorOp.PERCENT_OF,
            CalculatorOp.RATIO, CalculatorOp.MARGIN,
        }:
            if n != 2:
                raise ValueError(f"{op.value} requires exactly 2 operands")
            return self

        if op in {
            CalculatorOp.SUM, CalculatorOp.MEAN, CalculatorOp.MEDIAN,
            CalculatorOp.MIN, CalculatorOp.MAX,
        }:
            if n == 0:
                raise ValueError(f"{op.value} requires at least 1 operand")
            return self

        return self  # pragma: no cover (unreachable, all enum values handled)


class CalculatorResult(BaseModel):
    operation: CalculatorOp
    result: float
    formula: str            # human-readable rendering, e.g. "(96.9 - 60.9) / 60.9 * 100 = 59.11"
    operands: list[float]   # echoed back


# ---------------------------------------------------------------------------
# Safe AST evaluator, never calls eval()
# ---------------------------------------------------------------------------


_ALLOWED_BIN_OPS: dict[type, str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.Mod: "%",
    ast.Pow: "**",
    ast.FloorDiv: "//",
}

_ALLOWED_UNARY_OPS: dict[type, str] = {
    ast.UAdd: "+",
    ast.USub: "-",
}


def _eval_ast(node: ast.AST) -> float:
    """Recursively evaluate a whitelisted AST node, returning a float."""
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body)

    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise ValueError(f"Disallowed constant type: {type(node.value).__name__}")
        return float(node.value)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARY_OPS:
            raise ValueError(f"Disallowed unary op: {op_type.__name__}")
        operand = _eval_ast(node.operand)
        return +operand if op_type is ast.UAdd else -operand

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BIN_OPS:
            raise ValueError(f"Disallowed binary op: {op_type.__name__}")
        left = _eval_ast(node.left)
        right = _eval_ast(node.right)
        if op_type is ast.Add:
            return left + right
        if op_type is ast.Sub:
            return left - right
        if op_type is ast.Mult:
            return left * right
        if op_type is ast.Div:
            if right == 0:
                raise ZeroDivisionError("division by zero in calculator expression")
            return left / right
        if op_type is ast.Mod:
            if right == 0:
                raise ZeroDivisionError("modulo by zero in calculator expression")
            return left % right
        if op_type is ast.Pow:
            return left ** right
        if op_type is ast.FloorDiv:
            if right == 0:
                raise ZeroDivisionError("floor division by zero in calculator expression")
            return left // right
        # Unreachable: enumerated above
        raise ValueError(f"Disallowed binary op: {op_type.__name__}")  # pragma: no cover

    raise ValueError(f"Disallowed AST node: {type(node).__name__}")


def safe_eval_expression(expr: str) -> float:
    """Evaluate an arithmetic expression safely. Never calls Python's eval().

    Allowed: numeric literals, parens, +, -, *, /, %, **, //, unary +/-.
    Disallowed: names, calls, attributes, subscripts, comprehensions, anything else.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("expression must be a non-empty string")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"invalid expression syntax: {e.msg}") from e
    return float(_eval_ast(tree))


# ---------------------------------------------------------------------------
# Named operation handlers
# ---------------------------------------------------------------------------


def _yoy_growth(current: float, previous: float) -> float:
    if previous == 0:
        raise ZeroDivisionError("YoY growth undefined when previous value is zero")
    return (current - previous) / previous * 100.0


def _percent_of(part: float, whole: float) -> float:
    if whole == 0:
        raise ZeroDivisionError("percent_of undefined when whole is zero")
    return part / whole * 100.0


def _ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise ZeroDivisionError("ratio undefined when denominator is zero")
    return numerator / denominator


def _margin(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise ZeroDivisionError("margin undefined when denominator is zero")
    return numerator / denominator * 100.0


def _cagr(start: float, end: float, years: int) -> float:
    if start <= 0:
        raise ValueError("CAGR requires start > 0")
    if end <= 0:
        raise ValueError("CAGR requires end > 0")
    if years <= 0:
        raise ValueError("CAGR requires years > 0")
    return ((end / start) ** (1.0 / years) - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def calculate(request: CalculatorRequest) -> CalculatorResult:
    """Run a calculator request and return a typed result."""
    op = request.operation
    operands = list(request.operands)

    if op == CalculatorOp.ADD:
        a, b = operands
        result = a + b
        formula = f"{a} + {b} = {result}"

    elif op == CalculatorOp.SUBTRACT:
        a, b = operands
        result = a - b
        formula = f"{a} - {b} = {result}"

    elif op == CalculatorOp.MULTIPLY:
        a, b = operands
        result = a * b
        formula = f"{a} * {b} = {result}"

    elif op == CalculatorOp.DIVIDE:
        a, b = operands
        if b == 0:
            raise ZeroDivisionError("division by zero")
        result = a / b
        formula = f"{a} / {b} = {result}"

    elif op == CalculatorOp.YOY_GROWTH:
        current, previous = operands
        result = _yoy_growth(current, previous)
        formula = f"({current} - {previous}) / {previous} * 100 = {result:.4f}%"

    elif op == CalculatorOp.PERCENT_OF:
        part, whole = operands
        result = _percent_of(part, whole)
        formula = f"{part} / {whole} * 100 = {result:.4f}%"

    elif op == CalculatorOp.RATIO:
        num, den = operands
        result = _ratio(num, den)
        formula = f"{num} / {den} = {result:.6f}"

    elif op == CalculatorOp.MARGIN:
        num, den = operands
        result = _margin(num, den)
        formula = f"{num} / {den} * 100 = {result:.4f}%"

    elif op == CalculatorOp.CAGR:
        start, end = operands
        years = request.years  # validated by model_validator
        assert years is not None
        result = _cagr(start, end, years)
        formula = f"((({end} / {start}) ^ (1 / {years})) - 1) * 100 = {result:.4f}%"

    elif op == CalculatorOp.SUM:
        result = float(sum(operands))
        formula = f"sum({operands}) = {result}"

    elif op == CalculatorOp.MEAN:
        result = float(statistics.fmean(operands))
        formula = f"mean({operands}) = {result}"

    elif op == CalculatorOp.MEDIAN:
        result = float(statistics.median(operands))
        formula = f"median({operands}) = {result}"

    elif op == CalculatorOp.MIN:
        result = float(min(operands))
        formula = f"min({operands}) = {result}"

    elif op == CalculatorOp.MAX:
        result = float(max(operands))
        formula = f"max({operands}) = {result}"

    elif op == CalculatorOp.EXPRESSION:
        # validated as non-empty by model_validator
        assert request.expression is not None
        result = safe_eval_expression(request.expression)
        formula = f"{request.expression} = {result}"

    else:  # pragma: no cover, exhaustive enum coverage above
        raise ValueError(f"Unhandled operation: {op}")

    return CalculatorResult(
        operation=op,
        result=float(result),
        formula=formula,
        operands=operands,
    )
