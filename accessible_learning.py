"""Deterministic accessibility and beginner-learning command packs for CodeUp."""

from __future__ import annotations

import ast
import csv
import io
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _lesson(
    title: str,
    goal: str,
    explanation: str,
    code: str,
    task: str,
    hint: str,
    next_step: str,
    check: str,
) -> Dict[str, str]:
    return {
        "title": title,
        "goal": goal,
        "explanation": explanation,
        "code": code,
        "task": task,
        "hint": hint,
        "next": next_step,
        "check": check,
    }


LESSONS = [
    _lesson(
        "Hello world and spoken output",
        "Print a message.",
        "Print sends text to CodeUp's output, where it can also be spoken.",
        'print("Hello, CodeUp!")\n',
        "Change the message, then run it.",
        "Text inside quotes is a string.",
        "Say check lesson, then next lesson.",
        "print",
    ),
    _lesson(
        "Variables and values",
        "Store a number and print it.",
        "A variable gives a value a useful name.",
        "score = 10\nprint(score)\n",
        "Change score and print the new value.",
        "Use name equals value.",
        "Say check lesson after running it.",
        "assignment_print",
    ),
    _lesson(
        "Strings and numbers",
        "Use both text and numeric values.",
        "Strings hold text; integers and floats hold numbers.",
        'name = "Asha"\nage = 12\nprint(name, age)\n',
        "Change both values and print them.",
        "Text needs quotes; numbers do not.",
        "Say check lesson after running it.",
        "string_number",
    ),
    _lesson(
        "Conditions",
        "Choose output with if and else.",
        "A condition runs one branch when its test is true.",
        'score = 70\nif score >= 50:\n    print("Pass")\nelse:\n    print("Try again")\n',
        "Change score and run both branches.",
        "Keep the branch lines indented.",
        "Say check lesson when both branches remain.",
        "condition",
    ),
    _lesson(
        "Loops",
        "Repeat an action.",
        "A loop runs its indented body more than once.",
        "for number in range(3):\n    print(number)\n",
        "Change the range and run it.",
        "A for loop ends its header with a colon.",
        "Say check lesson after it prints values.",
        "loop",
    ),
    _lesson(
        "Lists",
        "Store several values in one list.",
        "A list keeps ordered values between square brackets.",
        "scores = [70, 80, 90]\nprint(scores[0])\n",
        "Add one score and print it.",
        "List positions start at zero.",
        "Say check lesson after using the list.",
        "list",
    ),
    _lesson(
        "Functions",
        "Define and call a function.",
        "A function names a reusable block of code.",
        'def greet(name):\n    return "Hello " + name\n\nprint(greet("Asha"))\n',
        "Change the argument and call the function.",
        "Defining a function does not run it; call it below.",
        "Say check lesson after the function is called.",
        "function",
    ),
    _lesson(
        "Debugging errors",
        "Fix a syntax error.",
        "Read the error type and line, then make one small fix.",
        'message = "Hello\nprint(message)\n',
        "Close the missing quote and run the code.",
        "The first line starts a string but does not end it.",
        "Say check lesson after the program runs.",
        "valid",
    ),
    _lesson(
        "Step narration and tracing",
        "Prepare code for step narration.",
        "Tracing explains execution one step at a time.",
        "total = 0\nfor number in range(3):\n    total += number\nprint(total)\n",
        "Run with step narration and follow total.",
        "Listen for total changing inside the loop.",
        "Say check lesson when the code has changing state.",
        "trace",
    ),
    _lesson(
        "Multi-file project basics",
        "Use a main entry point and a local import.",
        "Larger projects split related code into named files.",
        'from helpers import greet\n\nprint(greet("Asha"))\n',
        "Create helpers.py with a greet function in project mode.",
        "The imported name and function name must match.",
        "Say check lesson with both files in the project.",
        "multifile",
    ),
    _lesson(
        "Screen reader workflow",
        "Use clear names and a simple structure.",
        "Short functions and descriptive names make code easier to navigate by speech.",
        "def calculate_total(scores):\n    return sum(scores)\n\nprint(calculate_total([70, 80]))\n",
        "Use code map, then go to definition of calculate_total.",
        "Navigate by function name instead of counting lines.",
        "Say check lesson after keeping a named function.",
        "screen_reader",
    ),
    _lesson(
        "Moving toward VS Code",
        "Prepare a project handoff.",
        "CodeUp is a beginner bridge; VS Code is a useful next editor for larger projects.",
        'def main():\n    print("Ready to export")\n\nif __name__ == "__main__":\n    main()\n',
        "Run the entry point, then export for VS Code.",
        "Keep main small and explicit.",
        "Say check lesson, then export for VS Code.",
        "handoff",
    ),
]


BLOCK_EXERCISES = [
    ("Print hello world", [(1, 'print("Hello world")', 0)]),
    (
        "Add two numbers",
        [
            (1, "first = 2", 0),
            (2, "second = 3", 0),
            (3, "total = first + second", 0),
            (4, "print(total)", 0),
        ],
    ),
    (
        "Total numbers in a loop",
        [
            (1, "total = 0", 0),
            (2, "for number in [1, 2, 3]:", 0),
            (3, "total += number", 1),
            (4, "print(total)", 0),
        ],
    ),
    (
        "If or else pass or fail",
        [
            (1, "score = 70", 0),
            (2, "if score >= 50:", 0),
            (3, 'print("Pass")', 1),
            (4, "else:", 0),
            (5, 'print("Fail")', 1),
        ],
    ),
    (
        "Function that returns a value",
        [
            (1, "def double(number):", 0),
            (2, "return number * 2", 1),
            (3, "result = double(4)", 0),
            (4, "print(result)", 0),
        ],
    ),
    (
        "List average",
        [
            (1, "scores = [70, 80, 90]", 0),
            (2, "total = sum(scores)", 0),
            (3, "average = total / len(scores)", 0),
            (4, "print(average)", 0),
        ],
    ),
]


ERROR_CHALLENGES = [
    {
        "name": "missing colon",
        "kind": "syntax",
        "code": "for number in range(3)\n    print(number)\n",
        "fixed": "for number in range(3):\n    print(number)\n",
        "hint": "The loop header needs punctuation at the end.",
        "output": "0\n1\n2",
    },
    {
        "name": "bad indentation",
        "kind": "indentation",
        "code": 'if True:\nprint("Ready")\n',
        "fixed": 'if True:\n    print("Ready")\n',
        "hint": "The print line belongs inside the if block.",
        "output": "Ready",
    },
    {
        "name": "undefined variable",
        "kind": "name",
        "code": "score = 70\nprint(scores)\n",
        "fixed": "score = 70\nprint(score)\n",
        "hint": "Compare the name you stored with the name you print.",
        "output": "70",
    },
    {
        "name": "string plus number",
        "kind": "type",
        "code": 'age = 12\nprint("Age: " + age)\n',
        "fixed": 'age = 12\nprint("Age:", age)\n',
        "hint": "Print can accept text and a number as separate arguments.",
        "output": "Age: 12",
    },
    {
        "name": "missing closing quote",
        "kind": "syntax",
        "code": 'message = "Hello\nprint(message)\n',
        "fixed": 'message = "Hello"\nprint(message)\n',
        "hint": "The string on line one needs an ending quote.",
        "output": "Hello",
    },
    {
        "name": "wrong function call name",
        "kind": "name",
        "code": 'def greet():\n    print("Hello")\n\ngreeting()\n',
        "fixed": 'def greet():\n    print("Hello")\n\ngreet()\n',
        "hint": "The call name must match the definition name.",
        "output": "Hello",
    },
]


SHORTCUTS = (
    "Alt Shift R: run. Alt Shift H: command help. Alt Shift E: read errors. "
    "Alt Shift M: code map. Alt Shift T: run with step narration. Alt Shift S: stop. "
    "Alt Shift A: toggle screen reader mode. Alt Shift K: shortcut help. "
    "Alt Shift N: toggle navigation mode."
)


def _message(text: str, **extra: Any) -> Dict[str, Any]:
    return {
        "success": True,
        "action": "deterministic_message",
        "message": text,
        "speech": text,
        **extra,
    }


def _edit(code: str, speech: str, source: str) -> Dict[str, Any]:
    return {
        "success": True,
        "action": "conversational_edit",
        "message": speech,
        "speech": speech,
        "ai_action": {
            "action": "replace_code",
            "target": {"line_number": None, "position": ""},
            "code": code,
            "spoken_confirmation": speech,
            "confidence": 1.0,
            "requires_confirmation": False,
            "source": source,
        },
    }


def _tree(code: str) -> Optional[ast.AST]:
    try:
        return ast.parse(code or "")
    except (SyntaxError, ValueError, TypeError):
        return None


def _calls(tree: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
        for n in ast.walk(tree)
    )


def _lesson_passes(lesson: Dict[str, str], code: str, project: Dict[str, Any]) -> bool:
    tree = _tree(code)
    check = lesson["check"]
    if check == "valid":
        return tree is not None and bool(str(code).strip())
    if tree is None:
        return False
    nodes = list(ast.walk(tree))
    if check == "print":
        return _calls(tree, "print")
    if check == "assignment_print":
        return any(
            isinstance(n, (ast.Assign, ast.AnnAssign)) for n in nodes
        ) and _calls(tree, "print")
    if check == "string_number":
        return (
            any(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in nodes)
            and any(
                isinstance(n, ast.Constant)
                and isinstance(n.value, (int, float))
                and not isinstance(n.value, bool)
                for n in nodes
            )
            and _calls(tree, "print")
        )
    if check == "condition":
        return any(isinstance(n, ast.If) and n.orelse for n in nodes) and _calls(
            tree, "print"
        )
    if check == "loop":
        return any(isinstance(n, (ast.For, ast.While)) for n in nodes) and _calls(
            tree, "print"
        )
    if check == "list":
        return any(isinstance(n, (ast.List, ast.ListComp)) for n in nodes) and _calls(
            tree, "print"
        )
    if check == "function":
        defs = {n.name for n in nodes if isinstance(n, ast.FunctionDef)}
        return bool(defs) and any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in defs
            for n in nodes
        )
    if check == "trace":
        return any(isinstance(n, (ast.For, ast.While)) for n in nodes) and any(
            isinstance(n, (ast.Assign, ast.AugAssign)) for n in nodes
        )
    if check == "multifile":
        files = project.get("files") if isinstance(project.get("files"), dict) else {}
        return len(files) >= 2 and any(
            isinstance(n, (ast.Import, ast.ImportFrom)) for n in nodes
        )
    if check == "screen_reader":
        return any(isinstance(n, ast.FunctionDef) and len(n.name) > 2 for n in nodes)
    if check == "handoff":
        return any(
            isinstance(n, ast.FunctionDef) and n.name == "main" for n in nodes
        ) and _calls(tree, "main")
    return False


def _lesson_response(mem: Dict[str, Any], *, load: bool) -> Dict[str, Any]:
    state = mem.setdefault(
        "learning_path", {"index": 0, "attempted": [], "passed": [], "failed": []}
    )
    idx = max(0, min(int(state.get("index", 0)), len(LESSONS) - 1))
    state["index"] = idx
    if idx not in state.setdefault("attempted", []):
        state["attempted"].append(idx)
    lesson = LESSONS[idx]
    speech = (
        f"Lesson {idx + 1}: {lesson['title']}. Your goal is to {lesson['goal'].lower()}"
    )
    if load:
        speech += " I loaded starter code. Run it, then say check lesson."
        return _edit(lesson["code"], speech, "learning_path") | {"lesson": idx + 1}
    return _message(
        f"{speech} {lesson['explanation']} Task: {lesson['task']} {lesson['next']}",
        lesson=idx + 1,
    )


def _start_blocks(mem: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    index = max(0, min(index, len(BLOCK_EXERCISES) - 1))
    title, expected = BLOCK_EXERCISES[index]
    order = [dict(id=i, text=text, indent=indent) for i, text, indent in expected]
    if len(order) > 2:
        order[1], order[2] = order[2], order[1]
    mem["block_practice"] = {"index": index, "blocks": order, "attempts": 0}
    return _message(
        f"Block practice {index + 1}: {title}. {len(order)} blocks loaded. Say read block order.",
        block_practice=True,
    )


def _block_order(state: Dict[str, Any]) -> str:
    blocks = state.get("blocks") or []
    return " ".join(
        f"Block {b['id']}, indentation {b['indent']}: {b['text']}." for b in blocks
    )


def _csv_rows(
    project: Dict[str, Any],
) -> Tuple[str, List[str], List[Dict[str, str]], str]:
    files = project.get("files") if isinstance(project.get("files"), dict) else {}
    path = next((p for p in sorted(files) if str(p).lower().endswith(".csv")), "")
    if not path:
        return "", [], [], "No CSV file is available in the current project."
    try:
        reader = csv.DictReader(io.StringIO(str(files[path])))
        columns = list(reader.fieldnames or [])
        rows = []
        for row in reader:
            rows.append({str(k): str(v or "") for k, v in row.items() if k is not None})
            if len(rows) >= 500:
                break
        if not columns:
            return path, [], [], f"{path} does not have a header row."
        return path, columns, rows, ""
    except (csv.Error, UnicodeError, TypeError) as exc:
        return path, [], [], f"Could not read {path}: {type(exc).__name__}."


def _numbers(rows: Iterable[Dict[str, str]], column: str) -> Tuple[List[float], int]:
    values, missing = [], 0
    for row in rows:
        raw = str(row.get(column, "")).strip()
        if not raw:
            missing += 1
            continue
        try:
            values.append(float(raw))
        except ValueError:
            pass
    return values, missing


def _find_column(columns: List[str], requested: str) -> str:
    low = requested.lower().strip()
    return next((c for c in columns if c.lower() == low), "")


def _data_command(t: str, project: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    is_data_command = (
        t
        in {
            "summarize csv",
            "describe csv",
            "list csv columns",
            "describe chart",
            "make chart accessible",
            "read chart as text",
            "sonify data",
            "stop sonification",
        }
        or bool(re.match(r"^read csv row \d+$", t))
        or bool(re.match(r"^(?:find )?(?:highest|lowest)(?: value)? in [\w -]+$", t))
        or bool(re.match(r"^average(?: of)? [\w -]+$", t))
        or bool(re.match(r"^compare columns [\w -]+ and [\w -]+$", t))
        or bool(re.match(r"^sonify column [\w -]+$", t))
    )
    if not is_data_command:
        return None
    if t == "stop sonification":
        return {
            "success": True,
            "action": "stop_data_sonification",
            "message": "Sonification stopped.",
            "speech": "Sonification stopped.",
        }
    path, columns, rows, error = _csv_rows(project)
    if error:
        return _message(error)
    if t in {"list csv columns", "describe csv"}:
        missing = sum(
            sum(1 for v in row.values() if not str(v).strip()) for row in rows
        )
        return _message(
            f"{path} has {len(rows)} rows and {len(columns)} columns: {', '.join(columns)}. Missing values: {missing}."
        )
    if t == "summarize csv":
        numeric = [
            c
            for c in columns
            if _numbers(rows, c)[0]
            and len(_numbers(rows, c)[0]) >= max(1, len(rows) // 2)
        ]
        first = (
            ", ".join(f"{c} {rows[0].get(c, '')}" for c in columns[:4])
            if rows
            else "no data rows"
        )
        return _message(
            f"{path} has {len(rows)} rows. Columns: {', '.join(columns)}. Numeric columns: {', '.join(numeric) or 'none'}. First row: {first}."
        )
    m = re.match(r"read csv row (\d+)$", t)
    if m:
        n = int(m.group(1))
        if n < 1 or n > len(rows):
            return _message(
                f"Row {n} is not available. This file has {len(rows)} data rows."
            )
        return _message(
            f"Row {n}: "
            + ", ".join(f"{c} {rows[n - 1].get(c, '')}" for c in columns)
            + "."
        )
    m = re.match(r"(?:find )?(highest|lowest)(?: value)? in ([\w -]+)$", t)
    if m:
        col = _find_column(columns, m.group(2))
        values = _numbers(rows, col)[0] if col else []
        if not col:
            return _message(
                f"Column {m.group(2)} was not found. Available columns: {', '.join(columns)}."
            )
        if not values:
            return _message(f"Column {col} does not contain numeric values.")
        value = max(values) if m.group(1) == "highest" else min(values)
        return _message(f"The {m.group(1)} value in {col} is {value:g}.")
    m = re.match(r"average(?: of)? ([\w -]+)$", t)
    if m:
        col = _find_column(columns, m.group(1))
        values = _numbers(rows, col)[0] if col else []
        if not col:
            return _message(
                f"Column {m.group(1)} was not found. Available columns: {', '.join(columns)}."
            )
        if not values:
            return _message(f"Column {col} does not contain numeric values.")
        return _message(
            f"The average of {col} is {sum(values) / len(values):g}, using {len(values)} numeric values."
        )
    m = re.match(r"compare columns ([\w -]+) and ([\w -]+)$", t)
    if m:
        a, b = _find_column(columns, m.group(1)), _find_column(columns, m.group(2))
        if not a or not b:
            return _message(
                f"Both columns must exist. Available columns: {', '.join(columns)}."
            )
        av, bv = _numbers(rows, a)[0], _numbers(rows, b)[0]
        if not av or not bv:
            return _message("Both selected columns need numeric values.")
        return _message(
            f"{a} averages {sum(av) / len(av):g} with range {min(av):g} to {max(av):g}. {b} averages {sum(bv) / len(bv):g} with range {min(bv):g} to {max(bv):g}. This compares averages and ranges; it does not infer causation."
        )
    m = re.match(r"sonify(?: data| column(?: ([\w -]+))?)$", t)
    if m:
        requested = (m.group(1) or "").strip()
        col = (
            _find_column(columns, requested)
            if requested
            else next((c for c in columns if _numbers(rows, c)[0]), "")
        )
        values = _numbers(rows, col)[0] if col else []
        if not values:
            return _message("No numeric CSV column is available to sonify.")
        values = values[:30]
        speech = f"Sonifying {len(values)} values from {col}. Higher values use higher pitches."
        return {
            "success": True,
            "action": "accessible_data_sonify",
            "values": values,
            "column": col,
            "message": speech,
            "speech": speech,
        }
    if t in {"describe chart", "make chart accessible", "read chart as text"}:
        col = next((c for c in columns if _numbers(rows, c)[0]), "")
        if not col:
            return _message(
                "A chart description needs at least one numeric CSV column."
            )
        values = _numbers(rows, col)[0]
        trend = (
            "rises overall"
            if values[-1] > values[0]
            else "falls overall"
            if values[-1] < values[0]
            else "starts and ends at the same value"
        )
        return _message(
            f"Accessible chart description: a simple line chart of {col} by row, with values from {min(values):g} to {max(values):g}. The series {trend}. Highest {max(values):g}; lowest {min(values):g}. This is a text description, not a generated visual chart."
        )
    return None


def _style_issues(code: str) -> List[str]:
    tree = _tree(code)
    if tree is None:
        return ["Fix the syntax error before checking style."]
    issues: List[str] = []
    parents: Dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    bad_builtins = {"list", "dict", "str", "int", "sum", "input", "print"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if len(node.id) == 1 and node.id not in {"i", "j", "k"}:
                issues.append(
                    f"Line {node.lineno}: give variable {node.id} a more descriptive name."
                )
            elif node.id in bad_builtins:
                issues.append(
                    f"Line {node.lineno}: {node.id} hides a Python built-in name."
                )
        if isinstance(node, ast.FunctionDef):
            end = getattr(node, "end_lineno", node.lineno)
            if end - node.lineno + 1 > 30:
                issues.append(
                    f"Function {node.name} is over 30 lines; consider smaller steps."
                )
            calls = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == node.name
            ]
            if not calls:
                issues.append(f"Function {node.name} is defined but not called.")
        if isinstance(node, ast.Compare) and any(
            isinstance(c, ast.Constant) and isinstance(c.value, bool)
            for c in node.comparators
        ):
            issues.append(
                f"Line {node.lineno}: a direct boolean check is clearer than comparing with True or False."
            )
    for n, line in enumerate((code or "").splitlines(), 1):
        if len(line) > 88:
            issues.append(
                f"Line {n} is {len(line)} characters; a shorter line may be easier to hear."
            )

    def depth(node: ast.AST, current: int = 0) -> int:
        extra = int(isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With)))
        return max(
            [current + extra]
            + [depth(c, current + extra) for c in ast.iter_child_nodes(node)]
        )

    if depth(tree) > 3:
        issues.append(
            "The code nests more than 3 levels; small helper functions may be easier to follow."
        )
    return list(dict.fromkeys(issues))


def _teacher_report(mem: Dict[str, Any], storage: Dict[str, Any], code: str) -> str:
    lp = mem.get("learning_path") if isinstance(mem.get("learning_path"), dict) else {}
    command_count = max(0, int(mem.get("command_count") or 0))
    command_types = (
        mem.get("command_type_counts")
        if isinstance(mem.get("command_type_counts"), dict)
        else {}
    )
    errors = (
        mem.get("error_type_counts")
        if isinstance(mem.get("error_type_counts"), dict)
        else {}
    )
    current = int(lp.get("index", 0)) + 1 if lp else 0
    lines = [
        "# CodeUp Teacher Report",
        "",
        "## Learning progress",
        f"- Lessons attempted: {len(lp.get('attempted', []))}",
        f"- Current lesson: {current or 'Not started'}",
        f"- Lesson checks passed: {len(lp.get('passed', []))}",
        f"- Lesson checks failed: {len(lp.get('failed', []))}",
        "",
        "## Activity",
        f"- Commands used: {command_count}",
        "- Command types: "
        + (
            ", ".join(
                f"{name} {count}" for name, count in sorted(command_types.items())
            )
            or "None"
        ),
        f"- Run count: {int(mem.get('run_count') or 0)}",
        f"- Hints requested: {int(mem.get('hints_requested') or 0)}",
        f"- Block practice attempts: {int(mem.get('block_attempts') or 0)}",
        f"- Project export used: {'Yes' if 'exported the project' in mem.get('features_used', []) else 'No'}",
        "",
        "## Recent result",
        f"- Last output: {str(mem.get('last_run_output') or 'None')[:240]}",
        f"- Last error: {str(mem.get('last_run_error') or 'None')[:240]}",
        "",
        "## Common mistakes",
    ]
    for name in (
        "SyntaxError",
        "IndentationError",
        "NameError",
        "TypeError",
        "ImportError",
        "BlockedImport",
    ):
        lines.append(f"- {name}: {int(errors.get(name, 0))}")
    lines += [
        "",
        "## Accessibility",
        f"- Screen reader mode: {'On' if storage.get('screen_reader_mode') else 'Off'}",
        f"- Accessibility profile: {storage.get('screen_reader_profile') or 'default'}",
        "",
        "## Privacy",
        "This report is session-based and local. It excludes full code by default.",
    ]
    if mem.get("teacher_include_code"):
        secret_markers = ("api_key", "secret", "token", "password", "private key")
        safe_code_lines = []
        for line in str(code or "")[:5000].splitlines():
            if any(marker in line.lower() for marker in secret_markers):
                safe_code_lines.append("# Redacted possible secret")
            else:
                safe_code_lines.append(line)
        lines += ["", "## Current code", "```python", "\n".join(safe_code_lines), "```"]
    return "\n".join(lines) + "\n"


def record_runtime_error(mem: Dict[str, Any], error: str) -> None:
    text = str(error or "")
    if not text:
        return
    counts = mem.setdefault("error_type_counts", {})
    kind = next(
        (
            name
            for name in (
                "IndentationError",
                "SyntaxError",
                "NameError",
                "TypeError",
                "ImportError",
            )
            if name in text
        ),
        "",
    )
    if not kind and "blocked" in text.lower() and "import" in text.lower():
        kind = "BlockedImport"
    if kind:
        counts[kind] = int(counts.get(kind, 0)) + 1


def route_command(
    text: str,
    code: str,
    mem: Dict[str, Any],
    storage: Dict[str, Any],
    project: Optional[Dict[str, Any]] = None,
    cursor_line: Optional[int] = None,
    error: str = "",
) -> Optional[Dict[str, Any]]:
    t = " ".join(str(text or "").lower().strip().rstrip(".!?").split())
    project = project or {}
    if not t:
        return None

    learning_commands = {
        "start learning path",
        "start python path",
        "continue learning path",
        "next lesson",
        "previous lesson",
        "repeat lesson",
        "where am i in the learning path",
        "skip lesson",
        "reset learning path",
        "list lessons",
        "check lesson",
        "give lesson hint",
        "show lesson goal",
    }
    block_prefixes = (
        "start block practice",
        "start parsons practice",
        "read block order",
        "move block ",
        "indent block ",
        "outdent block ",
        "read block ",
        "check block order",
        "convert blocks to code",
        "reset block practice",
        "exit block practice",
    )
    hint_commands = {
        "give me a small hint",
        "give me a bigger hint",
        "give me the next hint",
        "repeat hint",
        "hide hints",
        "why is this hint useful",
        "show solution steps",
        "stop hints",
    }
    other_markers = (
        "keyboard shortcuts",
        "navigation mode",
        "next symbol",
        "previous symbol",
        "next loop",
        "previous loop",
        "next error",
        "previous error",
        "next todo",
        "previous todo",
        "read current scope",
        "teacher mode",
        "teacher report",
        "student report",
        "lesson report",
        "mistakes report",
        "common mistakes",
        "beginner style",
        "readable names",
        "function length",
        "too much nesting",
        "confusing names",
        "style issues",
        "error practice",
        "accessible coding tools",
        "explain quorum",
        "codeup different from quorum",
        "vscode handoff",
        "accessible coding pathway",
    )
    data = _data_command(t, project)
    error_commands = {
        "read error challenge",
        "check error fix",
        "give error hint",
        "show error solution",
        "next error challenge",
        "exit error practice",
    }
    recognized = (
        t in learning_commands
        or t.startswith(block_prefixes)
        or t in hint_commands
        or any(m in t for m in other_markers)
        or bool(re.match(r"^practice (?:indentation|name|type|syntax) errors$", t))
        or t in error_commands
        or data is not None
    )
    if not recognized:
        return None
    history = mem.setdefault("accessible_command_history", [])
    history.append(t)
    del history[:-100]
    record_runtime_error(mem, error or str(mem.get("last_run_error") or ""))
    if data is not None:
        return data

    if t in {"start learning path", "start python path"}:
        mem["learning_path"] = {"index": 0, "attempted": [], "passed": [], "failed": []}
        return _lesson_response(mem, load=True)
    if t == "reset learning path":
        mem["learning_path"] = {"index": 0, "attempted": [], "passed": [], "failed": []}
        return _message(
            "Learning path reset. Say start learning path when you are ready."
        )
    if t == "list lessons":
        return _message(
            "The 12 lessons are: "
            + "; ".join(f"{i + 1}, {x['title']}" for i, x in enumerate(LESSONS))
            + "."
        )
    if t in learning_commands:
        state = mem.setdefault(
            "learning_path", {"index": 0, "attempted": [], "passed": [], "failed": []}
        )
        if t in {"next lesson", "skip lesson"}:
            state["index"] = min(len(LESSONS) - 1, int(state.get("index", 0)) + 1)
            return _lesson_response(mem, load=True)
        if t == "previous lesson":
            state["index"] = max(0, int(state.get("index", 0)) - 1)
            return _lesson_response(mem, load=True)
        idx = int(state.get("index", 0))
        lesson = LESSONS[idx]
        if t == "repeat lesson" or t == "continue learning path":
            return _lesson_response(mem, load=t == "continue learning path")
        if t == "where am i in the learning path":
            return _message(
                f"You are on lesson {idx + 1} of {len(LESSONS)}: {lesson['title']}."
            )
        if t == "give lesson hint":
            mem["hints_requested"] = int(mem.get("hints_requested") or 0) + 1
            return _message(lesson["hint"])
        if t == "show lesson goal":
            return _message(
                f"Lesson {idx + 1} goal: {lesson['goal']} Task: {lesson['task']}"
            )
        if t == "check lesson":
            passed = _lesson_passes(lesson, code, project)
            key = "passed" if passed else "failed"
            if idx not in state.setdefault(key, []):
                state[key].append(idx)
            return _message(
                ("Lesson check passed. " + lesson["next"])
                if passed
                else ("Not yet. " + lesson["hint"]),
                lesson_passed=passed,
            )

    if t.startswith(("start block practice", "start parsons practice")):
        m = re.search(r"(\d+)$", t)
        return _start_blocks(mem, int(m.group(1)) - 1 if m else 0)
    state = (
        mem.get("block_practice")
        if isinstance(mem.get("block_practice"), dict)
        else None
    )
    if t == "exit block practice":
        mem.pop("block_practice", None)
        return _message("Block practice closed. Your editor was not changed.")
    if t == "reset block practice":
        return _start_blocks(mem, int(state.get("index", 0)) if state else 0)
    if t.startswith(block_prefixes):
        if not state:
            return _message("Block practice is not active. Say start block practice.")
        blocks = state.get("blocks") or []
        if t == "read block order":
            return _message(_block_order(state))
        m = re.match(r"read block (\d+)$", t)
        if m:
            block = next((b for b in blocks if b["id"] == int(m.group(1))), None)
            return (
                _message(
                    f"Block {block['id']}, indentation {block['indent']}: {block['text']}."
                )
                if block
                else _message("That block number is not available.")
            )
        m = re.match(r"move block (\d+) (up|down)$", t)
        if m:
            pos = next(
                (i for i, b in enumerate(blocks) if b["id"] == int(m.group(1))), -1
            )
            delta = -1 if m.group(2) == "up" else 1
            if pos < 0:
                return _message("That block number is not available.")
            target = pos + delta
            if target < 0 or target >= len(blocks):
                return _message(f"Block {m.group(1)} cannot move {m.group(2)} farther.")
            blocks[pos], blocks[target] = blocks[target], blocks[pos]
            return _message(
                f"Block {m.group(1)} moved {m.group(2)}. Say read block order to hear the full order."
            )
        m = re.match(r"(indent|outdent) block (\d+)$", t)
        if m:
            block = next((b for b in blocks if b["id"] == int(m.group(2))), None)
            if not block:
                return _message("That block number is not available.")
            block["indent"] = (
                min(4, block["indent"] + 1)
                if m.group(1) == "indent"
                else max(0, block["indent"] - 1)
            )
            return _message(
                f"Block {block['id']} now has indentation level {block['indent']}."
            )
        if t == "check block order":
            state["attempts"] = int(state.get("attempts", 0)) + 1
            mem["block_attempts"] = int(mem.get("block_attempts") or 0) + 1
            expected = BLOCK_EXERCISES[int(state["index"])][1]
            passed = [(b["id"], b["indent"]) for b in blocks] == [
                (i, indent) for i, _text, indent in expected
            ]
            return _message(
                "Block order is correct. You can convert blocks to code."
                if passed
                else "Not yet. Check both line order and indentation.",
                block_passed=passed,
            )
        if t == "convert blocks to code":
            generated = (
                "\n".join("    " * int(b["indent"]) + b["text"] for b in blocks) + "\n"
            )
            return _edit(
                generated,
                "Converted the current blocks to code in the editor.",
                "block_practice",
            )

    if t in hint_commands:
        if t in {"hide hints", "stop hints"}:
            mem.pop("hint_state", None)
            return _message("Hints cleared. No hint will appear unless you ask.")
        current = mem.setdefault("hint_state", {"level": 0, "last": ""})
        lesson_state = (
            mem.get("learning_path")
            if isinstance(mem.get("learning_path"), dict)
            else None
        )
        error_state = (
            mem.get("error_practice")
            if isinstance(mem.get("error_practice"), dict)
            else None
        )
        block_state = (
            mem.get("block_practice")
            if isinstance(mem.get("block_practice"), dict)
            else None
        )
        if (
            not lesson_state
            and not error_state
            and not block_state
            and t
            in {
                "give me a small hint",
                "give me a bigger hint",
                "give me the next hint",
            }
        ):
            return None
        base = (
            ERROR_CHALLENGES[int(error_state["index"])]["hint"]
            if error_state
            else LESSONS[int(lesson_state["index"])]["hint"]
            if lesson_state
            else "Compare the current order and indentation with the task."
            if block_state
            else "Read the error type and the line before changing code."
        )
        if t == "repeat hint" and current.get("last"):
            return _message(current["last"])
        if t == "why is this hint useful":
            return _message(
                "It points to one relevant fact so you can make the next change yourself."
            )
        if t == "show solution steps":
            level = 2
        elif t == "give me a bigger hint":
            level = 1
        elif t == "give me the next hint":
            level = min(2, int(current.get("level", 0)) + 1)
        else:
            level = 0
        suffix = [
            "",
            " Check the exact names, punctuation, and indentation on that line.",
            " Step 1: fix the named issue. Step 2: run the code. Step 3: read the output or error again.",
        ][level]
        current.update(level=level, last=base + suffix)
        mem["hints_requested"] = int(mem.get("hints_requested") or 0) + 1
        return _message(current["last"])

    if t in {"show keyboard shortcuts", "practice keyboard shortcuts"}:
        return _message(SHORTCUTS)
    if t in {"enter navigation mode", "navigation mode on"}:
        mem["navigation_mode"] = True
        return _message(
            "Navigation mode on. Structural navigation commands are active; editing remains available."
        )
    if t in {"exit navigation mode", "navigation mode off"}:
        mem["navigation_mode"] = False
        return _message("Navigation mode off.")
    if t == "what navigation mode am i in":
        return _message(
            "Navigation mode is " + ("on." if mem.get("navigation_mode") else "off.")
        )
    m = re.match(r"(next|previous) (symbol|loop|error|todo)$", t)
    if m:
        direction, kind = m.groups()
        line = max(1, int(cursor_line or 1))
        candidates: List[Tuple[int, str]] = []
        tree = _tree(code)
        if kind == "error":
            nums = re.findall(
                r"line (\d+)", error or str(mem.get("last_run_error") or ""), re.I
            )
            candidates = [(int(n), "error") for n in nums]
        elif kind == "todo":
            candidates = [
                (i, text.strip())
                for i, text in enumerate((code or "").splitlines(), 1)
                if "TODO" in text.upper()
            ]
        elif tree:
            for n in ast.walk(tree):
                if kind == "loop" and isinstance(n, (ast.For, ast.While)):
                    candidates.append((n.lineno, "loop"))
                elif kind == "symbol" and isinstance(
                    n, (ast.FunctionDef, ast.ClassDef)
                ):
                    candidates.append((n.lineno, n.name))
        candidates.sort()
        chosen = (
            next((x for x in candidates if x[0] > line), None)
            if direction == "next"
            else next((x for x in reversed(candidates) if x[0] < line), None)
        )
        if not chosen:
            return _message(f"There is no {direction} {kind}.")
        msg = f"{direction.title()} {kind} is {chosen[1]} on line {chosen[0]}."
        return {
            "success": True,
            "action": "navigate_code",
            "line": chosen[0],
            "end_line": chosen[0],
            "message": msg,
            "speech": msg,
        }
    if t == "read current scope":
        tree = _tree(code)
        line = int(cursor_line or 1)
        scopes = []
        if tree:
            for n in ast.walk(tree):
                if isinstance(
                    n, (ast.FunctionDef, ast.ClassDef)
                ) and n.lineno <= line <= getattr(n, "end_lineno", n.lineno):
                    scopes.append((n.lineno, n.name))
        return _message(
            f"Current scope is {max(scopes)[1]}."
            if scopes
            else "Current scope is the module level."
        )

    if t in {"teacher mode on", "teacher mode off"}:
        mem["teacher_mode"] = t.endswith("on")
        return _message("Teacher mode " + ("on." if mem["teacher_mode"] else "off."))
    if t == "include code in teacher report":
        mem["teacher_include_code"] = True
        return _message(
            "Teacher reports will include the current code until you exclude it."
        )
    if t == "exclude code from teacher report":
        mem["teacher_include_code"] = False
        return _message("Teacher reports will exclude full code.")
    if t == "reset teacher report":
        for key in (
            "accessible_command_history",
            "error_type_counts",
            "block_attempts",
            "hints_requested",
        ):
            mem.pop(key, None)
        return _message(
            "Teacher report counters reset. Learning progress and code were not changed."
        )
    if t in {
        "generate lesson report",
        "generate student report",
        "generate mistakes report",
        "show common mistakes",
        "export teacher report",
    }:
        report = _teacher_report(mem, storage, code)
        if t == "show common mistakes":
            counts = mem.get("error_type_counts") or {}
            return _message(
                "Common mistakes: "
                + ", ".join(f"{k} {v}" for k, v in counts.items())
                + "."
                if counts
                else "No tracked error types yet."
            )
        speech = "Teacher report prepared locally. Full code is " + (
            "included." if mem.get("teacher_include_code") else "excluded by default."
        )
        if t == "export teacher report":
            return {
                "success": True,
                "action": "export_teacher_report",
                "filename": "CodeUp_Teacher_Report.md",
                "report": report,
                "message": speech,
                "speech": speech,
            }
        return _message(speech, report=report)

    if t in {
        "check beginner style",
        "check readable names",
        "check function length",
        "check too much nesting",
        "check confusing names",
        "explain style issues",
        "show more style issues",
    }:
        issues = _style_issues(code)
        limit = len(issues) if t == "show more style issues" else 3
        if not issues:
            return _message("No beginner style issues found.")
        shown = issues[:limit]
        more = len(issues) - len(shown)
        return _message(
            "Style check: "
            + " ".join(shown)
            + (f" {more} more. Say show more style issues." if more else ""),
            style_issues=shown,
        )

    if (
        t.startswith("start error practice")
        or t.startswith("practice ")
        and t.endswith(" errors")
    ):
        kind = next(
            (k for k in ("indentation", "name", "type", "syntax") if k in t), ""
        )
        idx = next((i for i, c in enumerate(ERROR_CHALLENGES) if c["kind"] == kind), 0)
        mem["error_practice"] = {"index": idx}
        c = ERROR_CHALLENGES[idx]
        return _edit(
            c["code"],
            f"Error challenge: {c['name']}. Fix the code, run it, then say check error fix.",
            "error_practice",
        )
    state = (
        mem.get("error_practice")
        if isinstance(mem.get("error_practice"), dict)
        else None
    )
    if t == "exit error practice":
        mem.pop("error_practice", None)
        return _message("Error practice closed.")
    if (
        any(
            x in t
            for x in ("error challenge", "error fix", "error hint", "error solution")
        )
        or t == "next error challenge"
    ):
        if not state:
            return _message("Error practice is not active. Say start error practice.")
        idx = int(state["index"])
        c = ERROR_CHALLENGES[idx]
        if t == "read error challenge":
            return _message(
                f"Challenge {idx + 1}: {c['name']}. Find and fix the error."
            )
        if t in {"give error hint", "show error hint"}:
            mem["hints_requested"] = int(mem.get("hints_requested") or 0) + 1
            return _message(c["hint"])
        if t == "show error solution":
            return _edit(
                c["fixed"],
                f"Solution loaded for {c['name']}. Run it and compare the result.",
                "error_practice_solution",
            )
        if t == "next error challenge":
            state["index"] = (idx + 1) % len(ERROR_CHALLENGES)
            c = ERROR_CHALLENGES[state["index"]]
            return _edit(
                c["code"],
                f"Next error challenge: {c['name']}. Fix it, then say check error fix.",
                "error_practice",
            )
        if t == "check error fix":
            candidate, expected = _tree(code), _tree(c["fixed"])
            valid = (
                candidate is not None
                and expected is not None
                and ast.dump(candidate) == ast.dump(expected)
            )
            return _message(
                "Error fix passed. The repaired program matches the expected behavior."
                if valid
                else "Not fixed yet. " + c["hint"],
                error_fix_passed=valid,
            )

    if t in {"open accessible coding tools", "show accessible coding tools"}:
        return {
            "success": True,
            "action": "open_accessible_tools",
            "message": "Opening accessible coding tools.",
            "speech": "Opening accessible coding tools.",
        }
    if t == "explain quorum":
        return _message(
            "Quorum is an accessible, evidence-oriented programming language and learning ecosystem. CodeUp does not replace it; CodeUp teaches mainstream Python as a bridge toward professional tools."
        )
    if t == "how is codeup different from quorum":
        return _message(
            "CodeUp is a Python-focused, audio-first beginner bridge. Quorum is its own programming language and learning ecosystem. They are separate tools with different roles."
        )
    if t == "explain vs code handoff":
        return _message(
            "Export your CodeUp project, open it in VS Code, enable VS Code screen reader mode, and continue with your usual screen reader. CodeUp does not replace VS Code or assistive technology."
        )
    if t == "show accessible coding pathway":
        return _message(
            "Start with CodeUp lessons and audio debugging, learn screen-reader-compatible coding habits, explore Quorum if useful, then move to VS Code for larger projects."
        )
    return None
