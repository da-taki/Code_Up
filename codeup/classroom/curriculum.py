"""The real, interactive beginner Python course for cohort learners.

Deliberately a NEW, separate module rather than an extension of
``codeup.learning.tutorial_engine``: that engine's 5-topic curriculum
(print/variables/if/for/while) is exact-list-asserted by existing tests
(``MODULE_ORDER == [...]``, last item must stay "while"), so touching it
would break working, already-shipped behavior for the anonymous IDE. This
module reuses the exact same shape and validation style (concept -> example
-> attempt -> deterministic AST check -> feedback -> challenge -> quiz), just
for the classroom's 10-topic course, addressed independently.

Every module has the SAME shape whether it's built-in (defined here) or
instructor-authored (stored in the database via ``db.create_custom_lesson``)
- see ``classroom/routes.py``'s curriculum views, which resolve a module id
of the form ``custom:<id>`` from the database and otherwise look it up here,
so learners see one accessible lesson interface, not two engines.
"""

from __future__ import annotations

import ast
from typing import Callable, Dict, List, Optional, Tuple

Check = Callable[[str], bool]


def _parse(code: str) -> Optional[ast.AST]:
    try:
        return ast.parse(code or "")
    except SyntaxError:
        return None


def _calls_named(tree: ast.AST, name: str) -> List[ast.Call]:
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


def has_print(code: str) -> bool:
    tree = _parse(code)
    return bool(tree) and bool(_calls_named(tree, "print"))


def has_assignment(code: str) -> bool:
    tree = _parse(code)
    if not tree:
        return False
    return any(isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) for n in ast.walk(tree))


def has_input(code: str) -> bool:
    tree = _parse(code)
    return bool(tree) and bool(_calls_named(tree, "input"))


def has_type_conversion(code: str) -> bool:
    tree = _parse(code)
    if not tree:
        return False
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {"int", "float", "str", "bool"}
        for n in ast.walk(tree)
    )


def has_if(code: str) -> bool:
    tree = _parse(code)
    return bool(tree) and any(isinstance(n, ast.If) for n in ast.walk(tree))


def has_if_else(code: str) -> bool:
    tree = _parse(code)
    if not tree:
        return False
    return any(isinstance(n, ast.If) and n.orelse for n in ast.walk(tree))


def has_for(code: str) -> bool:
    tree = _parse(code)
    return bool(tree) and any(isinstance(n, ast.For) for n in ast.walk(tree))


def has_while(code: str) -> bool:
    tree = _parse(code)
    return bool(tree) and any(isinstance(n, ast.While) for n in ast.walk(tree))


def while_is_safe(code: str) -> bool:
    """Mirrors tutorial_engine's infinite-loop guard for while-loop modules."""
    tree = _parse(code)
    if not tree:
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.While):
            continue
        if any(isinstance(n, ast.Break) for n in ast.walk(node)):
            continue
        test = node.test
        if isinstance(test, ast.Constant) and bool(test.value):
            return False
        cond_names = {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
        if cond_names:
            modified = set()
            for n in ast.walk(node):
                if isinstance(n, ast.Assign):
                    modified |= {t.id for t in n.targets if isinstance(t, ast.Name)}
                elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                    modified.add(n.target.id)
            if not (cond_names & modified):
                return False
    return True


def has_list(code: str) -> bool:
    tree = _parse(code)
    if not tree:
        return False
    return any(isinstance(n, ast.List) for n in ast.walk(tree))


def has_list_operation(code: str) -> bool:
    """append/index/len on a list - evidence the learner actually used one."""
    tree = _parse(code)
    if not tree:
        return False
    if any(isinstance(n, ast.Subscript) for n in ast.walk(tree)):
        return True
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {"append", "sort", "pop"}:
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "len":
            return True
    return False


def has_dict(code: str) -> bool:
    tree = _parse(code)
    if not tree:
        return False
    return any(isinstance(n, ast.Dict) for n in ast.walk(tree)) or bool(_calls_named(tree, "dict"))


def has_dict_access(code: str) -> bool:
    tree = _parse(code)
    if not tree:
        return False
    return any(isinstance(n, ast.Subscript) for n in ast.walk(tree)) or any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {"get", "keys", "values", "items"}
        for n in ast.walk(tree)
    )


def has_function_def(code: str) -> bool:
    tree = _parse(code)
    return bool(tree) and any(isinstance(n, ast.FunctionDef) for n in ast.walk(tree))


def has_function_call(code: str) -> bool:
    """A user-defined function is both declared AND called."""
    tree = _parse(code)
    if not tree:
        return False
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    if not defined:
        return False
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return bool(defined & called)


def has_try_except(code: str) -> bool:
    tree = _parse(code)
    return bool(tree) and any(isinstance(n, ast.Try) for n in ast.walk(tree))


def _all(*checks: Check) -> Check:
    def combined(code: str) -> bool:
        return all(c(code) for c in checks)
    return combined


# ---- module definitions -----------------------------------------------------

MODULE_ORDER: List[str] = [
    "printing", "variables", "input", "data_types", "conditions",
    "for_loops", "while_loops", "lists", "dictionaries", "functions",
    "debugging", "mini_project",
]

MODULES: Dict[str, Dict] = {
    "printing": {
        "id": "printing", "order": 1, "title": "Printing and output",
        "concept": "A print statement makes your program display a message when it runs. "
                   "It's how your code talks back to you.",
        "example_code": 'print("Hello, CodeUp!")',
        "instructions": "Write a print statement that displays a short greeting, then run it.",
        "hints": ["Use the word print, then parentheses, then your message in quotes.",
                   "Example: print(\"Hello\")"],
        "attempt_check": has_print,
        "success": "Nicely done - you made Python speak using a print statement.",
        "challenge": "Now print two different messages, one after another, on separate lines.",
        "challenge_check": lambda code: _parse(code) is not None and len(_calls_named(_parse(code), "print")) >= 2,
        "quiz_question": "What does print(\"Hi\") do?",
        "quiz_choices": ["Saves a file named Hi", "Displays the text Hi when the program runs",
                          "Deletes the variable Hi", "Asks the user to type Hi"],
        "quiz_answer_index": 1,
    },
    "variables": {
        "id": "variables", "order": 2, "title": "Variables",
        "concept": "A variable is a named box that stores information so your program can use it "
                   "later. You give the box a name and put a value inside with =.",
        "example_code": 'name = "Amir"\nprint(name)',
        "instructions": "Create a variable with your name (or a made-up one) and print it.",
        "hints": ["Give a name to a value with equals, like x = 5.",
                   "Then print the variable by its name, not in quotes: print(x)."],
        "attempt_check": _all(has_assignment, has_print),
        "success": "You stored a value in a variable and printed it back - that's the core of programming.",
        "challenge": "Create two variables and print a message that uses both of them.",
        "challenge_check": lambda code: len(_assigned_names(code)) >= 2 and has_print(code),
        "quiz_question": "What best describes a variable?",
        "quiz_choices": ["A fixed number that never changes", "A named box that stores a value",
                          "A type of loop", "A kind of error message"],
        "quiz_answer_index": 1,
    },
    "input": {
        "id": "input", "order": 3, "title": "User input",
        "concept": "input() pauses your program and waits for the user to type something, then "
                   "gives you back what they typed as text.",
        "example_code": 'name = input("What is your name? ")\nprint("Hello, " + name)',
        "instructions": "Ask the user for their name with input(), then greet them with print().",
        "hints": ["input(\"your question\") returns what the user types.",
                   "Store the result in a variable, then print something using it."],
        "attempt_check": _all(has_input, has_print),
        "success": "Your program listened to the user and responded - that's interactive code.",
        "challenge": "Ask for two pieces of information and print a message that uses both.",
        "challenge_check": lambda code: (_parse(code) is not None and len(_calls_named(_parse(code), "input")) >= 2 and has_print(code)),
        "quiz_question": "What does input() give back to your program?",
        "quiz_choices": ["Nothing, it only prints", "Whatever the user typed, as text",
                          "A random number", "An error message"],
        "quiz_answer_index": 1,
    },
    "data_types": {
        "id": "data_types", "order": 4, "title": "Data types",
        "concept": "Every value in Python has a type - text (str), whole numbers (int), decimals "
                   "(float), or true/false (bool). input() always gives text, so you convert it "
                   "with int(...) or float(...) when you need a number.",
        "example_code": 'age_text = input("Age? ")\nage = int(age_text)\nprint(age + 1)',
        "instructions": "Ask for a number with input(), convert it with int() or float(), then use it in a calculation.",
        "hints": ["input() always returns text, even if the user types a number.",
                   "Wrap it: int(input(\"...\")) turns that text into a whole number."],
        "attempt_check": _all(has_input, has_type_conversion),
        "success": "You converted text into a real number so Python could do maths with it.",
        "challenge": "Convert two different inputs to numbers and print their sum.",
        "challenge_check": lambda code: has_input(code) and has_type_conversion(code) and has_print(code),
        "quiz_question": "Why do you need int(input(...)) instead of just input(...) for a number?",
        "quiz_choices": ["You never need to", "input() always returns text, so it must be converted to do maths",
                          "int() makes the program run faster", "input() only works with int()"],
        "quiz_answer_index": 1,
    },
    "conditions": {
        "id": "conditions", "order": 5, "title": "Conditions (if / else)",
        "concept": "An if statement lets your program make a decision. Python checks whether "
                   "something is true, and only then runs the indented action underneath it.",
        "example_code": 'age = 12\nif age >= 10:\n    print("You can vote in the mock election")\nelse:\n    print("Not yet")',
        "instructions": "Write an if/else that checks a number and prints different messages depending on the result.",
        "hints": ["End the if line with a colon, then indent the line underneath by 4 spaces.",
                   "Add else: for the other case."],
        "attempt_check": _all(has_if_else, has_print),
        "success": "Your program made a decision and responded differently depending on the condition.",
        "challenge": "Add a second condition using elif to handle a third case.",
        "challenge_check": lambda code: has_if_else(code) and "elif" in (code or ""),
        "quiz_question": "What happens if the condition in an if statement is False and there's no else?",
        "quiz_choices": ["Python crashes", "The indented block underneath is simply skipped",
                          "Python runs the block anyway", "The program restarts"],
        "quiz_answer_index": 1,
    },
    "for_loops": {
        "id": "for_loops", "order": 6, "title": "For loops",
        "concept": "A for loop repeats an action a known number of times, once for each item in a "
                   "sequence like range(5) or a list.",
        "example_code": 'for i in range(3):\n    print(i)',
        "instructions": "Write a for loop with range(...) that prints something each time it repeats.",
        "hints": ["for i in range(5): repeats 5 times, with i being 0, 1, 2, 3, 4.",
                   "Indent the line you want repeated underneath the for line."],
        "attempt_check": _all(has_for, has_print),
        "success": "Your for loop repeated the action and printed each time - no copy-pasting needed.",
        "challenge": "Use a for loop to print the numbers 1 to 10, using range with a start and stop.",
        "challenge_check": lambda code: has_for(code) and "range(1" in (code or "").replace(" ", ""),
        "quiz_question": "What does range(3) produce for a for loop?",
        "quiz_choices": ["The numbers 1, 2, 3", "The numbers 0, 1, 2", "Just the number 3", "An error"],
        "quiz_answer_index": 1,
    },
    "while_loops": {
        "id": "while_loops", "order": 7, "title": "While loops",
        "concept": "A while loop repeats an action while a condition stays true. Because the "
                   "condition could stay true forever, it must change something each time so it "
                   "can eventually stop.",
        "example_code": 'count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1',
        "instructions": "Write a while loop with a counter that starts, repeats, and safely stops.",
        "hints": ["Start a counter variable before the loop.",
                   "Inside the loop, change the counter so the condition eventually becomes false."],
        "attempt_check": lambda code: has_while(code) and while_is_safe(code) and has_print(code),
        "success": "Your while loop counted safely and stopped - the most important habit for while loops.",
        "challenge": "Write a while loop that counts DOWN to zero instead of up.",
        "challenge_check": lambda code: has_while(code) and while_is_safe(code) and ("-= " in (code or "") or "- 1" in (code or "")),
        "quiz_question": "Why must a while loop change its condition variable inside the loop?",
        "quiz_choices": ["It doesn't need to", "So the loop eventually stops instead of running forever",
                          "To make the code shorter", "Python requires it for syntax reasons"],
        "quiz_answer_index": 1,
    },
    "lists": {
        "id": "lists", "order": 8, "title": "Lists",
        "concept": "A list stores many values together in order, like [10, 20, 30]. You can add to "
                   "it, look up an item by position, or loop over every item.",
        "example_code": 'marks = [78, 91, 85]\nfor m in marks:\n    print(m)',
        "instructions": "Create a list of a few values, then use or loop over it.",
        "hints": ["Square brackets make a list: names = [\"Amir\", \"Priya\"].",
                   "names[0] gets the first item; a for loop can visit every item."],
        "attempt_check": _all(has_list, has_list_operation),
        "success": "You stored several values together in a list and worked with them.",
        "challenge": "Add a new item to your list with .append(...) and print the updated list.",
        "challenge_check": lambda code: has_list(code) and ".append(" in (code or ""),
        "quiz_question": "What does marks[0] refer to in a list?",
        "quiz_choices": ["The last item", "The first item", "The number of items", "An error"],
        "quiz_answer_index": 1,
    },
    "dictionaries": {
        "id": "dictionaries", "order": 9, "title": "Dictionaries",
        "concept": "A dictionary stores key-value pairs, like a real dictionary maps a word to its "
                   "meaning. You look up a value using its key instead of a position.",
        "example_code": 'marks = {"Amir": 78, "Priya": 91}\nprint(marks["Amir"])',
        "instructions": "Create a dictionary with a couple of key-value pairs, then look one up.",
        "hints": ["Curly braces with key: value pairs make a dictionary.",
                   "Look up a value with square brackets and the key: marks[\"Amir\"]."],
        "attempt_check": _all(has_dict, has_dict_access),
        "success": "You stored and looked up information by name instead of position - that's a dictionary.",
        "challenge": "Loop over your dictionary's keys and print each key with its value.",
        "challenge_check": lambda code: has_dict(code) and has_for(code) and (".items(" in (code or "") or ".keys(" in (code or "")),
        "quiz_question": "What does a Python dictionary store?",
        "quiz_choices": ["Only numbers, in order", "Key-value pairs", "Only functions", "Nothing, it's just a comment"],
        "quiz_answer_index": 1,
    },
    "functions": {
        "id": "functions", "order": 10, "title": "Functions",
        "concept": "A function is a named, reusable block of code. You define it once with def, "
                   "then call it by name whenever you need it, as many times as you like.",
        "example_code": 'def greet(name):\n    print("Hello, " + name)\n\ngreet("Amir")',
        "instructions": "Define a small function that does something useful, then call it.",
        "hints": ["def my_function(): starts a function; indent the lines it should run.",
                   "Calling it is just its name with parentheses: my_function()."],
        "attempt_check": has_function_call,
        "success": "You packaged code into a reusable function and called it - a big step toward real programs.",
        "challenge": "Give your function a parameter and call it twice with two different values.",
        "challenge_check": lambda code: has_function_call(code) and (_parse(code) is not None and any(
            isinstance(n, ast.FunctionDef) and n.args.args for n in ast.walk(_parse(code)))),
        "quiz_question": "Why use a function instead of writing the same code twice?",
        "quiz_choices": ["Functions run faster automatically", "You can reuse the same logic by calling it instead of repeating it",
                          "Python requires at least one function", "Functions prevent all errors"],
        "quiz_answer_index": 1,
    },
    "debugging": {
        "id": "debugging", "order": 11, "title": "Basic debugging",
        "concept": "Errors are normal. Python's error messages (tracebacks) tell you what went "
                   "wrong and on which line - reading them is the fastest way to fix a bug.",
        "example_code": 'total = 0\nfor n in [1, 2, 3]:\n    total = total + n\nprint(total)',
        "instructions": "Here is a program with one small bug (a NameError or an indentation "
                        "problem). Run it, read the error message, then fix it so it runs cleanly.",
        "hints": ["The last line of an error message usually names the problem, like NameError or IndentationError.",
                   "The line number in the error tells you exactly where to look."],
        "attempt_check": lambda code: _parse(code) is not None and has_print(code),
        "success": "You read an error message and fixed the bug yourself - that's real debugging.",
        "challenge": "Deliberately write a small bug, run it to see the error, then fix it.",
        "challenge_check": lambda code: has_print(code) and has_try_except(code),
        "quiz_question": "What is the most useful part of a Python error message?",
        "quiz_choices": ["The color of the text", "The exception type and line number, which show what and where",
                          "The length of the message", "The time it took to run"],
        "quiz_answer_index": 1,
    },
    "mini_project": {
        "id": "mini_project", "order": 12, "title": "Combine concepts: mini project",
        "concept": "Real programs combine several concepts together. Let's use input, variables, "
                   "a list or dictionary, and a loop or function in one small program.",
        "example_code": (
            'marks = {}\n'
            'name = input("Student name: ")\n'
            'score = int(input("Score: "))\n'
            'marks[name] = score\n'
            'for student, mark in marks.items():\n'
            '    print(student, mark)'
        ),
        "instructions": "Build a tiny program that asks for input, stores it in a list or "
                        "dictionary, and prints a summary using a loop.",
        "hints": ["Reuse what you already built in earlier modules - input, a dictionary or list, and a loop.",
                   "Start small: get one piece of input working, then add the loop."],
        "attempt_check": lambda code: has_input(code) and (has_list(code) or has_dict(code)) and (has_for(code) or has_while(code)),
        "success": "You combined input, data storage, and a loop into one working program - that's a real project.",
        "challenge": "Extend it to handle more than one entry, using a loop around the input step too.",
        "challenge_check": lambda code: has_input(code) and (has_list(code) or has_dict(code)) and has_for(code) and has_print(code),
        "quiz_question": "What's the benefit of combining concepts like input, a dictionary, and a loop?",
        "quiz_choices": ["It's required by Python", "Real programs need multiple concepts working together",
                          "It makes the code run faster", "It avoids all errors"],
        "quiz_answer_index": 1,
    },
}


def _assigned_names(code: str) -> set:
    tree = _parse(code)
    names = set()
    if not tree:
        return names
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
    return names


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


def public_module(module_id: str) -> Optional[Dict]:
    """The JSON-safe view of a module (no function objects)."""
    m = MODULES.get(module_id)
    if not m:
        return None
    return {
        "id": m["id"], "order": m["order"], "title": m["title"], "concept": m["concept"],
        "example_code": m["example_code"], "instructions": m["instructions"], "hints": m["hints"],
        "challenge": m["challenge"], "quiz_question": m.get("quiz_question"),
        "quiz_choices": m.get("quiz_choices") or [],
    }


def list_modules() -> List[Dict]:
    return [public_module(mid) for mid in MODULE_ORDER]


def check_attempt(module_id: str, code: str) -> Dict:
    module = MODULES.get(module_id)
    if not module:
        return {"passed": False, "feedback": "That module is not part of the course."}
    if _parse(code) is None:
        return {
            "passed": False,
            "feedback": "There's a small typo in your code, so Python could not read it. "
                        "Listen to the error, then fix and run again.",
        }
    if module_id == "while_loops" and not while_is_safe(code):
        return {
            "passed": False,
            "feedback": "That while loop has no way to stop, so it would run forever. "
                        "Make sure a counter changes each time inside the loop.",
        }
    passed = bool(module["attempt_check"](code))
    return {
        "passed": passed,
        "feedback": module["success"] if passed else (
            "Not quite there yet - " + module["instructions"]
        ),
    }


def check_challenge(module_id: str, code: str) -> Dict:
    module = MODULES.get(module_id)
    if not module or _parse(code) is None:
        return {"passed": False, "feedback": "Run your code first so there's something to check."}
    passed = bool(module["challenge_check"](code))
    return {
        "passed": passed,
        "feedback": "Challenge complete." if passed else "Not quite - " + module["challenge"],
    }


def check_quiz(module_id: str, choice_index: int) -> Tuple[bool, str]:
    module = MODULES.get(module_id)
    if not module or module.get("quiz_answer_index") is None:
        return False, "This module has no quiz."
    correct = int(choice_index) == int(module["quiz_answer_index"])
    return correct, ("Correct." if correct else "Not quite - review the concept and try again.")
