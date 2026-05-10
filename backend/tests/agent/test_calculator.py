"""100%-coverage test suite for backend.agent.calculator.

Every operation, every validation rule, every AST branch, every error path.
Run with:
    pytest backend/tests/agent/test_calculator.py --cov=backend.agent.calculator --cov-report=term-missing
"""

from __future__ import annotations

import math

import pytest

from backend.agent.calculator import (
    CalculatorOp,
    CalculatorRequest,
    CalculatorResult,
    _cagr,
    _eval_ast,
    _margin,
    _percent_of,
    _ratio,
    _yoy_growth,
    calculate,
    safe_eval_expression,
)


# ===========================================================================
# Validation
# ===========================================================================


class TestRequestValidation:
    def test_expression_requires_expression_field(self):
        with pytest.raises(ValueError, match="EXPRESSION operation requires"):
            CalculatorRequest(operation=CalculatorOp.EXPRESSION)

    def test_expression_with_empty_string_invalid(self):
        with pytest.raises(ValueError):
            CalculatorRequest(operation=CalculatorOp.EXPRESSION, expression="")

    def test_cagr_requires_two_operands(self):
        with pytest.raises(ValueError, match="CAGR requires operands"):
            CalculatorRequest(operation=CalculatorOp.CAGR, operands=[100], years=3)

    def test_cagr_requires_positive_years(self):
        with pytest.raises(ValueError, match="years"):
            CalculatorRequest(operation=CalculatorOp.CAGR, operands=[100, 200], years=0)

    def test_cagr_requires_years_field(self):
        with pytest.raises(ValueError, match="years"):
            CalculatorRequest(operation=CalculatorOp.CAGR, operands=[100, 200])

    def test_binary_op_requires_two_operands(self):
        with pytest.raises(ValueError, match="requires exactly 2 operands"):
            CalculatorRequest(operation=CalculatorOp.ADD, operands=[1])

    def test_binary_op_rejects_three_operands(self):
        with pytest.raises(ValueError, match="requires exactly 2 operands"):
            CalculatorRequest(operation=CalculatorOp.MULTIPLY, operands=[1, 2, 3])

    def test_aggregate_requires_at_least_one_operand(self):
        with pytest.raises(ValueError, match="requires at least 1 operand"):
            CalculatorRequest(operation=CalculatorOp.SUM, operands=[])


# ===========================================================================
# Named operations
# ===========================================================================


class TestBasicArithmetic:
    def test_add(self):
        r = calculate(CalculatorRequest(operation=CalculatorOp.ADD, operands=[60.9, 36.0]))
        assert r.result == 96.9
        assert "60.9 + 36.0" in r.formula
        assert r.operation == CalculatorOp.ADD

    def test_subtract(self):
        r = calculate(CalculatorRequest(operation=CalculatorOp.SUBTRACT, operands=[100, 25]))
        assert r.result == 75
        assert "100" in r.formula and "25" in r.formula

    def test_multiply(self):
        r = calculate(CalculatorRequest(operation=CalculatorOp.MULTIPLY, operands=[3, 4]))
        assert r.result == 12

    def test_divide(self):
        r = calculate(CalculatorRequest(operation=CalculatorOp.DIVIDE, operands=[10, 4]))
        assert r.result == 2.5

    def test_divide_by_zero(self):
        with pytest.raises(ZeroDivisionError, match="division by zero"):
            calculate(CalculatorRequest(operation=CalculatorOp.DIVIDE, operands=[1, 0]))


class TestFinanceOps:
    def test_yoy_growth_positive(self):
        # Apple FY2023 vs FY2022 hypothetical: 96.9 vs 60.9
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.YOY_GROWTH, operands=[96.9, 60.9])
        )
        assert abs(r.result - 59.1133) < 0.01
        assert "%" in r.formula

    def test_yoy_growth_negative(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.YOY_GROWTH, operands=[40, 100])
        )
        assert r.result == -60.0

    def test_yoy_growth_zero_previous(self):
        with pytest.raises(ZeroDivisionError):
            calculate(
                CalculatorRequest(operation=CalculatorOp.YOY_GROWTH, operands=[100, 0])
            )

    def test_percent_of(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.PERCENT_OF, operands=[25, 100])
        )
        assert r.result == 25.0

    def test_percent_of_zero_whole(self):
        with pytest.raises(ZeroDivisionError):
            calculate(
                CalculatorRequest(operation=CalculatorOp.PERCENT_OF, operands=[10, 0])
            )

    def test_ratio(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.RATIO, operands=[100, 25])
        )
        assert r.result == 4.0

    def test_ratio_zero_denom(self):
        with pytest.raises(ZeroDivisionError):
            calculate(
                CalculatorRequest(operation=CalculatorOp.RATIO, operands=[100, 0])
            )

    def test_margin(self):
        # Gross margin: 50 / 200 = 25%
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.MARGIN, operands=[50, 200])
        )
        assert r.result == 25.0

    def test_margin_zero_denom(self):
        with pytest.raises(ZeroDivisionError):
            calculate(
                CalculatorRequest(operation=CalculatorOp.MARGIN, operands=[50, 0])
            )

    def test_cagr_2_year(self):
        # 100 -> 144 over 2 years = 20% CAGR
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.CAGR, operands=[100, 144], years=2)
        )
        assert abs(r.result - 20.0) < 0.0001

    def test_cagr_4_year(self):
        # 100 -> 200 over 4 years
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.CAGR, operands=[100, 200], years=4)
        )
        assert abs(r.result - 18.9207) < 0.001

    def test_cagr_negative_start(self):
        with pytest.raises(ValueError, match="start"):
            calculate(
                CalculatorRequest(operation=CalculatorOp.CAGR, operands=[-100, 200], years=3)
            )

    def test_cagr_zero_start(self):
        with pytest.raises(ValueError, match="start"):
            calculate(
                CalculatorRequest(operation=CalculatorOp.CAGR, operands=[0, 200], years=3)
            )

    def test_cagr_negative_end(self):
        with pytest.raises(ValueError, match="end"):
            calculate(
                CalculatorRequest(operation=CalculatorOp.CAGR, operands=[100, -200], years=3)
            )

    def test_cagr_zero_end(self):
        with pytest.raises(ValueError, match="end"):
            calculate(
                CalculatorRequest(operation=CalculatorOp.CAGR, operands=[100, 0], years=3)
            )

    def test_cagr_internal_helper_zero_years(self):
        """Direct call to _cagr, exercises the years <= 0 branch."""
        with pytest.raises(ValueError, match="years"):
            _cagr(100, 200, 0)

    def test_yoy_helper(self):
        assert _yoy_growth(100, 50) == 100.0

    def test_percent_of_helper(self):
        assert _percent_of(50, 100) == 50.0

    def test_ratio_helper(self):
        assert _ratio(10, 4) == 2.5

    def test_margin_helper(self):
        assert _margin(20, 80) == 25.0


class TestAggregations:
    def test_sum_single(self):
        r = calculate(CalculatorRequest(operation=CalculatorOp.SUM, operands=[5]))
        assert r.result == 5

    def test_sum_multi(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.SUM, operands=[1, 2, 3, 4])
        )
        assert r.result == 10

    def test_mean(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.MEAN, operands=[1, 2, 3, 4])
        )
        assert r.result == 2.5

    def test_median_odd(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.MEDIAN, operands=[1, 5, 3])
        )
        assert r.result == 3

    def test_median_even(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.MEDIAN, operands=[1, 2, 3, 4])
        )
        assert r.result == 2.5

    def test_min(self):
        r = calculate(CalculatorRequest(operation=CalculatorOp.MIN, operands=[5, 3, 9, 1, 7]))
        assert r.result == 1

    def test_max(self):
        r = calculate(CalculatorRequest(operation=CalculatorOp.MAX, operands=[5, 3, 9, 1, 7]))
        assert r.result == 9


# ===========================================================================
# Safe-eval expressions, every AST branch must be exercised
# ===========================================================================


class TestSafeEvalValid:
    def test_simple_add(self):
        assert safe_eval_expression("1 + 2") == 3.0

    def test_simple_subtract(self):
        assert safe_eval_expression("10 - 4") == 6.0

    def test_simple_multiply(self):
        assert safe_eval_expression("3 * 4") == 12.0

    def test_simple_divide(self):
        assert safe_eval_expression("10 / 4") == 2.5

    def test_modulo(self):
        assert safe_eval_expression("10 % 3") == 1.0

    def test_power(self):
        assert safe_eval_expression("2 ** 8") == 256.0

    def test_floor_divide(self):
        assert safe_eval_expression("10 // 3") == 3.0

    def test_unary_minus(self):
        assert safe_eval_expression("-5") == -5.0

    def test_unary_plus(self):
        assert safe_eval_expression("+5") == 5.0

    def test_nested_parens(self):
        assert safe_eval_expression("(1 + 2) * (3 + 4)") == 21.0

    def test_finance_yoy_formula(self):
        # YoY growth Apple-style hypothetical
        result = safe_eval_expression("(96.9 - 60.9) / 60.9 * 100")
        assert abs(result - 59.1133) < 0.01

    def test_float_literal(self):
        assert safe_eval_expression("3.14") == 3.14

    def test_dot_decimal(self):
        assert safe_eval_expression(".25 + .75") == 1.0

    def test_int_literal(self):
        assert safe_eval_expression("42") == 42.0

    def test_calculate_via_expression(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.EXPRESSION, expression="2 + 3 * 4")
        )
        assert r.result == 14
        assert "= 14" in r.formula


class TestSafeEvalInvalid:
    def test_empty_expression_via_safe_eval(self):
        with pytest.raises(ValueError):
            safe_eval_expression("   ")

    def test_non_string_input(self):
        # Bypassing the Pydantic layer, direct call
        with pytest.raises(ValueError):
            safe_eval_expression(123)  # type: ignore[arg-type]

    def test_syntax_error(self):
        with pytest.raises(ValueError, match="invalid expression syntax"):
            safe_eval_expression("1 +")

    def test_disallowed_name(self):
        with pytest.raises(ValueError, match="Disallowed AST node"):
            safe_eval_expression("x + 1")

    def test_disallowed_function_call(self):
        with pytest.raises(ValueError, match="Disallowed AST node"):
            safe_eval_expression("abs(-5)")

    def test_disallowed_attribute(self):
        with pytest.raises(ValueError, match="Disallowed AST node"):
            safe_eval_expression("math.pi")

    def test_disallowed_subscript(self):
        with pytest.raises(ValueError, match="Disallowed AST node"):
            safe_eval_expression("[1,2,3][0]")

    def test_disallowed_compare(self):
        with pytest.raises(ValueError, match="Disallowed AST node"):
            safe_eval_expression("1 < 2")

    def test_disallowed_boolean_op(self):
        with pytest.raises(ValueError, match="Disallowed AST node"):
            safe_eval_expression("True and False")

    def test_disallowed_string_constant(self):
        with pytest.raises(ValueError, match="Disallowed constant type"):
            safe_eval_expression("'hello'")

    def test_disallowed_bool_constant(self):
        with pytest.raises(ValueError, match="Disallowed constant type"):
            safe_eval_expression("True")

    def test_division_by_zero(self):
        with pytest.raises(ZeroDivisionError):
            safe_eval_expression("1 / 0")

    def test_modulo_by_zero(self):
        with pytest.raises(ZeroDivisionError):
            safe_eval_expression("5 % 0")

    def test_floor_div_by_zero(self):
        with pytest.raises(ZeroDivisionError):
            safe_eval_expression("5 // 0")


# ===========================================================================
# Direct AST node tests, ensures the recursive evaluator covers every
# allowed branch (even ones that named ops don't naturally exercise)
# ===========================================================================


class TestEvalAstDirect:
    def test_module_node_disallowed(self):
        """A module-level AST shouldn't be evaluated (only Expression mode)."""
        import ast
        with pytest.raises(ValueError, match="Disallowed AST node"):
            _eval_ast(ast.parse("1 + 2", mode="exec"))

    def test_disallowed_unary_op_not(self):
        import ast
        # Build an AST with a Not operator
        node = ast.parse("not 1", mode="eval")
        with pytest.raises(ValueError):
            _eval_ast(node)

    def test_disallowed_bin_op_lshift(self):
        import ast
        node = ast.parse("1 << 2", mode="eval")
        with pytest.raises(ValueError, match="Disallowed binary op"):
            _eval_ast(node)

    def test_floor_div_works(self):
        import ast
        node = ast.parse("7 // 2", mode="eval")
        assert _eval_ast(node) == 3

    def test_pow_works(self):
        import ast
        node = ast.parse("3 ** 4", mode="eval")
        assert _eval_ast(node) == 81

    def test_modulo_works(self):
        import ast
        node = ast.parse("7 % 3", mode="eval")
        assert _eval_ast(node) == 1


# ===========================================================================
# Result type
# ===========================================================================


class TestResultShape:
    def test_result_echoes_operands(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.ADD, operands=[1, 2])
        )
        assert r.operands == [1.0, 2.0]

    def test_result_is_calculator_result_type(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.ADD, operands=[1, 2])
        )
        assert isinstance(r, CalculatorResult)

    def test_result_serialises_to_json(self):
        r = calculate(
            CalculatorRequest(operation=CalculatorOp.YOY_GROWTH, operands=[150, 100])
        )
        roundtrip = CalculatorResult.model_validate_json(r.model_dump_json())
        assert roundtrip == r


# ===========================================================================
# Defensive: confirm calculate raises on an enum value the dispatch hasn't
# handled (paranoia, useful if a new op is added without a dispatch branch).
# ===========================================================================


def test_unhandled_op_raises(monkeypatch):
    """Hypothetical: an enum extended without a dispatch branch in calculate()."""
    # Add a fake operation by monkey-patching the enum (not normally possible
    # but we simulate it via direct attribute injection on the enum class).
    # We rely on the fallthrough else branch in calculate() being defensive.
    class FakeReq:
        operation = "unknown_op"
        operands: list = [1, 2]
        expression = None
        years = None

    with pytest.raises((ValueError, AttributeError)):
        calculate(FakeReq)  # type: ignore[arg-type]
