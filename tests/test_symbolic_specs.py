import contextlib
import io

import pytest

from symbolic_specs import (
    build_exact_symbol_generation,
    is_exact_symbol_task,
    normalize_spoken_symbols,
    parse_spoken_number,
)


def _run_generated(code):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(code, {}, {})
    return output.getvalue()


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("star", "*"),
        ("asterisk", "*"),
        ("hash", "#"),
        ("pound", "#"),
        ("plus minus slash percent", "+ - / %"),
        ("open bracket close bracket", "[ ]"),
        ("open parenthesis close parenthesis", "( )"),
        ("open brace close brace", "{ }"),
        ("double quote single quote", "\" '"),
    ],
)
def test_symbol_normalization(spoken, expected):
    assert normalize_spoken_symbols(spoken) == expected


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("zero", 0),
        ("five", 5),
        ("twenty", 20),
        ("first", 1),
        ("third", 3),
        ("fifth", 5),
        ("paanch", 5),
    ],
)
def test_number_and_ordinal_normalization(spoken, expected):
    assert parse_spoken_number(spoken) == expected


@pytest.mark.parametrize(
    "prompt",
    [
        "make a 5 by 5 star pattern",
        "make a square of 5 rows and 5 columns",
        "third line should have six asterisks",
        "print brackets around hello",
    ],
)
def test_exact_spec_detection_positive(prompt):
    assert is_exact_symbol_task(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "run",
        "map my code",
        "open main",
        "walk me through this program",
        "remember this as my pattern",
        "use macro my pattern",
    ],
)
def test_exact_spec_detection_ignores_normal_commands(prompt):
    assert not is_exact_symbol_task(prompt)


@pytest.mark.parametrize(
    "prompt,expected_code",
    [
        ("print five stars", 'print("*" * 5)\n'),
        ("print three hashes", 'print("#" * 3)\n'),
        ("make a 5 by 5 star pattern", 'for row in range(5):\n    print("*" * 5)\n'),
        ("make a 4 by 6 hash pattern", 'for row in range(4):\n    print("#" * 6)\n'),
        (
            "make a 5 by 5 pattern where the third row has 6 stars",
            'for row in range(5):\n    if row == 2:\n        print("*" * 6)\n    else:\n        print("*" * 5)\n',
        ),
        (
            "make a triangle pattern with five rows using stars",
            'for row in range(1, 6):\n    print("*" * row)\n',
        ),
    ],
)
def test_deterministic_pattern_generation(prompt, expected_code):
    result = build_exact_symbol_generation(prompt)
    assert result["success"] is True
    assert result["source"] == "deterministic_exact"
    assert result["code"] == expected_code


def test_generated_square_pattern_runtime_output():
    code = build_exact_symbol_generation("make a 5 by 5 star pattern")["code"]
    assert _run_generated(code) == "*****\n*****\n*****\n*****\n*****\n"


def test_generated_row_exception_runtime_output():
    code = build_exact_symbol_generation("make a 5x5 star pattern but line 3 should have six stars")["code"]
    assert _run_generated(code).splitlines() == ["*****", "*****", "******", "*****", "*****"]


def test_generated_triangle_runtime_output():
    code = build_exact_symbol_generation("make increasing star pattern with 5 rows")["code"]
    assert _run_generated(code).splitlines() == ["*", "**", "***", "****", "*****"]


def test_generated_hash_pattern_uses_hashes_not_stars_or_x():
    code = build_exact_symbol_generation("make a 4 by 6 hash pattern")["code"]
    output = _run_generated(code)
    assert output == "######\n######\n######\n######\n"
    assert "*" not in output
    assert "x" not in output.lower()


def test_ambiguous_exact_task_asks_for_clarification():
    result = build_exact_symbol_generation("make a pattern with symbols")
    assert result["success"] is False
    assert result["clarification"] is True
    assert "could not identify the symbol or the exact counts" in result["message"]
