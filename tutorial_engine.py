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
            "A print statement makes Python say something in the output. "
            "You write the word print, then round brackets, and inside the "
            "brackets you put your message wrapped in quotes."
        ),
        "example_code": 'print("Hello world")',
        "example_spoken": (
            "Here is an example. print, open round bracket, double quote, "
            "Hello world, double quote, close round bracket."
        ),
        "task": (
            "Now you try. Write one line that prints any short message you "
            "like. When you are ready, press Control and Enter together to run "
            "it. If you would like me to type an example for you, say: give me "
            "an example."
        ),
        "hints": [
            "Start the line with the word print, then an opening round bracket.",
            "Put your message inside double quotes, for example: quote, hello, quote.",
            "Finish with a closing round bracket. The whole line reads: print, "
            "bracket, quote, your message, quote, bracket.",
        ],
        "success": "Nicely done. You just made Python speak using a print statement.",
        "recap": (
            "Quick recap. A print statement is the word print, then brackets, "
            "with your message in quotes inside. It shows text in the output."
        ),
    },
    "variables": {
        "id": "variables",
        "order": 2,
        "title": "Variables",
        "concept": (
            "A variable gives a name to a piece of information so you can use it "
            "later. You choose a name, write an equals sign, then the value. In "
            "CodeUp you set values directly with equals. You do not need input."
        ),
        "example_code": 'name = "Aman"\nprint(name)',
        "example_spoken": (
            "Here is an example. First line: name, space, equals, space, quote, "
            "Aman, quote. Second line: print, bracket, name, bracket. That "
            "stores the word Aman in a variable called name, then prints it."
        ),
        "task": (
            "Now you try. On the first line, store any word or number in a "
            "variable using equals. On the next line, print that variable. Pick "
            "any name you like. Then press Control and Enter to run. Say give me "
            "an example if you would like me to fill one in."
        ),
        "hints": [
            "On line one, choose a name, then equals, then a value. For a word, "
            "wrap it in quotes. For a number, no quotes are needed.",
            "On line two, print your variable by name, with no quotes, like: "
            "print, bracket, your name, bracket.",
            "For example: score equals 10, then print, bracket, score, bracket.",
        ],
        "success": "Well done. You stored a value in a variable and printed it back.",
        "recap": (
            "Quick recap. A variable is a name, an equals sign, and a value. "
            "You can print the variable by writing its name inside print."
        ),
    },
    "if": {
        "id": "if",
        "order": 3,
        "title": "If statements",
        "concept": (
            "An if statement lets your program make a choice. It checks whether "
            "something is true, and only then runs the lines underneath it. "
            "Those lines must be indented, which means they start with four "
            "spaces."
        ),
        "example_code": 'age = 18\nif age >= 18:\n    print("You can vote")',
        "example_spoken": (
            "Here is an example. Line one: age equals 18. Line two: if, space, "
            "age, greater-than-or-equal, 18, colon. Line three is indented with "
            "four spaces: print, bracket, quote, You can vote, quote, bracket."
        ),
        "task": (
            "Now you try. First set a variable to a number or a word. Then write "
            "an if statement that checks a condition, ending the if line with a "
            "colon. On the next line, indented by four spaces, print a message. "
            "Press Control and Enter to run. Say give me an example for help."
        ),
        "hints": [
            "Set a variable first, for example: x equals 10.",
            "The if line ends with a colon, for example: if x is greater than 5 "
            "colon. In code that is: if x greater-than 5 colon.",
            "The print line under the if must be indented with four spaces. You "
            "can say: read line 3, to hear a line and its indentation.",
        ],
        "success": "Great work. Your if statement made a decision and printed when the condition was true.",
        "recap": (
            "Quick recap. An if statement checks a condition and ends with a "
            "colon. The lines that run when it is true are indented four spaces."
        ),
    },
    "for": {
        "id": "for",
        "order": 4,
        "title": "For loops",
        "concept": (
            "A for loop repeats an action a set number of times, or once for "
            "every item in a group. The repeated lines are indented with four "
            "spaces underneath the for line."
        ),
        "example_code": 'for number in range(3):\n    print(number)',
        "example_spoken": (
            "Here is an example. Line one: for, space, number, space, in, space, "
            "range, bracket, 3, bracket, colon. Line two is indented four "
            "spaces: print, bracket, number, bracket. That prints 0, 1, then 2."
        ),
        "task": (
            "Now you try. Write a for loop that prints something several times. "
            "Using range, bracket, 3, bracket, repeats three times. Remember the "
            "colon at the end of the for line, and indent the print line by four "
            "spaces. Press Control and Enter to run. Say give me an example for help."
        ),
        "hints": [
            "Start with: for, a loop name, in, range, bracket, a number, bracket, "
            "then a colon. For example: for i in range 3 colon.",
            "The line you want repeated must be indented four spaces under the "
            "for line.",
            "For example: for i in range 3 colon, then on the next line, four "
            "spaces, print, bracket, i, bracket.",
        ],
        "success": "Excellent. Your for loop repeated the action and printed each time.",
        "recap": (
            "Quick recap. A for loop with range repeats a set number of times. "
            "The for line ends with a colon and the repeated lines are indented."
        ),
    },
    "while": {
        "id": "while",
        "order": 5,
        "title": "While loops",
        "concept": (
            "A while loop keeps repeating as long as a condition stays true. To "
            "stop safely, you use a counter that changes each time until the "
            "condition becomes false."
        ),
        "example_code": "count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1",
        "example_spoken": (
            "Here is an example. Line one: count equals 1. Line two: while, "
            "space, count, less-than-or-equal, 3, colon. Line three, indented: "
            "print, bracket, count, bracket. Line four, also indented: count "
            "equals count plus 1. The counter grows until it passes 3, then the "
            "loop stops."
        ),
        "task": (
            "Now you try. Start a counter variable. Write a while loop that runs "
            "while the counter is below a limit. Inside the loop, print the "
            "counter and then increase it so the loop will end. Press Control "
            "and Enter to run. Say give me an example if you would like help."
        ),
        "hints": [
            "Set a counter first, for example: count equals 1.",
            "The while line ends with a colon, for example: while count less-than "
            "or equal 3 colon.",
            "Most important: inside the loop, change the counter each time, for "
            "example: count equals count plus 1. Without that, the loop never ends.",
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
