"""Global beginner concept questions ("why do we use quotes", "what does range
3 mean") answered anywhere — not only inside the tutorial.

Answers are deterministic and grounded in the current editor code when it helps
(the actual string literal, the actual range count), so Key 2 can later make
them warmer/shorter without inventing anything. Pure and Flask-free.
"""

import re
from typing import List, Optional, Tuple

_COUNT_WORDS = {1: "once", 2: "twice", 3: "three times", 4: "four times", 5: "five times"}
_NUM_WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
              6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}

_TRIGGERS = [
    ("quotes", ["why do we use quotes", "why are there quotes", "why are the quotes there",
                "what are the quotes for", "what do quotes do", "why use quotes", "why the quotes",
                "why quotes", "why is there a quote", "what do the quotes mean"]),
    ("indentation", ["why indentation", "why do we indent", "why is this indented",
                     "why is this line indented", "what is indentation", "why the indentation",
                     "why four spaces", "why is it indented"]),
    ("colon", ["why do we need a colon", "why do we need colon", "why the colon",
               "what is the colon for"]),
    ("range", ["what does range", "what is range", "why range", "what does the range",
               "explain range"]),
    ("print", ["what does print mean", "what does print do", "what is print",
               "what does the print do", "what does print"]),
    ("variable", ["what is a variable", "what does variable mean", "what is variable",
                  "what does a variable do", "what's a variable"]),
]


def classify_concept_question(text: str) -> Optional[str]:
    t = " ".join(str(text or "").lower().strip().rstrip(".!?").split())
    if not t:
        return None
    for kind, phrases in _TRIGGERS:
        for phrase in phrases:
            if t == phrase or t.startswith(phrase) or phrase in t:
                return kind
    return None


def _first_string_literal(code: str) -> Optional[str]:
    match = re.search(r"""(['"])(.*?)\1""", code or "")
    if match and match.group(2).strip():
        return match.group(2).strip()
    return None


def _first_range_count(code: str) -> Optional[int]:
    match = re.search(r"\brange\s*\(\s*(\d+)", code or "")
    return int(match.group(1)) if match else None


def _number_list(n: int) -> str:
    items = [str(i) for i in range(n)]
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + ", and " + items[-1]


def answer_concept(kind: str, code: str = "") -> Tuple[str, List[str]]:
    """Return (deterministic answer, required facts to preserve under grounding)."""
    code = code or ""
    if kind == "quotes":
        literal = _first_string_literal(code)
        if literal:
            return (f"Quotes tell Python that {literal} is text, not a variable name.",
                    [literal, "text", "variable"])
        return ("Quotes tell Python that the words inside are text, not a variable name.",
                ["text", "variable"])
    if kind == "range":
        count = _first_range_count(code)
        if count is not None and 0 < count <= 10:
            times = _COUNT_WORDS.get(count, f"{count} times")
            return (f"range({count}) gives Python the numbers {_number_list(count)}, "
                    f"so the loop runs {times}.",
                    [f"range({count})", str(count)])
        return ("range(n) gives Python the numbers from 0 up to n minus one, "
                "so the loop runs n times.", ["range", "0"])
    if kind == "print":
        return ("The print statement tells Python to show a message on the screen.",
                ["print", "message"])
    if kind == "variable":
        return ("A variable is a named box that stores a value so you can use it later.",
                ["variable", "value"])
    if kind == "indentation":
        return ("Indentation, the spaces at the start of a line, means that line belongs "
                "inside the block above it.", ["indentation", "block"])
    if kind == "colon":
        return ("A colon ends the line that starts a block, and the indented lines below "
                "belong to it.", ["colon", "block"])
    return ("", [])
