from __future__ import annotations

import ast
import re
from typing import Callable, Dict, List, Optional, Tuple

MODULE_ORDER: List[str] = ["print", "variables", "if", "for", "while"]
CORE_MODULE_ORDER: List[str] = list(MODULE_ORDER)
EXPANDED_MODULE_ORDER: List[str] = list(MODULE_ORDER)

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


def first_module_id() -> str:
    return MODULE_ORDER[0]


def get_module(module_id: str) -> Optional[Dict]:
    return MODULES.get(module_id)


def next_module_id(module_id: str) -> Optional[str]:
    try:
        idx = MODULE_ORDER.index(module_id)
    except ValueError:
        return None
    if idx + 1 >= len(MODULE_ORDER):
        return None
    return MODULE_ORDER[idx + 1]


def module_pack() -> Dict:
    return {
        "order": list(MODULE_ORDER),
        "count": len(MODULE_ORDER),
        "modules": {mid: dict(MODULES[mid]) for mid in MODULE_ORDER},
    }


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


def check_while_safety(code: str) -> Tuple[bool, str]:
    tree = _safe_parse(code)
    if tree is None:
        return True, ""

    for node in ast.walk(tree):
        if not isinstance(node, ast.While):
            continue

        has_break = any(isinstance(n, ast.Break) for n in ast.walk(node))
        if has_break:
            continue

        test = node.test
        if isinstance(test, ast.Constant) and bool(test.value):
            return (
                False,
                "That while loop uses while True with no way out, so it would "
                "run forever. Use a counter and a condition, for example while "
                "count is less than 3, and change the counter inside the loop.",
            )

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


_COACH_SIMPLE: Dict[str, str] = {
    "print": "A print statement tells Python to show a message on the screen.",
    "variables": "A variable is a labelled box that remembers a value so you can use it later.",
    "if": "An if statement runs the indented line only when something is true.",
    "for": "A for loop repeats the indented line a set number of times.",
    "while": "A while loop keeps repeating while something stays true, then stops.",
}

_COACH_FACTS: Dict[str, str] = {
    "quotes": "Quotes tell Python the words inside are text to show, not the name of a variable.",
    "indentation": "Indentation, the four spaces, means that line belongs inside the block above it.",
}

_COACH_ENCOURAGE = (
    "Good attempt. The idea is right; only the wording needs a small fix.",
    "You are doing fine. This part trips up everyone at first. Let us take it one small step.",
)

COACH_REQUESTS = {
    "explain_simpler",
    "dont_understand",
    "why_quotes",
    "why_indentation",
    "another_hint",
    "encourage",
    "what_learning",
}

_COACH_TRIGGERS: List[Tuple[str, List[str]]] = [
    ("why_quotes", ["why do we use quotes", "why use quotes", "why the quotes", "why quotes",
                    "what are quotes for", "what do quotes do", "purpose of quotes"]),
    ("why_indentation", ["why do we indent", "why the indentation", "why indentation", "why indent",
                         "what is indentation", "what does indentation do", "why four spaces"]),
    ("another_hint", ["give me another hint", "another hint", "one more hint", "a different hint",
                      "different hint", "more hints", "next hint"]),
    ("what_learning", ["what am i learning", "what are we learning", "what is this teaching",
                       "what am i doing", "what topic is this", "what is this topic", "what is this about"]),
    ("encourage", ["encourage me", "i give up", "this is too hard", "this is hard", "i can't do this",
                   "i cannot do this", "i am stuck", "i'm stuck", "cheer me up"]),
    ("explain_simpler", ["say that again simpler", "say it simpler", "explain it simpler",
                         "explain simpler", "explain that simpler", "make it simpler",
                         "simpler please", "in simple words", "in simpler words", "simpler"]),
    ("dont_understand", ["i don't understand", "i do not understand", "i dont understand",
                         "i don't get it", "i dont get it", "i'm confused", "i am confused",
                         "i don't follow", "this is confusing", "confused"]),
]


def classify_coach_request(text: str) -> Optional[str]:
    t = " ".join(str(text or "").lower().strip().rstrip(".!?").split())
    if not t:
        return None
    for request_type, phrases in _COACH_TRIGGERS:
        for phrase in phrases:
            if t == phrase or phrase in t:
                return request_type
    return None


def coach_response(module_id: str, request: str, attempts: int = 0) -> Dict:
    module = MODULES.get(module_id or "")
    title = module["title"] if module else "this topic"
    simpler = _COACH_SIMPLE.get(module_id or "", "")
    try:
        attempts = max(0, int(attempts or 0))
    except (TypeError, ValueError):
        attempts = 0

    if request == "explain_simpler":
        text = simpler or (module["concept"] if module else "Let us take it one small step at a time.")
    elif request == "dont_understand":
        body = simpler or (module["concept"] if module else "Let us take it one small step at a time.")
        text = "No problem. " + body
    elif request == "why_quotes":
        text = _COACH_FACTS["quotes"]
    elif request == "why_indentation":
        text = _COACH_FACTS["indentation"]
    elif request == "another_hint":
        hints = (module or {}).get("hints") or []
        if hints:
            idx = min(attempts, len(hints) - 1)
            text = "Here is another hint. " + hints[idx]
        else:
            text = "Try saying: give me an example."
    elif request == "encourage":
        text = _COACH_ENCOURAGE[1] if attempts >= 2 else _COACH_ENCOURAGE[0]
        hint = first_hint(module_id)
        if hint:
            text = text + " " + hint
    elif request == "what_learning":
        text = (f"Right now you are learning {title}. " + simpler).strip()
    else:
        return {"request": None, "text": "", "kind": "none"}

    return {"request": request, "text": text.strip(), "kind": "coach"}


def validate_attempt(
    module_id: str,
    code: str,
    ran_ok: bool = True,
    output: str = "",
) -> Dict:
    code = code or ""
    module = MODULES.get(module_id)
    if module is None:
        return {
            "passed": False,
            "safe": True,
            "feedback": "That topic is not part of the tutorial.",
            "hint": None,
        }

    if _safe_parse(code) is None:
        return {
            "passed": False,
            "safe": True,
            "feedback": "There is a small typo in your code, so Python could not "
            "read it. Listen to the error, then fix and run again.",
            "hint": first_hint(module_id),
        }

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

# BEGINNER_CURRICULUM_EXTENSION: deterministic modules added after Vision-Aid feedback.
def _has_call_named(code: str, name: str) -> bool:
    tree = _safe_parse(code)
    if tree is None:
        return False
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name for n in ast.walk(tree))


def _has_binop(code: str, ops) -> bool:
    tree = _safe_parse(code)
    if tree is None:
        return False
    return any(isinstance(n, ast.BinOp) and isinstance(n.op, ops) for n in ast.walk(tree))


def _has_compare(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and any(isinstance(n, ast.Compare) for n in ast.walk(tree)))


def _has_boolop(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and any(isinstance(n, ast.BoolOp) for n in ast.walk(tree)))


def _has_if_else(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and any(isinstance(n, ast.If) and n.orelse for n in ast.walk(tree)))


def _has_elif(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and any(isinstance(n, ast.If) and any(isinstance(child, ast.If) for child in n.orelse) for n in ast.walk(tree)))


def _has_nested_if(code: str) -> bool:
    tree = _safe_parse(code)
    if tree is None:
        return False
    return any(isinstance(n, ast.If) and any(isinstance(c, ast.If) for c in ast.walk(ast.Module(body=n.body, type_ignores=[]))) for n in ast.walk(tree))


def _has_counter(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and any(isinstance(n, ast.AugAssign) or (isinstance(n, ast.Assign) and isinstance(n.value, ast.BinOp)) for n in ast.walk(tree)))


def _has_list(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and any(isinstance(n, (ast.List, ast.ListComp)) for n in ast.walk(tree)))


def _has_dict(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and any(isinstance(n, ast.Dict) for n in ast.walk(tree)))


def _has_function_with_return(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and any(isinstance(n, ast.FunctionDef) and any(isinstance(c, ast.Return) for c in ast.walk(n)) for n in ast.walk(tree)))


def _has_function_params(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and any(isinstance(n, ast.FunctionDef) and n.args.args for n in ast.walk(tree)))


def _has_function_call(code: str) -> bool:
    tree = _safe_parse(code)
    if tree is None:
        return False
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in defined for n in ast.walk(tree))


def _has_two_functions(code: str) -> bool:
    tree = _safe_parse(code)
    return bool(tree and sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)) >= 2)


def _always_practice(code: str) -> bool:
    return bool(str(code or '').strip())


def _module(mid, order, title, concept, example, task, hints, success, validator):
    MODULES[mid] = {
        "id": mid,
        "order": order,
        "title": title,
        "concept": concept,
        "example_code": example,
        "example_spoken": "Ask for the example when you want the exact starter code read aloud.",
        "task": task,
        "hints": hints,
        "success": success,
        "recap": f"Quick recap. {concept}",
    }
    _VALIDATORS[mid] = validator


_EXTRA_MODULES = [
    ("input", "Input", "input lets a program ask the learner for a value while it runs.", 'name = input("Name: ")\nprint(name)', "Ask for a name, store it, then print it back.", ["Use input with a prompt.", "Store the result in a variable.", "Then print the variable."], lambda c: _has_call_named(c, "input")),
    ("data_types", "Data types", "Data types are the kinds of values Python works with, such as text and numbers.", 'age = 16\nname = "Asha"\nprint(type(age))', "Use at least two different value types.", ["Try one number and one string.", "Strings use quotes.", "type(value) can show the kind."], _always_practice),
    ("type_conversion", "Type conversion", "Type conversion changes a value from one kind to another when that is safe.", 'marks = int("90")\nprint(marks + 5)', "Convert text to a number and use it in arithmetic.", ["Use int(), float(), or str().", "input() gives text first.", "Convert before adding numbers."], lambda c: _has_call_named(c, 'int') or _has_call_named(c, 'float') or _has_call_named(c, 'str')),
    ("arithmetic", "Arithmetic operators", "Arithmetic operators let Python calculate with numbers.", 'total = 80 + 15\nprint(total)', "Use an arithmetic operator and print the result.", ["Try plus or minus first.", "Store the result in a variable.", "Print the result."], lambda c: _has_binop(c, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow))),
    ("comparison", "Comparison operators", "Comparisons ask a true-or-false question about values.", 'score = 90\nprint(score >= 40)', "Use a comparison such as greater than or equal to.", ["Try score >= 40.", "A comparison gives True or False.", "Print the comparison to hear the result."], _has_compare),
    ("boolean_logic", "Boolean logic", "Boolean logic combines true-or-false conditions with and, or, and not.", 'score = 90\nattendance = 80\nprint(score >= 40 and attendance >= 75)', "Combine two comparisons.", ["Use and when both must be true.", "Use or when either can be true.", "Use not to reverse a condition."], _has_boolop),
    ("conditions", "Conditions", "A condition is a true-or-false test that controls a decision.", 'marks = 55\nif marks >= 40:\n    print("pass")', "Write a condition inside an if statement.", ["Use if marks >= 40 colon.", "Indent the action.", "Print pass inside the block."], validate_if),
    ("if_else", "if and else", "if and else choose between two paths.", 'marks = 35\nif marks >= 40:\n    print("pass")\nelse:\n    print("try again")', "Use if and else to handle both results.", ["The else line also ends with a colon.", "Indent both print lines.", "Use else for the other case."], _has_if_else),
    ("elif", "elif", "elif checks another condition after the first if is false.", 'marks = 75\nif marks >= 80:\n    print("A")\nelif marks >= 60:\n    print("B")\nelse:\n    print("C")', "Use if, elif, and else for grades.", ["Start with the highest grade.", "Use elif for the middle grade.", "Use else for everything left."], _has_elif),
    ("nested_conditions", "Nested conditions", "A nested condition is an if statement inside another block.", 'marks = 80\nif marks >= 40:\n    if marks >= 75:\n        print("distinction")', "Put one if statement inside another.", ["Indent the inner if.", "The inner print is indented twice.", "Keep the example small."], _has_nested_if),
    ("for_loops", "for loops", "A for loop repeats for each value in a known sequence.", 'for number in range(5):\n    print(number)', "Use a for loop with range.", ["Use range(5).", "Indent the print.", "Run to hear each number."], validate_for),
    ("while_loops", "while loops", "A while loop repeats while a condition stays true.", 'count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1', "Create a while loop that changes its counter.", ["Start count at 1.", "Use while count <= 3.", "Add count = count + 1 inside."], validate_while),
    ("counters", "Counters", "A counter keeps track of how many times something happened.", 'count = 0\nfor item in range(3):\n    count = count + 1\nprint(count)', "Count three loop passes.", ["Start at zero.", "Add one inside the loop.", "Print after the loop."], _has_counter),
    ("accumulators", "Accumulators", "An accumulator builds a total over time.", 'total = 0\nfor mark in [80, 90]:\n    total = total + mark\nprint(total)', "Add several marks into a total.", ["Start total at zero.", "Add each mark inside the loop.", "Print total after the loop."], _has_counter),
    ("strings", "Strings", "Strings are text values in quotes.", 'name = "Asha"\nprint("Hello " + name)', "Create and print a string.", ["Use quotes for text.", "Store text in a variable.", "Print it or combine it."], lambda c: bool(re.search(r"['\"]", c or ''))),
    ("lists", "Lists", "A list stores several values in order.", 'marks = [80, 90, 75]\nprint(marks[0])', "Create a list and read one item.", ["Use square brackets.", "Separate items with commas.", "Index 0 is the first item."], _has_list),
    ("list_iteration", "List iteration", "List iteration means using a loop to visit each item.", 'marks = [80, 90, 75]\nfor mark in marks:\n    print(mark)', "Loop through a list.", ["Create the list first.", "Use for mark in marks.", "Print mark inside the loop."], lambda c: _has_list(c) and validate_for(c)),
    ("dictionaries", "Dictionaries", "A dictionary stores values under named keys.", 'student = {"name": "Asha", "marks": 90}\nprint(student["name"])', "Create a student dictionary.", ["Use braces.", "Keys are labels like name.", "Read a value with square brackets."], _has_dict),
    ("functions", "Functions", "A function is a named set of steps you can call later.", 'def greet():\n    print("Hello")\ngreet()', "Define and call a function.", ["Start with def.", "Indent the function body.", "Call it by writing its name with parentheses."], _has_function_call),
    ("parameters", "Parameters", "A parameter is a name that receives a value inside a function.", 'def greet(name):\n    print(name)\ngreet("Asha")', "Create a function with one parameter.", ["Put the parameter inside the parentheses.", "Use it inside the function.", "Pass a value when calling."], _has_function_params),
    ("return_values", "Return values", "return sends a result back from a function.", 'def add(a, b):\n    return a + b\nprint(add(2, 3))', "Write a function that returns a value.", ["Use return inside the function.", "Print the function call.", "Do not confuse return with print."], _has_function_with_return),
    ("scope", "Scope", "Scope means where a variable name can be used.", 'def show_score():\n    score = 90\n    print(score)\nshow_score()', "Use a variable inside a function.", ["Create the variable inside the function.", "Print it inside that function.", "Then call the function."], _has_function_call),
    ("syntax_errors", "Common syntax errors", "Syntax errors happen when Python cannot read the code shape.", 'if True:\n    print("fixed")', "Fix or write a tiny if block with a colon and indentation.", ["Look for missing colons.", "Check quotes and parentheses.", "Indent block lines."], _always_practice),
    ("name_error", "NameError", "NameError usually means a name was used before Python knew it.", 'score = 90\nprint(score)', "Create a variable before printing it.", ["Spell the name the same way.", "Assign first, use second.", "Run after fixing."], validate_variables),
    ("type_error", "TypeError", "TypeError often means two values do not fit the operation.", 'age = 16\nprint("Age: " + str(age))', "Convert a number before joining it with text.", ["Text plus number causes trouble.", "Use str(number).", "Then join the text."], lambda c: _has_call_named(c, 'str')),
    ("debugging", "Debugging strategies", "Debugging means finding one small cause and testing a fix.", 'total = 0\nprint("total is", total)', "Add a print that helps inspect a value.", ["Print one value at a time.", "Run after each small change.", "Read the exact error line."], validate_print),
    ("decomposition", "Program decomposition", "Decomposition means splitting a program into smaller named parts.", 'def get_marks():\n    return 90\ndef show_result(marks):\n    print(marks)\nshow_result(get_marks())', "Split a task into two functions.", ["One function can get a value.", "Another can show it.", "Call them together at the end."], _has_two_functions),
    ("multifile_basics", "Multi-file basics", "Multi-file programs keep related code in separate files when projects grow.", 'def helper():\n    return "ready"\nprint(helper())', "Practice separating a helper idea into a function.", ["In this single editor, use a helper function first.", "Later that helper can move to another file.", "Keep names clear."], _has_function_call),
    ("beginner_projects", "Beginner projects", "A beginner project combines several concepts into one useful program.", 'marks = [80, 90, 75]\ntotal = 0\nfor mark in marks:\n    total = total + mark\naverage = total / len(marks)\nprint(average)', "Build a small marks, calculator, quiz, or record program.", ["Choose one small goal.", "Use variables first.", "Add conditions or loops after the first version works."], _always_practice),
]

_start = len(MODULE_ORDER) + 1
for _offset, (_mid, _title, _concept, _example, _task, _hints, _validator) in enumerate(_EXTRA_MODULES, start=0):
    if _mid not in MODULES:
        EXPANDED_MODULE_ORDER.append(_mid)
        _module(_mid, _start + _offset, _title, _concept, _example, _task, _hints, f"Good work. You practised {_title.lower()}.", _validator)



_TOPIC_ALIASES = {
    "print": "print", "printing": "print", "output": "print",
    "variable": "variables", "variables": "variables",
    "if": "if", "if statements": "if", "if statement": "if",
    "for": "for", "for loop": "for", "for loops": "for", "while": "while", "while loop": "while",
    "while loops": "while", "loops": "for_loops", "input": "input", "inputs": "input",
    "types": "data_types", "data types": "data_types", "type conversion": "type_conversion",
    "converting types": "type_conversion", "maths": "arithmetic", "math": "arithmetic",
    "arithmetic": "arithmetic", "comparison": "comparison", "comparisons": "comparison",
    "boolean": "boolean_logic", "booleans": "boolean_logic", "and or not": "boolean_logic",
    "conditions": "conditions", "else": "if_else", "if else": "if_else", "elif": "elif",
    "nested if": "nested_conditions", "nested conditions": "nested_conditions",
    "counter": "counters", "counters": "counters", "accumulator": "accumulators",
    "accumulators": "accumulators", "totals": "accumulators", "string": "strings", "strings": "strings",
    "text": "strings", "list": "lists", "lists": "lists", "list loops": "list_iteration",
    "looping over lists": "list_iteration", "dictionary": "dictionaries", "dictionaries": "dictionaries",
    "dict": "dictionaries", "function": "functions", "functions": "functions",
    "parameter": "parameters", "parameters": "parameters", "return": "return_values",
    "return values": "return_values", "scope": "scope", "syntax errors": "syntax_errors",
    "syntax error": "syntax_errors", "name error": "name_error", "nameerror": "name_error",
    "type error": "type_error", "typeerror": "type_error", "debugging": "debugging",
    "decomposition": "decomposition", "multiple files": "multifile_basics",
    "multi file": "multifile_basics", "projects": "beginner_projects", "beginner projects": "beginner_projects",
}


def practice_module_for(topic: str) -> Optional[str]:
    """Resolve a spoken/typed topic ("dictionaries", "type conversion") to any
    module id in the expanded curriculum, core modules included."""
    key = " ".join(str(topic or "").lower().replace("_", " ").split())
    key = re.sub(r"^(?:the|a|an)\s+", "", key)
    key = re.sub(r"\s+(?:module|topic|lesson|again|tutorial)$", "", key).strip()
    if not key:
        return None
    if key in _TOPIC_ALIASES:  # aliases first: "for loops" stays the core "for" module
        return _TOPIC_ALIASES[key]
    if key.replace(" ", "_") in MODULES:
        return key.replace(" ", "_")
    for mid in EXPANDED_MODULE_ORDER:
        if MODULES[mid]["title"].lower() == key:
            return mid
    return None


def topics_listing() -> str:
    core = ", ".join(MODULES[m]["title"] for m in MODULE_ORDER)
    extra = ", ".join(MODULES[m]["title"] for m in EXPANDED_MODULE_ORDER if m not in MODULE_ORDER)
    return (f"The step by step tutorial covers {core}. You can also practise any of these "
            f"{len(EXPANDED_MODULE_ORDER) - len(MODULE_ORDER)} topics: {extra}. "
            "Say practise and a topic name, for example practise dictionaries.")


def expanded_module_pack() -> Dict:
    return {
        "order": list(EXPANDED_MODULE_ORDER),
        "count": len(EXPANDED_MODULE_ORDER),
        "modules": {mid: dict(MODULES[mid]) for mid in EXPANDED_MODULE_ORDER},
    }
