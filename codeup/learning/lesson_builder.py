
import re
from typing import Any, Dict, Tuple

SUPPORTED = ["print", "variables", "if statements", "for loops", "while loops",
             "functions", "lists", "dictionaries", "beginner debugging"]

_UNSUPPORTED_MESSAGE = ("I can build lessons for print, variables, if statements, loops, "
                        "functions, lists, dictionaries, and beginner debugging.")

_ALIASES = {
    "print": "print", "printing": "print", "print statements": "print", "output": "print",
    "variable": "variables", "variables": "variables",
    "if": "if_statements", "if statement": "if_statements", "if statements": "if_statements",
    "condition": "if_statements", "conditions": "if_statements", "conditionals": "if_statements",
    "for": "for_loops", "for loop": "for_loops", "for loops": "for_loops",
    "loop": "for_loops", "loops": "for_loops",
    "while": "while_loops", "while loop": "while_loops", "while loops": "while_loops",
    "function": "functions", "functions": "functions", "def": "functions",
    "list": "lists", "lists": "lists",
    "dict": "dictionaries", "dictionary": "dictionaries", "dictionaries": "dictionaries",
    "debug": "debug_indentation", "debugging": "debug_indentation",
    "indentation": "debug_indentation", "debugging indentation": "debug_indentation",
}

_LESSONS: Dict[str, Dict[str, Any]] = {
    "print": {
        "title": "Printing output", "concept": "print",
        "explanation": "The print statement shows a message on the screen.",
        "command": "insert print hello world",
        "practice": "Make it print your own name instead of hello world.",
        "small_hint": "Whatever is inside the quotes is what gets printed.",
        "bigger_hint": "Change the words inside the quotes to your name.",
        "solution": 'print("hello world")',
        "trainer_note": "Listen for whether the learner understands that the quotes mark text, not a command.",
    },
    "variables": {
        "title": "Variables", "concept": "variables",
        "explanation": "A variable is a named box that stores a value so you can use it later.",
        "command": "insert a variable named count with the value 1",
        "practice": "Make a variable named age and give it your age, then print it.",
        "small_hint": "A variable is written as a name, an equals sign, then a value.",
        "bigger_hint": "Write age = 16, then on the next line write print(age).",
        "solution": "count = 1\nprint(count)",
        "trainer_note": "Listen for whether the learner separates naming the box from putting a value in it.",
    },
    "if_statements": {
        "title": "If statements", "concept": "if statements",
        "explanation": "An if statement runs some lines only when a condition is true.",
        "command": "insert if count is greater than zero",
        "practice": "Add an indented print that says positive when count is greater than zero.",
        "small_hint": "The line after the colon must be indented to belong to the if.",
        "bigger_hint": "Under the if line, indent a print like print(\"positive\").",
        "solution": "if count > 0:\n    print(\"positive\")",
        "trainer_note": "Listen for whether the learner understands the indented block runs only when the test is true.",
    },
    "for_loops": {
        "title": "For loops", "concept": "for loops",
        "explanation": "A for loop repeats an action a known number of times.",
        "command": "put a loop from zero to two that prints each number in the editor",
        "practice": "Change the loop so it prints five numbers.",
        "small_hint": "range controls how many times the loop runs.",
        "bigger_hint": "change range 3 to range 5.",
        "solution": "for i in range(5):\n    print(i)",
        "trainer_note": "Listen for whether the learner understands that the indented line is the repeated action.",
    },
    "while_loops": {
        "title": "While loops", "concept": "while loops",
        "explanation": "A while loop repeats an action while a condition stays true.",
        "command": "insert while count is less than or equal to three",
        "practice": "Make the loop count from one up to three and print each value.",
        "small_hint": "Something inside the loop must change, or it never stops.",
        "bigger_hint": "Add count = count + 1 as an indented line inside the loop.",
        "solution": "count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1",
        "trainer_note": "Listen for whether the learner sees why the counter must change inside the loop.",
    },
    "functions": {
        "title": "Functions", "concept": "functions",
        "explanation": "A function groups steps under a name so you can reuse them.",
        "command": "write a function that adds two numbers and returns the result",
        "practice": "Call your function with two numbers and print the answer.",
        "small_hint": "A function starts with def, a name, and brackets for its inputs.",
        "bigger_hint": "Write def add(a, b): then an indented return a + b.",
        "solution": "def add(a, b):\n    return a + b\n\nprint(add(2, 3))",
        "trainer_note": "Listen for whether the learner separates defining the function from calling it.",
    },
    "lists": {
        "title": "Lists", "concept": "lists",
        "explanation": "A list holds several values in order under one name.",
        "command": "write a program that makes a list of three numbers and prints it",
        "practice": "Add a fourth number to the list and print the list again.",
        "small_hint": "A list is written inside square brackets with commas between values.",
        "bigger_hint": "Write numbers = [1, 2, 3], then print(numbers).",
        "solution": "numbers = [1, 2, 3]\nprint(numbers)",
        "trainer_note": "Listen for whether the learner understands the values keep their order.",
    },
    "dictionaries": {
        "title": "Dictionaries", "concept": "dictionaries",
        "explanation": "A dictionary stores pairs of keys and values, like a labelled box.",
        "command": "write a program that makes a dictionary with a name and age and prints it",
        "practice": "Add a third key and value to the dictionary and print it.",
        "small_hint": "A dictionary uses curly braces with key colon value pairs.",
        "bigger_hint": 'Write person = {"name": "Asha", "age": 16}, then print(person).',
        "solution": 'person = {"name": "Asha", "age": 16}\nprint(person)',
        "trainer_note": "Listen for whether the learner connects each key to its value.",
    },
    "debug_indentation": {
        "title": "Debugging indentation", "concept": "indentation",
        "explanation": "Indentation, the spaces at the start of a line, tells Python which block a line belongs to.",
        "command": "debug this like a teacher",
        "practice": "Write a loop, remove the indentation before the print, run it, then fix it.",
        "small_hint": "The line under a loop or if must be indented to be inside it.",
        "bigger_hint": "Add four spaces before the line that should run inside the block.",
        "solution": "for i in range(3):\n    print(i)",
        "trainer_note": "Listen for whether the learner can tell an indentation error from a logic error.",
    },
}


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def normalize_topic(topic: str) -> str:
    t = _norm(topic)
    if t in _ALIASES:
        return _ALIASES[t]
    for phrase, key in _ALIASES.items():
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            return key
    return ""


def extract_topic(text: str) -> str:
    t = _norm(text)
    m = re.search(r"\b(?:on|for|about|in)\s+(.+)$", t)
    topic = m.group(1).strip() if m else t
    topic = re.sub(r"\b(beginner|accessible|practice|python|short|simple)\b", "", topic).strip()
    return topic


def _speech_solution(solution: str) -> str:
    return " ".join(line.strip() for line in solution.splitlines() if line.strip())


def _render_lesson(lesson: Dict[str, Any], verbosity: str) -> Tuple[str, str]:
    display_solution = lesson["solution"].rstrip()
    sections = [
        (f"Lesson: {lesson['title']}.", f"Lesson: {lesson['title']}."),
        (f"Explanation: {lesson['explanation']}", f"Explanation: {lesson['explanation']}"),
        (f"Command to try: {lesson['command']}.", f"Command to try: {lesson['command']}."),
        (f"Practice task: {lesson['practice']}", f"Practice task: {lesson['practice']}"),
        (f"Small hint: {lesson['small_hint']}", f"Small hint: {lesson['small_hint']}"),
        (f"Bigger hint: {lesson['bigger_hint']}", f"Bigger hint: {lesson['bigger_hint']}"),
        (f"Expected solution:\n{display_solution}",
         f"Expected solution: {_speech_solution(lesson['solution'])}"),
        (f"Trainer note: {lesson['trainer_note']}", f"Trainer note: {lesson['trainer_note']}"),
    ]
    TRAINER_NOTE = 7
    if verbosity == "concise":
        display_keep = [0, 1, 2, 6]
    elif verbosity == "expert":
        display_keep = [0, 1, 2, 6, 7]
    else:
        display_keep = list(range(len(sections)))
    speech_keep = [i for i in display_keep if i != TRAINER_NOTE]
    message = "\n".join(sections[i][0] for i in display_keep)
    speech = " ".join(sections[i][1] for i in speech_keep)
    return message, speech


def build_accessible_lesson(topic: str, verbosity: str = "normal") -> Dict[str, Any]:
    verbosity = (verbosity or "normal").strip().lower()
    key = normalize_topic(topic)
    if not key or key not in _LESSONS:
        return {"action": "deterministic_message", "message": _UNSUPPORTED_MESSAGE,
                "speech": _UNSUPPORTED_MESSAGE, "supported": list(SUPPORTED), "topic": ""}

    lesson = _LESSONS[key]
    message, speech = _render_lesson(lesson, verbosity)
    return {
        "action": "deterministic_message", "message": message, "speech": speech,
        "topic": key, "concept": lesson["concept"], "explanation": lesson["explanation"],
        "command": lesson["command"], "practice": lesson["practice"],
        "hints": [lesson["small_hint"], lesson["bigger_hint"]],
        "solution": lesson["solution"], "trainer_note": lesson["trainer_note"],
    }
