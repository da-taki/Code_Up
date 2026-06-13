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


# Concepts answered by the existing code-grounded _TRIGGERS path. These are
# rephrased by Key 2 when available (the deterministic text is the fallback).
GROUNDED_KINDS = {"quotes", "indentation", "colon", "range", "print", "variable"}

# Sentinel for a clear concept-question form whose topic we do not recognise.
UNKNOWN_CONCEPT = "__unknown_concept__"

# Deterministic, beginner-friendly, spoken-safe explanations for the broader
# (often advanced) concepts that the narrow _TRIGGERS list did not cover. No
# markdown, no emojis, two to four short sentences. Returned verbatim so the AI
# grounding step cannot truncate them to a single sentence.
_CONCEPTS = {
    "recursion": (
        "Recursion is when a function calls itself to solve a smaller version of the same "
        "problem. A classic example is factorial, where the factorial of 5 uses the factorial "
        "of 4. In Python, recursion needs a stopping point called a base case, or it would run "
        "forever."),
    "inheritance": (
        "Inheritance is when one class builds on another. The child class inherits the "
        "behaviour of the parent class and can add to it or change it. It is a core idea in "
        "object-oriented programming and helps you reuse code."),
    "big_o": (
        "Big O describes how the work an algorithm does grows as the input gets bigger. For "
        "example, checking every item in a list is usually O of n, because the work grows with "
        "the number of items. It helps you compare how efficient different approaches are."),
    "tuple": (
        "A tuple is like a list, but it usually does not change after you create it. In Python "
        "you write a tuple with parentheses, for example coordinates = (3, 4). Use a tuple when "
        "a group of values should stay fixed."),
    "set": (
        "A set is a collection that keeps only unique items, with no duplicates and no "
        "guaranteed order. In Python you write a set with curly braces, for example "
        "colours = {'red', 'green'}. Sets are handy for removing duplicates."),
    "decorator": (
        "A decorator is a Python feature that wraps a function to add behaviour before or after "
        "it runs, without changing the function's own code. Decorators are an advanced topic, "
        "so as a beginner you can think of them as a way to add to a function from the outside."),
    "oop": (
        "Object-oriented programming, or OOP, organises code around objects. An object bundles "
        "data together with the actions that work on that data, and a class is the blueprint for "
        "making objects. It helps you model real things and reuse code."),
    "class": (
        "A class is a blueprint for creating objects. It groups related data together with the "
        "functions that work on that data, which are called methods. For example, a Dog class "
        "could store a name and have a bark method."),
    "method": (
        "A method is a function that belongs to a class and works on its objects. You call it on "
        "an object, for example dog.bark(). The first parameter, usually called self, refers to "
        "the object the method is working on."),
    "module": (
        "A module is a Python file full of code you can reuse. You bring it into your program "
        "with import, for example import math, and then use what is inside it, like math.sqrt. "
        "Modules help you organise and share code."),
    "import": (
        "Import brings code from another module into your program so you can use it. For "
        "example, import random lets you use random.randint. Imports usually go at the top of "
        "the file."),
    "exception": (
        "An exception is an error that happens while a program runs, such as dividing by zero. "
        "You handle it with try and except: the risky code goes in the try block, and the except "
        "block runs if an error happens, so the program does not crash."),
    "parameter": (
        "A parameter is a named input listed in a function's definition. The actual value you "
        "pass when you call the function is called an argument. For example, in def greet(name), "
        "name is the parameter."),
    "return": (
        "A return value is the result a function sends back using the return keyword. The code "
        "that called the function can then use that value. For example, a function that adds two "
        "numbers can return their sum so you can store or print it."),
}

_UNKNOWN_CONCEPT_MESSAGE = (
    "I can explain beginner Python concepts like variables, loops, functions, lists, "
    "dictionaries, classes, recursion, inheritance, and time complexity. Try asking, what is a "
    "loop, or what is recursion.")

# Aliases for the concepts above (the ones we answer here).
_CONCEPT_ALIASES = {
    "recursion": ["recursion", "recursive function", "recursive functions", "recursive"],
    "inheritance": ["inheritance", "parent class", "child class", "subclass", "superclass", "inherit"],
    "big_o": ["big o", "big-o", "big o notation", "time complexity", "space complexity",
              "algorithm complexity", "algorithmic complexity"],
    "tuple": ["tuple", "tuples"],
    "set": ["set", "sets"],
    "decorator": ["decorator", "decorators"],
    "oop": ["object oriented programming", "object-oriented programming", "oop"],
    "class": ["class", "classes"],
    "method": ["method", "methods"],
    "module": ["module", "modules"],
    "import": ["import", "imports", "importing", "import statement"],
    "exception": ["exception", "exceptions", "exception handling", "try except", "try/except",
                  "try and except", "error handling"],
    "parameter": ["parameter", "parameters", "argument", "arguments"],
    "return": ["return", "return value", "return values", "return statement"],
}

# Concepts already answered well by the existing concept-mentor / _TRIGGERS path.
# We recognise them here only to DEFER (return None) so we never steal their
# richer, code-grounded answers — and so the unknown-concept fallback does not
# fire for them.
_DEFER_ALIASES = [
    "loop", "loops", "for loop", "for loops", "while loop", "while loops",
    "list", "lists", "dictionary", "dictionaries", "dict", "dicts",
    "string", "strings", "function", "functions", "variable", "variables",
    "boolean", "booleans", "bool", "true false", "range", "print",
    "if statement", "if statements", "conditional", "conditionals", "indentation", "colon",
]

_CONCEPT_BY_ALIAS = {alias: kind for kind, aliases in _CONCEPT_ALIASES.items() for alias in aliases}
_CONCEPT_ALIASES_BY_LEN = sorted(_CONCEPT_BY_ALIAS, key=len, reverse=True)
_DEFER_SET = set(_DEFER_ALIASES)
_DEFER_BY_LEN = sorted(_DEFER_SET, key=len, reverse=True)

# Definitional forms ("what is X", "define X"). These clearly ask for a
# definition, so an unrecognised topic gets the helpful concept fallback.
_DEFINITIONAL_FORM_RE = re.compile(
    r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*"
    r"(?:"
    r"what(?:'s| is| are| was)\s+(?:a\s+|an\s+|the\s+)?(?P<a>.+?)"
    r"|what\s+do(?:es)?\s+(?:a\s+|an\s+|the\s+)?(?P<b>.+?)\s+mean"
    r"|define\s+(?:a\s+|an\s+|the\s+)?(?P<c>.+?)"
    r")\s*$",
    re.IGNORECASE,
)
# Command-overloaded forms ("explain X", "teach me X", "how does X work"). These
# double as real commands ("explain structure", "explain it again"), so they only
# count as a concept question when X is a KNOWN concept — otherwise we defer.
_WEAK_FORM_RE = re.compile(
    r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*"
    r"(?:"
    r"explain\s+(?:to\s+me\s+)?(?:what\s+(?:a\s+|an\s+|the\s+)?)?(?:a\s+|an\s+|the\s+)?(?P<a>.+?)"
    r"|tell\s+me\s+(?:more\s+)?about\s+(?:a\s+|an\s+|the\s+)?(?P<b>.+?)"
    r"|how\s+do(?:es)?\s+(?:a\s+|an\s+|the\s+)?(?P<c>.+?)\s+work"
    r"|why\s+(?:use|do\s+we\s+use|would\s+i\s+use)\s+(?:a\s+|an\s+|the\s+)?(?P<d>.+?)"
    r"|teach\s+me\s+(?:about\s+)?(?:a\s+|an\s+|the\s+)?(?P<e>.+?)"
    r")\s*$",
    re.IGNORECASE,
)
# Words that signal the learner means THEIR code, not a general concept — defer
# to Ask My Code / navigation / the mentor instead of a generic explanation.
_CODE_REF_RE = re.compile(
    r"\b(this|these|that|those|my\s+code|my\s+program|the\s+code|the\s+program|"
    r"current\s+code|the\s+output|the\s+error|this\s+line|the\s+line|here)\b", re.IGNORECASE)


def _extract_topic(rx, t: str) -> Optional[str]:
    m = rx.match(t)
    if not m:
        return None
    topic = next((g for g in m.groupdict().values() if g), "") or ""
    topic = re.sub(r"\s+in\s+python$", "", topic.strip(), flags=re.IGNORECASE)
    topic = topic.strip(" ?.!,'\"")
    return topic or None


def _lookup_concept(topic: str) -> Optional[str]:
    """Return a known concept kind, None to defer, or UNKNOWN_CONCEPT."""
    topic = re.sub(r"\s+", " ", (topic or "").lower()).strip()
    if not topic:
        return None
    if _CODE_REF_RE.search(topic):
        return None
    if topic in _CONCEPT_BY_ALIAS:
        return _CONCEPT_BY_ALIAS[topic]
    if topic in _DEFER_SET:
        return None
    for alias in _CONCEPT_ALIASES_BY_LEN:
        if re.search(rf"\b{re.escape(alias)}\b", topic):
            return _CONCEPT_BY_ALIAS[alias]
    for alias in _DEFER_BY_LEN:
        if re.search(rf"\b{re.escape(alias)}\b", topic):
            return None
    return UNKNOWN_CONCEPT


def classify_concept_question(text: str) -> Optional[str]:
    t = " ".join(str(text or "").lower().strip().rstrip(".!?").split())
    if not t:
        return None
    # 1) Existing code-grounded beginner triggers (quotes, range, print, ...).
    for kind, phrases in _TRIGGERS:
        for phrase in phrases:
            if t == phrase or t.startswith(phrase) or phrase in t:
                return kind
    # 2a) Definitional forms ("what is recursion", "define a tuple"): a known
    # concept, a deferred one (None), or the helpful unknown-concept fallback.
    topic = _extract_topic(_DEFINITIONAL_FORM_RE, t)
    if topic is not None:
        return _lookup_concept(topic)
    # 2b) Command-overloaded forms ("explain X", "teach me X"): only when X is a
    # KNOWN concept, so "explain structure" / "explain it again" still route to
    # their real commands instead of a generic explanation.
    topic = _extract_topic(_WEAK_FORM_RE, t)
    if topic is not None:
        kind = _lookup_concept(topic)
        return kind if kind not in (None, UNKNOWN_CONCEPT) else None
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
    if kind == UNKNOWN_CONCEPT:
        return (_UNKNOWN_CONCEPT_MESSAGE, [])
    if kind in _CONCEPTS:
        return (_CONCEPTS[kind], [])
    return ("", [])
