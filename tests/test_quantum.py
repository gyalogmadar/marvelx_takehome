import pytest

from claims_pipeline.components import formula
from claims_pipeline.components.quantum import FormulaError


def test_formula_evaluates_and_rounds():
    f = formula("0.5 * booking_value / passengers * affected")
    assert f.evaluate({"booking_value": 800, "passengers": 4, "affected": 2}) == (200.0, None)
    assert f.names() == ["affected", "booking_value", "passengers"]


def test_formula_reports_missing_inputs():
    value, note = formula("100 * affected").evaluate({})
    assert value is None and "affected" in note


def test_formula_functions_and_division_by_zero():
    assert formula("max(0, min(item_value, 1000) - 50)").evaluate({"item_value": 1200}) == (950.0, None)
    assert formula("a / b").evaluate({"a": 1, "b": 0})[0] is None


@pytest.mark.parametrize("bad", ["__import__('os')", "a ** 2", "a if b else c", "f(x)", "a.b", "[1,2]"])
def test_formula_rejects_anything_outside_the_whitelist(bad):
    with pytest.raises((FormulaError, SyntaxError)):
        formula(bad)
