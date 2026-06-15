"""Deterministic beginner code templates for CodeUp voice commands.

The functions in this module return safe, small Python examples only. They do
not call AI and they do not execute code.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class TemplateResult:
    intent: str
    edit_action: str = "append_code"
    code: str = ""
    speech: str = ""
    confidence: float = 0.92
    needs_clarification: bool = False
    reason: str = ""


_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

_PY_KEYWORDS = set((
    "False None True and as assert async await break class continue def del elif "
    "else except finally for from global if import in is lambda nonlocal not or "
    "pass raise return try while with yield"
).split())


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def _number(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _norm(str(value))
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return _NUMBER_WORDS.get(text)


def _safe_identifier(value: str, default: str = "value") -> str:
    candidate = re.sub(r"[^0-9a-zA-Z_]+", "_", str(value or "").strip().lower()).strip("_")
    if not candidate or candidate[0].isdigit() or candidate in _PY_KEYWORDS:
        return default
    return candidate


def _quote(value: str) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _friendly_text(text: str) -> str:
    raw = " ".join(str(text or "").strip().strip(".").split())
    if not raw:
        return "Hello"
    if raw.lower() == "codeup":
        return "CodeUp"
    if raw.lower() == "welcome to codeup":
        return "Welcome to CodeUp"
    if raw.lower() in {"hello", "hi"}:
        return raw.capitalize()
    if raw.lower() == "hello world":
        return "Hello world"
    return raw[0].upper() + raw[1:]


def _range_call(start: int, stop: int, step: int = 1) -> str:
    if step == 1 and start == 0:
        return f"range({stop})"
    if step == 1:
        return f"range({start}, {stop})"
    return f"range({start}, {stop}, {step})"


def _compile_ok(code: str) -> bool:
    try:
        compile(code, "<beginner_template>", "exec")
    except SyntaxError:
        return False
    return True


def _clarify(message: str, *, intent: str = "unknown_clarify", reason: str = "clarify") -> TemplateResult:
    return TemplateResult(
        intent=intent,
        edit_action="clarify",
        speech=message,
        needs_clarification=True,
        reason=reason,
        confidence=0.86,
    )


def make_print_template(text: str = "Hello") -> str:
    return f"print({_quote(_friendly_text(text))})"


def make_variable_template(kind: str = "name", *, name: Optional[str] = None, value: Any = None) -> str:
    k = _norm(kind or name or "name")
    if name:
        var_name = _safe_identifier(name, "value")
        literal = _literal_for_value(value if value is not None else "Taknoor")
        return f"{var_name} = {literal}\nprint({var_name})"
    if "mark" in k:
        return "marks = 85\nprint(marks)"
    if "age" in k:
        return "age = 16\nprint(age)"
    if "score" in k:
        return "score = 10\nprint(score)"
    return 'name = "Taknoor"\nprint(name)'


def make_input_template(kind: str = "name") -> str:
    k = _norm(kind)
    if "mark" in k or "number" in k or "score" in k:
        return 'marks = int(input("Enter your marks: "))\nprint("Your marks are", marks)'
    return 'name = input("Enter your name: ")\nprint("Hello", name)'


def make_if_template(kind: str = "marks") -> str:
    k = _norm(kind)
    if "age" in k or "adult" in k:
        return 'age = 18\n\nif age >= 18:\n    print("Adult")\nelse:\n    print("Not adult yet")'
    if "positive" in k or "number" in k:
        return 'number = 5\n\nif number > 0:\n    print("Positive")\nelse:\n    print("Not positive")'
    return 'marks = 75\n\nif marks >= 40:\n    print("Pass")\nelse:\n    print("Needs practice")'


def make_for_loop_template(
    start: int = 0,
    stop: int = 3,
    step: int = 1,
    *,
    variable: str = "i",
    body: Optional[str] = None,
) -> str:
    var = _safe_identifier(variable, "i")
    step = step or 1
    body_line = body or f"print({var})"
    body_line = body_line.strip()
    return f"for {var} in {_range_call(start, stop, step)}:\n    {body_line}"


def make_while_loop_template(
    start: int = 0,
    stop: int = 3,
    step: int = 1,
    *,
    variable: str = "count",
    inclusive_stop: bool = False,
) -> str:
    var = _safe_identifier(variable, "count")
    if step == 0:
        step = 1
    if step > 0:
        op = "<=" if inclusive_stop else "<"
    else:
        op = ">=" if inclusive_stop else ">"
    return (
        f"{var} = {start}\n\n"
        f"while {var} {op} {stop}:\n"
        f"    print({var})\n"
        f"    {var} = {var} {'+' if step > 0 else '-'} {abs(step)}"
    )


def make_list_template(kind: str = "fruits", *, loop: bool = False) -> str:
    k = _norm(kind)
    if "number" in k:
        if loop:
            return "numbers = [1, 2, 3]\n\nfor number in numbers:\n    print(number)"
        return "numbers = [1, 2, 3]\nprint(numbers)"
    if loop:
        return 'fruits = ["apple", "banana", "mango"]\n\nfor fruit in fruits:\n    print(fruit)'
    return 'fruits = ["apple", "banana", "mango"]\nprint(fruits)'


def make_function_template(kind: str = "greet", *, name: Optional[str] = None) -> str:
    function_name = _safe_identifier(name or kind or "greet", "greet")
    if function_name in {"function", "example", "program"}:
        function_name = "greet"
    return f'def {function_name}(name):\n    print("Hello", name)\n\n{function_name}("Taknoor")'


def add_comments_to_code(code: str) -> str:
    source = str(code or "").strip("\n")
    if not source.strip():
        return ""
    lines = source.splitlines()
    output = []
    previous_comment = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            output.append(line)
            previous_comment = False
            continue
        if stripped.startswith("#"):
            output.append(line)
            previous_comment = True
            continue
        comment = _comment_for_line(stripped)
        indent = line[: len(line) - len(line.lstrip())]
        if comment and not previous_comment:
            output.append(f"{indent}# {comment}")
        output.append(line)
        previous_comment = False
    return "\n".join(output)


def simplify_code(code: str) -> str:
    source = str(code or "").strip("\n")
    if not source.strip():
        return ""
    simplified = re.sub(r"range\(\s*0\s*,\s*(\d+)\s*,\s*1\s*\)", r"range(\1)", source)
    simplified = re.sub(r"range\(\s*0\s*,\s*(\d+)\s*\)", r"range(\1)", simplified)
    simplified = re.sub(r'print\("Hello "\s*\+\s*([A-Za-z_]\w*)\)', r'print("Hello", \1)', simplified)
    simplified = re.sub(r"\n{3,}", "\n\n", simplified)
    return simplified.strip("\n")


def convert_for_to_while(code: str) -> str:
    source = str(code or "").strip("\n")
    if not source.strip():
        return ""
    match = re.search(
        r"(?m)^(?P<indent>[ \t]*)for\s+(?P<var>[A-Za-z_]\w*)\s+in\s+range\((?P<args>[^)]*)\):\n(?P<body>(?:(?P=indent)[ \t]+.+(?:\n|$))+)",
        source,
    )
    if not match:
        return source
    parsed = _parse_range_args(match.group("args"))
    if not parsed:
        return source
    start, stop, step = parsed
    inclusive_stop = stop - 1 if step > 0 else stop + 1
    while_stop = inclusive_stop if start != 0 or step != 1 else stop
    while_code = make_while_loop_template(
        start,
        while_stop,
        step,
        variable="count",
        inclusive_stop=(start != 0 or step != 1),
    )
    body = _dedent_loop_body(match.group("body"), match.group("indent"))
    if body:
        converted_body = _replace_word(body[0].strip(), match.group("var"), "count")
        while_lines = while_code.splitlines()
        while_lines[3] = f"    {converted_body}"
        while_code = "\n".join(while_lines)
    return source[: match.start()] + while_code + source[match.end() :].strip("\n")


def convert_while_to_for(code: str) -> str:
    source = str(code or "").strip("\n")
    if not source.strip():
        return ""
    pattern = re.compile(
        r"(?ms)^\s*(?P<var>[A-Za-z_]\w*)\s*=\s*(?P<start>-?\d+)\s*\n\s*\n?"
        r"\s*while\s+(?P=var)\s*(?P<op><=|<|>=|>)\s*(?P<stop>-?\d+)\s*:\s*\n"
        r"(?P<body>(?:[ \t]+.+\n?)+?)"
        r"[ \t]*(?P=var)\s*=\s*(?P=var)\s*(?P<sign>\+|-)\s*(?P<step>\d+)\s*$"
    )
    match = pattern.match(source)
    if not match:
        return source
    start = int(match.group("start"))
    stop = int(match.group("stop"))
    step = int(match.group("step")) * (1 if match.group("sign") == "+" else -1)
    op = match.group("op")
    if op in {"<=", ">="}:
        stop = stop + (1 if step > 0 else -1)
    body_lines = [
        line.strip()
        for line in match.group("body").splitlines()
        if line.strip() and " = " not in line.strip()
    ]
    body = _replace_word(body_lines[0], match.group("var"), "i") if body_lines else "print(i)"
    return make_for_loop_template(start, stop, step, variable="i", body=body)


def build_from_mapping(intent: str, slots: Optional[Dict[str, Any]] = None, *, current_code: str = "") -> Optional[TemplateResult]:
    slots = dict(slots or {})
    intent = str(intent or "")
    if intent == "insert_beginner_loop":
        code = make_for_loop_template()
        return TemplateResult(intent, code=code, speech="Inserted a simple for loop that prints 0, 1, and 2.")
    if intent == "insert_print_statement":
        text = str(slots.get("text") or slots.get("value") or "Hello")
        code = make_print_template(text)
        return TemplateResult(intent, code=code, speech="Inserted a print statement.")
    if intent == "insert_variable_example":
        code = make_variable_template(str(slots.get("kind") or slots.get("name") or "name"), name=slots.get("name"), value=slots.get("value"))
        return TemplateResult(intent, code=code, speech="Inserted a beginner variable example.")
    if intent == "insert_input_example":
        code = make_input_template(str(slots.get("kind") or "name"))
        return TemplateResult(intent, code=code, speech="Inserted a beginner input example.")
    if intent == "insert_if_statement":
        code = make_if_template(str(slots.get("kind") or "marks"))
        return TemplateResult(intent, code=code, speech="Inserted a beginner if statement.")
    if intent == "insert_for_loop":
        result = _loop_from_slots(slots, while_loop=False)
        if result:
            return result
        return TemplateResult(intent, code=make_for_loop_template(), speech="Inserted a simple for loop that prints 0, 1, and 2.")
    if intent == "insert_while_loop":
        result = _loop_from_slots(slots, while_loop=True)
        if result:
            return result
        return TemplateResult(intent, code=make_while_loop_template(), speech="Inserted a safe while loop that stops after three steps.")
    if intent == "insert_list_example":
        code = make_list_template(str(slots.get("kind") or "fruits"), loop=bool(slots.get("loop")))
        return TemplateResult(intent, code=code, speech="Inserted a beginner list example.")
    if intent == "insert_function_example":
        code = make_function_template(str(slots.get("kind") or "greet"), name=slots.get("name"))
        return TemplateResult(intent, code=code, speech="Inserted a beginner function example.")
    if intent == "add_comments":
        if not str(current_code or "").strip():
            return _clarify("There is no code to comment yet. Insert code first, then say add comments.", intent=intent, reason="no_code")
        code = add_comments_to_code(current_code)
        return TemplateResult(intent, edit_action="replace_code", code=code, speech="I added comments to the current code.")
    if intent == "simplify_current_code":
        if not str(current_code or "").strip():
            return _clarify("There is no code to simplify yet. Insert code first, then say simplify this code.", intent=intent, reason="no_code")
        code = simplify_code(current_code)
        return TemplateResult(intent, edit_action="replace_code", code=code, speech="I simplified the current code.")
    if intent == "convert_loop_type":
        if not str(current_code or "").strip():
            return _clarify("There is no loop to convert yet. Insert a loop first.", intent=intent, reason="no_code")
        target = _norm(str(slots.get("to") or slots.get("target") or ""))
        if "while" in target:
            code = convert_for_to_while(current_code)
            if code == str(current_code or "").strip("\n"):
                return _clarify("I could not find a simple for loop to convert safely.", intent=intent, reason="no_simple_for_loop")
            return TemplateResult(intent, edit_action="replace_code", code=code, speech="Changed the simple for loop to a safe while loop.")
        if "for" in target:
            code = convert_while_to_for(current_code)
            if code == str(current_code or "").strip("\n"):
                return _clarify("I could not find a simple while loop to convert safely.", intent=intent, reason="no_simple_while_loop")
            return TemplateResult(intent, edit_action="replace_code", code=code, speech="Changed the simple while loop to a for loop.")
        return _clarify("Should I change the loop to a for loop or a while loop?", intent=intent, reason="missing_target")
    return None


def match_template_command(text: str, *, current_code: str = "") -> Optional[TemplateResult]:
    raw = " ".join(str(text or "").split())
    low = _norm(raw)
    if not low:
        return None
    if re.search(r"\b(?:then|and then|after that)\b", low):
        return None

    unsafe_loop = _unsafe_loop_request(low)
    if unsafe_loop:
        return unsafe_loop

    transform = _match_transform(low, current_code)
    if transform:
        return transform

    loop = _match_loop(low)
    if loop:
        return loop

    if re.match(r"^(?:insert|add|put|write|type)\s+(?:a\s+)?print(?:\s+line|\s+statement)?\b", low):
        return None
    if re.search(r"\b(?:make|create|add|insert|put|write)\b.*\bprint\s+(?:statement|line|example)\b", low):
        text_match = re.search(r"\b(?:that\s+says|says?|saying)\s+(.+)$", raw, re.IGNORECASE)
        if not text_match:
            text_match = re.search(r"\bprints?\s+(.+)$", raw, re.IGNORECASE)
        content = text_match.group(1) if text_match else "Hello"
        code = make_print_template(content)
        return TemplateResult("insert_print_statement", code=code, speech="Inserted a print statement.")

    if re.search(r"\b(?:variable\s+example|example\s+variable)\b", low):
        kind = _kind_from_text(low, ("marks", "age", "score", "name"))
        return TemplateResult("insert_variable_example", code=make_variable_template(kind), speech="Inserted a beginner variable example.")

    if re.search(r"\binput\s+example\b|\bask\s+(?:the\s+)?user\s+for\b", low):
        kind = _kind_from_text(low, ("marks", "score", "number", "name"))
        return TemplateResult("insert_input_example", code=make_input_template(kind), speech="Inserted a beginner input example.")

    if re.match(r"^insert\s+an?\s+if\s+statement\s+checking\b", low):
        return None
    if re.search(r"\bif\s+(?:statement|example)\b|\b(?:make|create|insert|add)\b.*\bif\b", low):
        kind = _kind_from_text(low, ("age", "adult", "positive", "number", "marks"))
        return TemplateResult("insert_if_statement", code=make_if_template(kind), speech="Inserted a beginner if statement.")

    if re.search(r"\b(?:list\s+example|example\s+list|fruits?\s+list)\b", low):
        loop = bool(re.search(r"\b(?:loop|print\s+each|each\s+fruit)\b", low))
        kind = "numbers" if "number" in low else "fruits"
        return TemplateResult("insert_list_example", code=make_list_template(kind, loop=loop), speech="Inserted a beginner list example.")

    if re.search(r"\b(?:function\s+example|example\s+function)\b", low):
        name = "greet" if re.search(r"\bgreet|hello\b", low) else None
        return TemplateResult("insert_function_example", code=make_function_template(name=name), speech="Inserted a beginner function example.")

    return None


def _literal_for_value(value: Any) -> str:
    number = _number(value)
    if number is not None:
        return str(number)
    text = str(value if value is not None else "Taknoor")
    if _norm(text) in {"true", "false"}:
        return _norm(text).capitalize()
    return _quote(text)


def _comment_for_line(stripped: str) -> str:
    if stripped.startswith("for "):
        return "Loop through the values"
    if stripped.startswith("while "):
        return "Repeat while the condition is true"
    if stripped.startswith("if "):
        return "Check a condition"
    if stripped.startswith("else"):
        return "Handle the other case"
    if stripped.startswith("def "):
        return "Define a reusable function"
    if stripped.startswith("print("):
        return "Show a result"
    if "input(" in stripped:
        return "Ask the user for a value"
    if re.match(r"^[A-Za-z_]\w*\s*=", stripped):
        return "Store a value"
    return ""


def _parse_range_args(args: str) -> Optional[Tuple[int, int, int]]:
    parts = [part.strip() for part in str(args or "").split(",")]
    try:
        values = [int(part) for part in parts if part]
    except ValueError:
        return None
    if len(values) == 1:
        return 0, values[0], 1
    if len(values) == 2:
        return values[0], values[1], 1
    if len(values) == 3 and values[2] != 0:
        return values[0], values[1], values[2]
    return None


def _dedent_loop_body(body: str, parent_indent: str) -> Iterable[str]:
    prefix = parent_indent + "    "
    lines = []
    for line in str(body or "").splitlines():
        if line.startswith(prefix):
            lines.append(line[len(prefix) :])
        else:
            lines.append(line.strip())
    return lines


def _replace_word(text: str, old: str, new: str) -> str:
    return re.sub(rf"\b{re.escape(old)}\b", new, text)


def _kind_from_text(text: str, choices: Iterable[str]) -> str:
    for choice in choices:
        if choice in text:
            return choice
    return next(iter(choices), "name")


def _unsafe_loop_request(low: str) -> Optional[TemplateResult]:
    if re.search(r"\b(?:while\s+true|infinite\s+loop|forever\s+loop|loop\s+forever|never\s+stop)\b", low):
        msg = "I will not insert an infinite loop. Say safe while loop to insert one that stops."
        return _clarify(msg, intent="insert_while_loop", reason="unsafe_infinite_loop")
    return None


def _match_transform(low: str, current_code: str) -> Optional[TemplateResult]:
    if re.search(r"\b(?:arey|karo|banao|tak|se|har|wala|naam|hai|ko|ke|mein|dikhe|kare)\b", low):
        return None
    if re.search(r"\badd\s+comments?\b|\bcomment\s+(?:this|my|the)\s+code\b", low):
        return build_from_mapping("add_comments", current_code=current_code)
    if re.search(r"\bsimplify\s+(?:this|my|the)?\s*code\b|\bmake\s+(?:this|it)\s+simpler\b", low):
        return build_from_mapping("simplify_current_code", current_code=current_code)
    if re.search(r"\b(?:change|convert|turn)\s+(?:it|this|the\s+loop)?\s*(?:into|to)\s+(?:a\s+)?while\s+loop\b", low):
        return build_from_mapping("convert_loop_type", {"to": "while"}, current_code=current_code)
    if re.search(r"\b(?:change|convert|turn)\s+(?:it|this|the\s+loop)?\s*(?:into|to)\s+(?:a\s+)?for\s+loop\b", low):
        return build_from_mapping("convert_loop_type", {"to": "for"}, current_code=current_code)
    return None


def _match_loop(low: str) -> Optional[TemplateResult]:
    if re.search(r"\b(?:lesson|report|trainer|handoff|story|tutorial|class|snippet|times)\b", low):
        return None
    if re.search(r"\b(?:trends|prince|friends|of\s+for\s+loop)\b", low):
        return None
    if re.search(r"\b(?:karo|banao|tak|se|har|wala|naam|hai|ko|ke|mein|dikhe|kare)\b", low):
        return None
    if re.search(r"\b(?:bookmark|bookmarks?|what|which|where|inside|line|control|controls|start)\b", low):
        return None
    if re.search(r"\bwhile\s+count\b|\b(?:less|greater)\s+than\b", low):
        return None
    if not re.search(
        r"^(?:please\s+|hey\s+|ok\s+|okay\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)*"
        r"(?:(?:insert|add|put|write|make|create|build|generate|give\s+me)\b.*\b(?:for\s+|while\s+)?loop\b|"
        r"(?:for\s+|while\s+)?loop\b|print\s+numbers?\b)",
        low,
    ):
        return None
    if re.search(r"\bfruits?\b", low) and re.search(r"\b(?:loop|each|list)\b", low):
        return TemplateResult(
            "insert_for_loop",
            code=make_list_template("fruits", loop=True),
            speech="Inserted a fruits list and a loop that prints each fruit.",
        )
    if not re.search(r"\b(?:loop|for\s+loop|while\s+loop|print\s+numbers?)\b", low):
        return None
    if "while" in low:
        bounds = _inclusive_bounds(low)
        if bounds:
            start, end = bounds
            code = make_while_loop_template(start, end, 1, inclusive_stop=True)
            return TemplateResult("insert_while_loop", code=code, speech=f"Inserted a safe while loop from {start} to {end}.")
        return TemplateResult(
            "insert_while_loop",
            code=make_while_loop_template(),
            speech="Inserted a safe while loop that stops after three steps.",
        )
    result = _for_loop_from_text(low)
    if result:
        return result
    if re.fullmatch(r"(?:insert|add|make|create|put|write)?\s*(?:a\s+)?(?:simple\s+)?(?:for\s+)?loop(?:\s+in\s+the\s+editor)?", low):
        return TemplateResult("insert_for_loop", code=make_for_loop_template(), speech="Inserted a simple for loop that prints 0, 1, and 2.")
    return _clarify(
        'I heard a loop command but not clearly. Say "insert a simple loop" or "loop from 1 to 5".',
        intent="insert_for_loop",
        reason="unclear_loop_command",
    )


def _for_loop_from_text(low: str) -> Optional[TemplateResult]:
    if re.search(r"\b(?:even|evens)\b", low):
        end = _number_after_to(low) or 10
        code = make_for_loop_template(2, end + 1, 2)
        return TemplateResult("insert_for_loop", code=code, speech=f"Inserted a for loop that prints even numbers up to {end}.")
    if re.search(r"\b(?:odd|odds)\b", low):
        end = _number_after_to(low) or 9
        code = make_for_loop_template(1, end + 1, 2)
        return TemplateResult("insert_for_loop", code=code, speech=f"Inserted a for loop that prints odd numbers up to {end}.")
    if re.search(r"\b(?:0\s+1\s+2|zero\s+one\s+two|first\s+(?:3|three)|(?:0|zero)\s+(?:to|through)\s+(?:2|two))\b", low):
        return TemplateResult("insert_for_loop", code=make_for_loop_template(), speech="Inserted a for loop that prints 0, 1, and 2.")
    count = _number_count(low)
    if count is not None:
        code = make_for_loop_template(0, count)
        if count == 3:
            speech = "Inserted a for loop that prints 0, 1, and 2."
        else:
            speech = f"Inserted a for loop that prints {count} numbers."
        return TemplateResult("insert_for_loop", code=code, speech=speech)
    bounds = _inclusive_bounds(low)
    if bounds:
        start, end = bounds
        step = 1 if end >= start else -1
        stop = end + step
        code = make_for_loop_template(start, stop, step)
        return TemplateResult("insert_for_loop", code=code, speech=f"Inserted a for loop from {start} to {end}.")
    return None


def _inclusive_bounds(low: str) -> Optional[Tuple[int, int]]:
    match = re.search(r"\b(?:from\s+)?([a-z0-9-]+)\s+(?:to|through|up\s+to)\s+([a-z0-9-]+)\b", low)
    if not match:
        return None
    start = _number(match.group(1))
    end = _number(match.group(2))
    if start is None or end is None:
        return None
    if abs(end - start) > 100:
        return None
    return start, end


def _number_after_to(low: str) -> Optional[int]:
    match = re.search(r"\b(?:to|through|up\s+to)\s+([a-z0-9-]+)\b", low)
    if not match:
        return None
    return _number(match.group(1))


def _number_count(low: str) -> Optional[int]:
    match = re.search(r"\b(?:first\s+)?([a-z0-9-]+)\s+(?:whole\s+|natural\s+|counting\s+)?numbers?\b", low)
    if not match:
        return None
    count = _number(match.group(1))
    if count is None or count <= 0 or count > 100:
        return None
    return count


def _loop_from_slots(slots: Dict[str, Any], *, while_loop: bool) -> Optional[TemplateResult]:
    kind = _norm(str(slots.get("kind") or slots.get("output") or ""))
    if slots.get("collection") == "fruits" or kind == "fruits":
        return TemplateResult("insert_for_loop", code=make_list_template("fruits", loop=True), speech="Inserted a fruits list and loop.")
    if kind in {"even", "even_numbers"}:
        end = _number(slots.get("stop")) or 10
        code = make_while_loop_template(2, end, 2, inclusive_stop=True) if while_loop else make_for_loop_template(2, end + 1, 2)
        return TemplateResult("insert_while_loop" if while_loop else "insert_for_loop", code=code, speech="Inserted an even-number loop.")
    if kind in {"odd", "odd_numbers"}:
        end = _number(slots.get("stop")) or 9
        code = make_while_loop_template(1, end, 2, inclusive_stop=True) if while_loop else make_for_loop_template(1, end + 1, 2)
        return TemplateResult("insert_while_loop" if while_loop else "insert_for_loop", code=code, speech="Inserted an odd-number loop.")
    start = _number(slots.get("start"))
    stop = _number(slots.get("stop"))
    step = _number(slots.get("step")) or 1
    if start is None and stop is None:
        return None
    if start is None:
        start = 0
    if stop is None:
        stop = 3
    if abs(stop - start) > 100 or step == 0:
        return _clarify("That loop range is too large or unsafe. Try a small beginner loop.", intent="insert_while_loop" if while_loop else "insert_for_loop", reason="unsafe_loop_bounds")
    if while_loop:
        return TemplateResult("insert_while_loop", code=make_while_loop_template(start, stop, step, inclusive_stop=True), speech="Inserted a safe while loop.")
    return TemplateResult("insert_for_loop", code=make_for_loop_template(start, stop + (1 if step > 0 else -1), step), speech="Inserted a for loop.")


def validate_template_code(code: str) -> bool:
    """Small helper for tests and callers that need a final syntax check."""
    source = str(code or "")
    if not source.strip():
        return False
    if not _compile_ok(source):
        return False
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True
