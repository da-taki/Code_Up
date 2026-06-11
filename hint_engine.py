"""
Confidence-based staged hints for CodeUp.

Pure / Flask-free. Given the current code, the last error, and (optionally) the
active tutorial module, produce a hint at one of three levels:
  * small  — a gentle nudge,
  * bigger — a more specific pointer,
  * answer — the likely concrete fix.

Detection is deterministic (driven mostly by the real error text, with light AST
cues). When there is no active problem it asks the learner what they are solving
rather than guessing. Key 2 may later polish wording, grounded in these facts.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, Optional

LEVELS = ("small", "bigger", "answer")

NO_PROBLEM = ("Tell me what you are trying to solve, or run your code first so I can see the issue.")


def _normalize_level(level: str) -> str:
    level = (level or "small").lower()
    if level in ("answer", "solution", "fix", "show"):
        return "answer"
    if level in ("bigger", "big", "more", "another"):
        return "bigger"
    return "small"


def _detect_problem(code: str, error: str) -> Dict[str, Any]:
    """Return {type, name?} for the active problem, or {} if none."""
    code = code or ""
    err = error or ""

    # Error-driven detection first: a real error means there is a problem to
    # hint about, regardless of whether the editor code was sent along.
    if "IndentationError" in err or "unexpected indent" in err or "expected an indented block" in err:
        return {"type": "indentation"}
    name = re.search(r"NameError: name '([^']+)' is not defined", err)
    if name:
        return {"type": "nameerror", "name": name.group(1)}
    if "SyntaxError" in err:
        if re.search(r"expected ':'", err) or _missing_colon_line(code):
            return {"type": "missing_colon"}
        return {"type": "syntaxerror"}
    if "TypeError" in err:
        return {"type": "typeerror"}
    if "ZeroDivisionError" in err:
        return {"type": "zerodivision"}

    # No (recognised) error: look at the code itself.
    if not code.strip():
        return {"type": "empty"}
    if not _parses(code):
        if _missing_colon_line(code):
            return {"type": "missing_colon"}
        return {"type": "syntaxerror"}
    return {}


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def _missing_colon_line(code: str) -> bool:
    for line in (code or "").splitlines():
        s = line.strip()
        if re.match(r"^(if|elif|else|for|while|def|class|try|except|finally|with)\b", s) and not s.endswith(":") \
                and "#" not in s:
            return True
    return False


_HINTS = {
    "indentation": {
        "small": "Check which line should belong inside the loop or block above it.",
        "bigger": "The line under your loop or if needs to be indented so Python knows it is inside that block.",
        "answer": "Indent that line by four spaces so it sits inside the block above.",
    },
    "missing_colon": {
        "small": "Look closely at the end of the lines that open a block.",
        "bigger": "A line that starts a block (like for, if, while, or def) is missing its colon.",
        "answer": "Add a colon at the end of that block-opening line.",
    },
    "syntaxerror": {
        "small": "There is a small typo somewhere; read the line the error points to.",
        "bigger": "Python could not parse one line — check for a missing colon, bracket, or quote.",
        "answer": "Fix the punctuation on the reported line: close the bracket or quote, or add the colon.",
    },
    "typeerror": {
        "small": "Two values of different kinds are being combined.",
        "bigger": "You may be mixing a number and text; check the types in that operation.",
        "answer": "Convert one value, for example use str(number) or int(text), so the types match.",
    },
    "zerodivision": {
        "small": "Something is being divided by zero.",
        "bigger": "Check the value you divide by — it becomes zero at some point.",
        "answer": "Guard the division, for example only divide when the divisor is not zero.",
    },
}


def build_hint(context: Optional[Dict[str, Any]], level: str = "small") -> Dict[str, Any]:
    """Return {hint, level, has_problem, problem_type}."""
    context = context or {}
    code = str(context.get("code") or "")
    error = str(context.get("error") or "")
    module = str(context.get("tutorial_module") or "")
    level = _normalize_level(level)

    problem = _detect_problem(code, error)
    ptype = problem.get("type", "")

    if ptype == "empty" or not ptype:
        # Tutorial gives a gentle module-specific nudge even without an error.
        if module:
            msg = f"For the {module} step, build the line the tutorial asked for, then run it so I can check it."
            return {"hint": msg, "level": level, "has_problem": False, "problem_type": "tutorial"}
        return {"hint": NO_PROBLEM, "level": level, "has_problem": False, "problem_type": ""}

    if ptype == "nameerror":
        name = problem.get("name", "a value")
        staged = {
            "small": "One of your names is used before it is given a value.",
            "bigger": f"The name {name} is used but never defined.",
            "answer": f"Define {name} before you use it, for example {name} = 0.",
        }
        return {"hint": staged[level], "level": level, "has_problem": True, "problem_type": "nameerror", "name": name}

    table = _HINTS.get(ptype)
    if table:
        return {"hint": table[level], "level": level, "has_problem": True, "problem_type": ptype}

    # Known-but-untabled problem: generic staged guidance.
    staged = {
        "small": "Read the error message; it names the line and the kind of problem.",
        "bigger": "Focus on the line the error points to and what value it expects.",
        "answer": "Adjust that line so it matches what the error asks for.",
    }
    return {"hint": staged[level], "level": level, "has_problem": True, "problem_type": ptype or "unknown"}
