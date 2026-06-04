"""
CodeUp guided tutorial engine.

Single source of truth for the spoken, activity-based beginner tutorial:

  * Ordered, opt-in lesson modules (print -> variables -> if -> for -> while),
    each with short *spoken* content (concept, example, task, hints, recap,
    success line). The frontend fetches this pack and narrates it through the
    proven audible speech path; it never invents lesson text.

  * Deterministic, AST-based validators for each module's hands-on activity.
    Validation is structural ("did the learner actually use a print / variable /
    if / for / while and run it?") rather than exact-string matching, so many
    different correct beginner answers are accepted.

  * A static safety check for the while-loop activity that flags obvious
    non-terminating attempts (``while True`` with no break, or a condition whose
    variables never change) *before* execution. The sandbox's wall-clock timeout
    remains the real backstop; this just lets the tutorial give a kind spoken
    hint instead of a scary timeout.

This module is pure (no Flask, no I/O) so it can be unit-tested directly and
imported by ``app.py`` for the ``/tutorial/*`` routes.
"""
from __future__ import annotations

import ast
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Lesson content
# ---------------------------------------------------------------------------
# Order is meaningful and defines progression. Progression is ALWAYS opt-in:
# the engine never assumes a learner who finished one module wants the next.
MODULE_ORDER: List[str] = ["print", "variables", "if", "for", "while"]

# Keep spoken text short and conversational. Long lectures are explicitly out of
# scope; each concept is immediately followed by a hands-on activity.
MODULES: Dict[str, Dict] = {
    "print": {
        "id": "print",
        "order": 1,
        "title": "Print statements",
        "concept": (
            "A print statement makes your program say or display a message when "
            "it runs. In CodeUp you create one by speaking an insert command, and "
            "I place the code for you."
        ),
        "example_code": 'print("Hello world")',
        "example_spoken": (
            "For example, to make Python greet us, you would say: insert print "
            "hello world."
        ),
        "task": (
            "I will tell you a command to say. When you say it, or type it into "
            "the command box, I will place the code and read it back. Then you "
            "run it by saying: run code."
        ),
        "hints": [
            "Say: insert print hello world.",
            "Start with the word insert, then print, then your message.",
            "You can also type the command into the command box and press Enter.",
        ],
        "success": "Nicely done. You just made Python speak using a print statement.",
        "recap": (
            "Quick recap. A print statement shows a message. You make one by "
            "saying: insert print, then your message."
        ),
    },
    "variables": {
        "id": "variables",
        "order": 2,
        "title": "Variables",
        "concept": (
            "A variable is a named box that stores information so your program "
            "can use it later. You give the box a name and put a value inside. "
            "We will make a box called name, then show what is inside it."
        ),
        "example_code": 'name = "Taknoor"\nprint(name)',
        "example_spoken": (
            "For example, to store your name you would say: insert a variable "
            "named name and give it the value Taknoor. Then to show it: insert "
            "print name."
        ),
        "task": (
            "We will build it in two steps: first create the variable, then "
            "print what is inside it. I will tell you each command to say."
        ),
        "hints": [
            "First say: insert a variable named name and give it the value Taknoor.",
            "Then say: insert print name.",
            "You may choose any name and any value; a word becomes text, a number "
            "stays a number.",
        ],
        "success": "Well done. You stored a value in a variable and printed it back.",
        "recap": (
            "Quick recap. A variable is a named box. Say insert a variable named, "
            "a name, and give it the value, then a value. Print it by saying "
            "insert print and the name."
        ),
    },
    "if": {
        "id": "if",
        "order": 3,
        "title": "If statements",
        "concept": (
            "An if statement lets a program make a decision. Python checks "
            "whether something is true, and only then runs the indented action "
            "underneath it. Indentation, four spaces, is how Python knows the "
            "action belongs to the if."
        ),
        "example_code": 'age = 12\nif age > 10:\n    print("you can vote")',
        "example_spoken": (
            "For example: insert a variable named age and give it the value 12. "
            "Then: insert an if statement checking age is greater than 10. Then: "
            "insert an indented print saying you can vote."
        ),
        "task": (
            "We will build it line by line: a variable to test, then the if "
            "decision, then an indented action. I will tell you each command."
        ),
        "hints": [
            "First the variable: insert a variable named age and give it the value 12.",
            "Then the decision: insert an if statement checking age is greater than 10.",
            "Then the action: insert an indented print saying you can vote. The "
            "word indented adds the four spaces for you.",
        ],
        "success": "Great work. Your if statement made a decision and printed when the condition was true.",
        "recap": (
            "Quick recap. An if statement checks a condition and ends with a "
            "colon. The line that runs when it is true is indented underneath."
        ),
    },
    "for": {
        "id": "for",
        "order": 4,
        "title": "For loops",
        "concept": (
            "A for loop repeats an action a known number of times. The repeated "
            "line is indented underneath the for line, which is how Python knows "
            "it is the action to repeat."
        ),
        "example_code": 'for i in range(3):\n    print(i)',
        "example_spoken": (
            "For example: insert for i in range 3. Then: insert an indented "
            "print i. That repeats three times, reading out 0, then 1, then 2."
        ),
        "task": (
            "We will build it in two steps: the loop line, then the indented "
            "action it repeats. I will tell you each command to say."
        ),
        "hints": [
            "First say: insert for i in range 3. CodeUp adds the brackets and the "
            "colon for you.",
            "Then say: insert an indented print i.",
            "The word indented adds the four spaces that put the line inside the loop.",
        ],
        "success": "Excellent. Your for loop repeated the action and printed each time.",
        "recap": (
            "Quick recap. A for loop with range repeats a set number of times. "
            "The repeated line is indented underneath."
        ),
    },
    "while": {
        "id": "while",
        "order": 5,
        "title": "While loops",
        "concept": (
            "A while loop repeats an action while a condition stays true. "
            "Because the condition could stay true forever, a while loop must "
            "change something each time so it can eventually stop."
        ),
        "example_code": "count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1",
        "example_spoken": (
            "For example: insert a variable named count and give it the value 1. "
            "Then: insert while count is less than or equal to 3. Then: insert "
            "an indented print count. Then: insert an indented count equals "
            "count plus 1. The counter grows until it passes 3, then the loop stops."
        ),
        "task": (
            "We will build a safe counter loop in four steps, ending with the "
            "line that lets it stop. I will tell you each command to say."
        ),
        "hints": [
            "Start the counter: insert a variable named count and give it the value 1.",
            "The loop: insert while count is less than or equal to 3.",
            "Most important, the update: insert an indented count equals count "
            "plus 1. Without it, the loop would run forever.",
        ],
        "success": "Brilliant. Your while loop counted up and stopped safely. That completes the last topic.",
        "recap": (
            "Quick recap. A while loop repeats while its condition is true. "
            "Always change the counter inside the loop so it eventually stops."
        ),
    },
}


# ---------------------------------------------------------------------------
# Module navigation helpers
# ---------------------------------------------------------------------------
def first_module_id() -> str:
    """The module the tutorial always starts on."""
    return MODULE_ORDER[0]


def get_module(module_id: str) -> Optional[Dict]:
    return MODULES.get(module_id)


def next_module_id(module_id: str) -> Optional[str]:
    """The id of the module after ``module_id``, or None if it is the last."""
    try:
        idx = MODULE_ORDER.index(module_id)
    except ValueError:
        return None
    if idx + 1 >= len(MODULE_ORDER):
        return None
    return MODULE_ORDER[idx + 1]


def module_pack() -> Dict:
    """Serializable lesson pack for the frontend (content + order)."""
    return {
        "order": list(MODULE_ORDER),
        "count": len(MODULE_ORDER),
        "modules": {mid: dict(MODULES[mid]) for mid in MODULE_ORDER},
    }


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------
def _safe_parse(code: str) -> Optional[ast.AST]:
    try:
        return ast.parse(code or "")
    except SyntaxError:
        return None


def _print_calls(node: ast.AST) -> List[ast.Call]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "print"
    ]


def _assigned_names(node: ast.AST) -> set:
    names: set = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
    return names


def _has_print_inside(node: ast.AST) -> bool:
    return len(_print_calls(node)) > 0


# ---------------------------------------------------------------------------
# Per-module structural validators
# Each returns True when the learner genuinely used the target construct.
# ---------------------------------------------------------------------------
def validate_print(code: str) -> bool:
    tree = _safe_parse(code)
    if tree is None:
        return False
    return any(len(call.args) >= 1 for call in _print_calls(tree))


def validate_variables(code: str) -> bool:
    tree = _safe_parse(code)
    if tree is None:
        return False
    assigned = _assigned_names(tree)
    if not assigned:
        return False
    # Accept any assignment that is later referenced inside a print(), so any
    # valid variable name / value works (not a hardcoded answer).
    for call in _print_calls(tree):
        for arg in call.args:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Name) and sub.id in assigned:
                    return True
    return False


def validate_if(code: str) -> bool:
    tree = _safe_parse(code)
    if tree is None:
        return False
    return any(
        isinstance(n, ast.If) and _has_print_inside(n) for n in ast.walk(tree)
    )


def validate_for(code: str) -> bool:
    tree = _safe_parse(code)
    if tree is None:
        return False
    return any(
        isinstance(n, ast.For) and _has_print_inside(n) for n in ast.walk(tree)
    )


def validate_while(code: str) -> bool:
    tree = _safe_parse(code)
    if tree is None:
        return False
    return any(
        isinstance(n, ast.While) and _has_print_inside(n) for n in ast.walk(tree)
    )


_VALIDATORS: Dict[str, Callable[[str], bool]] = {
    "print": validate_print,
    "variables": validate_variables,
    "if": validate_if,
    "for": validate_for,
    "while": validate_while,
}


# ---------------------------------------------------------------------------
# While-loop safety (static, best-effort; the sandbox timeout is the backstop)
# ---------------------------------------------------------------------------
def check_while_safety(code: str) -> Tuple[bool, str]:
    """Flag obvious non-terminating while loops.

    Returns ``(safe, reason)``. ``safe`` is True when no while loop looks
    obviously infinite. The reason is a kind, spoken-style hint when unsafe.
    Syntax errors are treated as "safe" here so the normal run path surfaces
    the syntax message instead.
    """
    tree = _safe_parse(code)
    if tree is None:
        return True, ""

    for node in ast.walk(tree):
        if not isinstance(node, ast.While):
            continue

        has_break = any(isinstance(n, ast.Break) for n in ast.walk(node))
        if has_break:
            # A break gives the learner an explicit exit; trust it.
            continue

        test = node.test
        # while True: / while 1: with no break -> runs forever.
        if isinstance(test, ast.Constant) and bool(test.value):
            return (
                False,
                "That while loop uses while True with no way out, so it would "
                "run forever. Use a counter and a condition, for example while "
                "count is less than 3, and change the counter inside the loop.",
            )

        # Condition references variables, but none of them change in the body.
        cond_names = {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
        if cond_names:
            modified: set = set()
            for n in ast.walk(node):
                if isinstance(n, ast.Assign):
                    for target in n.targets:
                        if isinstance(target, ast.Name):
                            modified.add(target.id)
                elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                    modified.add(n.target.id)
            if not (cond_names & modified):
                return (
                    False,
                    "Your while loop's condition never changes inside the loop, "
                    "so it would run forever. Make sure a counter changes each "
                    "time, for example count equals count plus 1.",
                )

    return True, ""


# ---------------------------------------------------------------------------
# Top-level attempt validation used by the /tutorial/validate route
# ---------------------------------------------------------------------------
def _miss_feedback(module_id: str) -> str:
    misses = {
        "print": "I do not see a finished print statement yet. A print needs the "
        "word print, brackets, and a message in quotes.",
        "variables": "I do not see a variable being printed yet. Set a value with "
        "equals, then print that variable by name.",
        "if": "I do not see a working if statement yet. Check a condition, end the "
        "if line with a colon, and indent a print underneath it.",
        "for": "I do not see a finished for loop yet. Use for with range, end the "
        "line with a colon, and indent a print underneath.",
        "while": "I do not see a finished while loop yet. You need a while line "
        "ending in a colon with an indented print inside.",
    }
    return misses.get(module_id, "That is not quite the target for this topic yet.")


def first_hint(module_id: str) -> str:
    module = MODULES.get(module_id) or {}
    hints = module.get("hints") or []
    return hints[0] if hints else "Try saying: give me an example."


def validate_attempt(
    module_id: str,
    code: str,
    ran_ok: bool = True,
    output: str = "",
) -> Dict:
    """Validate one activity attempt.

    Returns a dict with:
      passed (bool), safe (bool), feedback (str, spoken), hint (str|None).

    ``ran_ok`` is whether the learner's most recent run executed without an
    error (the frontend knows this); ``output`` is the captured program output.
    """
    code = code or ""
    module = MODULES.get(module_id)
    if module is None:
        return {
            "passed": False,
            "safe": True,
            "feedback": "That topic is not part of the tutorial.",
            "hint": None,
        }

    # Syntax problems: kind nudge, keep them in the activity.
    if _safe_parse(code) is None:
        return {
            "passed": False,
            "safe": True,
            "feedback": "There is a small typo in your code, so Python could not "
            "read it. Listen to the error, then fix and run again.",
            "hint": first_hint(module_id),
        }

    # While loop gets a safety pre-check so we can warn kindly before a timeout.
    if module_id == "while":
        safe, reason = check_while_safety(code)
        if not safe:
            return {
                "passed": False,
                "safe": False,
                "feedback": reason,
                "hint": MODULES["while"]["hints"][-1],
            }

    validator = _VALIDATORS.get(module_id)
    structural_ok = bool(validator(code)) if validator else False
    if not structural_ok:
        return {
            "passed": False,
            "safe": True,
            "feedback": _miss_feedback(module_id),
            "hint": first_hint(module_id),
        }

    if not ran_ok:
        return {
            "passed": False,
            "safe": True,
            "feedback": "Your code has the right shape, but it did not run "
            "cleanly. Listen to the error message, then run it again.",
            "hint": first_hint(module_id),
        }

    return {
        "passed": True,
        "safe": True,
        "feedback": module["success"],
        "hint": None,
    }
