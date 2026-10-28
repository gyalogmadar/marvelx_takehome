"""Payout formulas.

A formula is a short arithmetic expression over named numeric facts, kept as
text so the UI and the README can show it exactly as the policy author wrote
it.  Evaluation walks a whitelisted AST: numbers, fact names, + - * /, unary
minus, and the functions min/max/round.  A missing fact yields None with a
note saying which fact was missing.
"""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_FUNCS = {"min": min, "max": max, "round": round}


class FormulaError(ValueError):
    pass


@dataclass(frozen=True)
class Formula:
    expr: str

    def __post_init__(self) -> None:
        tree = ast.parse(self.expr, mode="eval")
        self._validate(tree.body)

    @classmethod
    def _validate(cls, node: ast.AST) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return
        if isinstance(node, ast.Name):
            return
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            cls._validate(node.left)
            cls._validate(node.right)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            cls._validate(node.operand)
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            for arg in node.args:
                cls._validate(arg)
            return
        raise FormulaError(f"unsupported expression element in formula: {ast.dump(node)[:60]}")

    def names(self) -> list[str]:
        return sorted({n.id for n in ast.walk(ast.parse(self.expr, mode="eval")) if isinstance(n, ast.Name)} - set(_FUNCS))

    def evaluate(self, values: dict[str, float]) -> tuple[float | None, str | None]:
        missing = [n for n in self.names() if n not in values]
        if missing:
            return None, "cannot compute payout, missing: " + ", ".join(missing)
        try:
            result = self._eval(ast.parse(self.expr, mode="eval").body, values)
        except ZeroDivisionError:
            return None, "cannot compute payout: division by zero"
        return round(float(result), 2), None

    def _eval(self, node: ast.AST, values: dict[str, float]) -> float:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.BinOp):
            return _BIN[type(node.op)](self._eval(node.left, values), self._eval(node.right, values))
        if isinstance(node, ast.UnaryOp):
            return -self._eval(node.operand, values)
        if isinstance(node, ast.Call):
            return float(_FUNCS[node.func.id](*(self._eval(a, values) for a in node.args)))
        raise FormulaError("unreachable")


def formula(expr: str) -> Formula:
    return Formula(expr)
