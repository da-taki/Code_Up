
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


GROUNDED_KINDS = {"quotes", "indentation", "colon"}

UNKNOWN_CONCEPT = "__unknown_concept__"   # concept-form question, topic unknown
IDENTITY_QUERY = "__identity__"
NON_CODE_QUERY = "__non_code__"

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

_BEGINNER_CONCEPTS = {
    "print": (
        "print is how Python shows text or values on the screen.\n\n"
        "Example:\n"
        "print(\"Hello\")\n\n"
        "Beginner note: print does not ask the user for information. It only shows output."),
    "input": (
        "input is how Python asks the user to type a value while the program is running.\n\n"
        "Example:\n"
        "name = input(\"Enter name: \")\n"
        "print(name)\n\n"
        "Beginner note: input receives information from the user, while print shows information to the user."),
    "range": (
        "range is how Python makes a simple sequence of numbers, often for a loop.\n\n"
        "Example:\n"
        "for number in range(3):\n"
        "    print(number)\n\n"
        "Beginner note: range(3) gives 0, 1, and 2, not 1, 2, and 3."),
    "for_loop": (
        "A for loop repeats the same block of code once for each value it is given.\n\n"
        "Example:\n"
        "for number in range(3):\n"
        "    print(number)\n\n"
        "Beginner note: the indented line is inside the loop, so it runs again and again."),
    "variable": (
        "A variable is a name that stores a value so your program can use it later.\n\n"
        "Example:\n"
        "score = 10\n"
        "print(score)\n\n"
        "Beginner note: the variable name is on the left, and the stored value is on the right."),
    "function": (
        "A function is a named set of instructions that you can run whenever you need it.\n\n"
        "Example:\n"
        "def greet():\n"
        "    print(\"Hello\")\n"
        "greet()\n\n"
        "Beginner note: defining a function saves the instructions; calling it makes them run."),
    "if_statement": (
        "An if statement lets Python choose whether to run a block of code based on a condition.\n\n"
        "Example:\n"
        "score = 80\n"
        "if score >= 50:\n"
        "    print(\"Pass\")\n\n"
        "Beginner note: the indented line runs only when the condition is true."),
    "list": (
        "A list stores several values in one ordered group.\n\n"
        "Example:\n"
        "scores = [80, 90, 75]\n"
        "print(scores[0])\n\n"
        "Beginner note: Python list positions start at 0, so scores[0] means the first item."),
}

_UNKNOWN_CONCEPT_MESSAGE = (
    "I do not have a prepared explanation for that concept yet. I can explain beginner Python and "
    "programming topics like variables, loops, functions, lists, dictionaries, classes, recursion, "
    "inheritance, exceptions, and time complexity.")

_IDENTITY_MESSAGE = (
    "I am CodeUp, a voice-first Python learning environment. I can help you create, run, debug, "
    "understand, and export Python code.")

_NON_CODE_MESSAGE = (
    "I am focused on helping with Python learning in this environment. You can ask me to create "
    "code, run code, explain code, debug errors, summarize structure, make trainer notes, or "
    "export your project.")

_IDENTITY_RE = re.compile(
    r"^(?:hey|ok|okay|so)?[,\s]*(?:"
    r"who\s+are\s+you|who\s+is\s+this|what\s+are\s+you|what\s+is\s+codeup|"
    r"what(?:'s| is)\s+your\s+name|whats\s+your\s+name|"
    r"are\s+you\s+(?:a\s+|an\s+)?(?:robot|human|real|ai|bot|person|chatgpt|gpt)|"
    r"introduce\s+yourself|tell\s+me\s+about\s+yourself"
    r")\s*\??$", re.IGNORECASE)
_NON_CODE_RE = re.compile(
    r"^(?:hey|ok|okay|so)?[,\s]*(?:"
    r"what\s+time\s+is\s+it|what(?:'s| is)\s+the\s+time|"
    r"what\s+day\s+is\s+it|what(?:'s| is)\s+(?:the\s+|today'?s\s+)?date|what(?:'s| is)\s+today|"
    r"what(?:'s| is)\s+the\s+weather|how(?:'s| is)\s+the\s+weather|"
    r"how\s+are\s+you(?:\s+doing)?|how\s+is\s+it\s+going|"
    r"are\s+you\s+(?:working|there|awake|ok|okay|online|ready|alive|listening)|"
    r"is\s+(?:this|it)\s+working|do\s+you\s+work"
    r")\s*\??$", re.IGNORECASE)

_WEAK_DEFER_WORDS = {
    "it", "again", "structure", "outline", "code", "program", "file", "line",
    "output", "error", "everything", "this", "that", "these", "those", "here",
}


def classify_non_code_query(text: str) -> Optional[str]:
    t = " ".join(str(text or "").lower().strip().split())
    if not t:
        return None
    if _IDENTITY_RE.match(t):
        return IDENTITY_QUERY
    if _NON_CODE_RE.match(t):
        return NON_CODE_QUERY
    return None


def non_code_answer(kind: str) -> str:
    if kind == IDENTITY_QUERY:
        return _IDENTITY_MESSAGE
    if kind == NON_CODE_QUERY:
        return _NON_CODE_MESSAGE
    return _UNKNOWN_CONCEPT_MESSAGE


_CONCEPT_DISPLAY = {"big_o": "time complexity", "oop": "object-oriented programming"}


def concept_label(kind: str) -> str:
    kind = str(kind or "").strip()
    if not kind or kind.startswith("__"):
        return ""
    return _CONCEPT_DISPLAY.get(kind, kind.replace("_", " "))


def _weak_command_target(topic: str) -> bool:
    t = (topic or "").lower()
    if _CODE_REF_RE.search(t):
        return True
    return bool(set(re.findall(r"[a-z]+", t)) & _WEAK_DEFER_WORDS)

_CONCEPT_ALIASES = {
    "print": ["print", "print function", "print functions", "print statement", "print statements"],
    "input": ["input", "input function", "input functions", "input statement"],
    "range": ["range", "range function", "range functions"],
    "for_loop": ["for loop", "for loops", "loop", "loops"],
    "variable": ["variable", "variables"],
    "function": ["function", "functions"],
    "if_statement": ["if statement", "if statements", "condition", "conditions",
                     "conditional", "conditionals"],
    "list": ["list", "lists"],
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

_DEFER_ALIASES = [
    "while loop", "while loops",
    "dictionary", "dictionaries", "dict", "dicts",
    "string", "strings",
    "boolean", "booleans", "bool", "true false", "indentation", "colon",
]

_CONCEPT_BY_ALIAS = {alias: kind for kind, aliases in _CONCEPT_ALIASES.items() for alias in aliases}
_CONCEPT_ALIASES_BY_LEN = sorted(_CONCEPT_BY_ALIAS, key=len, reverse=True)
_DEFER_SET = set(_DEFER_ALIASES)
_DEFER_BY_LEN = sorted(_DEFER_SET, key=len, reverse=True)

_DEFINITIONAL_FORM_RE = re.compile(
    r"^(?:hey|ok|okay|so|um|hmm)?[,\s]*"
    r"(?:"
    r"what(?:'s| is| are| was)\s+(?:a\s+|an\s+|the\s+)?(?P<a>.+?)"
    r"|what\s+do(?:es)?\s+(?:a\s+|an\s+|the\s+)?(?P<b>.+?)\s+mean"
    r"|define\s+(?:a\s+|an\s+|the\s+)?(?P<c>.+?)"
    r")\s*$",
    re.IGNORECASE,
)
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
    for kind, phrases in _TRIGGERS:
        for phrase in phrases:
            if t == phrase or t.startswith(phrase) or phrase in t:
                return kind
    topic = _extract_topic(_DEFINITIONAL_FORM_RE, t)
    if topic is not None:
        return _lookup_concept(topic)
    topic = _extract_topic(_WEAK_FORM_RE, t)
    if topic is not None:
        kind = _lookup_concept(topic)
        if kind not in (None, UNKNOWN_CONCEPT):
            return kind
        if kind is None:
            return None
        return None if _weak_command_target(topic) else UNKNOWN_CONCEPT
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
    code = code or ""
    if kind == "quotes":
        literal = _first_string_literal(code)
        if literal:
            return (f"Quotes tell Python that {literal} is text, not a variable name.",
                    [literal, "text", "variable"])
        return ("Quotes tell Python that the words inside are text, not a variable name.",
                ["text", "variable"])
    if kind in _BEGINNER_CONCEPTS:
        return (_BEGINNER_CONCEPTS[kind], [])
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
