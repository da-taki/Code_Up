"""Deterministic Audio Blocks Mode for beginner Python programs."""

from __future__ import annotations

import ast
import copy
import json
import keyword
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


CATALOG: Dict[str, List[Tuple[str, str]]] = {
    "output": [("print_text", "print text"), ("print_variable", "print variable")],
    "variables": [
        ("set_number", "set variable to number"),
        ("set_text", "set variable to text"),
        ("set_expression", "set variable to expression"),
        ("change_variable", "change variable by number"),
    ],
    "math": [
        ("add_numbers", "add numbers"),
        ("subtract_numbers", "subtract numbers"),
        ("multiply_numbers", "multiply numbers"),
        ("divide_numbers", "divide numbers"),
        ("expression", "expression block"),
    ],
    "conditions": [
        ("if_condition", "if condition"),
        ("if_else_condition", "if else condition"),
        ("compare_values", "compare values"),
    ],
    "loops": [
        ("repeat_times", "repeat times"),
        ("for_range", "for each number in range"),
        ("while_condition", "while condition"),
    ],
    "lists": [
        ("create_list", "create list"),
        ("append_list", "append to list"),
        ("print_list_item", "print list item"),
        ("loop_list", "loop through list"),
    ],
    "functions": [
        ("define_function", "define function"),
        ("call_function", "call function"),
        ("return_value", "return value"),
    ],
    "input": [("ask_input", "ask input"), ("ask_number_input", "ask number input")],
    "comments": [("comment_note", "comment note")],
}

BLOCK_LABELS = {
    block_type: label for items in CATALOG.values() for block_type, label in items
}
CONTAINERS = {
    "if_condition",
    "if_else_condition",
    "repeat_times",
    "for_range",
    "while_condition",
    "loop_list",
    "define_function",
}
MAX_BLOCKS = 100
MAX_HISTORY = 30
SECRET_MARKERS = ("api_key", "secret=", "token=", "password=", "private key")


def new_workspace() -> Dict[str, Any]:
    return {
        "mode": "code",
        "blocks": [],
        "next_id": 1,
        "cursor_id": None,
        "generated": False,
        "generated_code": "",
        "dirty": False,
        "undo": [],
        "redo": [],
        "lesson": None,
    }


def get_workspace(
    mem: Dict[str, Any], *, create: bool = False
) -> Optional[Dict[str, Any]]:
    workspace = mem.get("audio_blocks")
    if not isinstance(workspace, dict) and create:
        workspace = new_workspace()
        mem["audio_blocks"] = workspace
    return workspace if isinstance(workspace, dict) else None


def _snapshot(workspace: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "blocks": copy.deepcopy(workspace.get("blocks", [])),
        "next_id": int(workspace.get("next_id", 1)),
        "cursor_id": workspace.get("cursor_id"),
        "generated": bool(workspace.get("generated")),
        "generated_code": str(workspace.get("generated_code") or ""),
        "dirty": bool(workspace.get("dirty")),
    }


def _restore(workspace: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
    for key in (
        "blocks",
        "next_id",
        "cursor_id",
        "generated",
        "generated_code",
        "dirty",
    ):
        workspace[key] = copy.deepcopy(snapshot[key])


def _before_change(workspace: Dict[str, Any]) -> None:
    undo = workspace.setdefault("undo", [])
    undo.append(_snapshot(workspace))
    del undo[:-MAX_HISTORY]
    workspace["redo"] = []


def _changed(workspace: Dict[str, Any]) -> None:
    workspace["dirty"] = True
    workspace["generated"] = False


def valid_name(name: str) -> bool:
    value = str(name or "").strip()
    return bool(
        value
        and value.isidentifier()
        and not keyword.iskeyword(value)
        and not value.startswith("__")
    )


_ALLOWED_EXPR_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.List,
    ast.Tuple,
    ast.Subscript,
    ast.Slice,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


def safe_expression(expression: str) -> Tuple[bool, str]:
    value = str(expression or "").strip()
    if not value or len(value) > 200:
        return False, "The expression is empty or too long."
    try:
        tree = ast.parse(value, mode="eval")
    except SyntaxError:
        return False, "That expression is not valid beginner Python."
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_EXPR_NODES):
            return False, f"Expressions cannot use {type(node).__name__}."
        if isinstance(node, ast.Name) and not valid_name(node.id):
            return False, f"The name {node.id} is not safe."
    return True, value


def _number(value: Any) -> Tuple[bool, str]:
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return False, ""
    if not (-1_000_000_000 <= number <= 1_000_000_000):
        return False, ""
    return True, str(int(number)) if number.is_integer() else str(number)


def _condition_words(value: str) -> str:
    result = str(value or "").strip()
    replacements = [
        (r"\bgreater than or equal to\b", ">="),
        (r"\bless than or equal to\b", "<="),
        (r"\bnot equal to\b", "!="),
        (r"\bgreater than\b", ">"),
        (r"\bless than\b", "<"),
        (r"\bequals\b", "=="),
        (r"\bequal to\b", "=="),
    ]
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def normalize_slots(
    block_type: str, slots: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], str]:
    s = {str(k): v for k, v in (slots or {}).items()}
    name_keys = {
        "print_variable": ("variable",),
        "set_number": ("variable",),
        "set_text": ("variable",),
        "set_expression": ("variable",),
        "change_variable": ("variable",),
        "add_numbers": ("variable",),
        "subtract_numbers": ("variable",),
        "multiply_numbers": ("variable",),
        "divide_numbers": ("variable",),
        "expression": ("variable",),
        "compare_values": ("variable",),
        "for_range": ("variable",),
        "create_list": ("variable",),
        "append_list": ("variable",),
        "print_list_item": ("variable",),
        "loop_list": ("variable", "item"),
        "define_function": ("name",),
        "call_function": ("name",),
        "ask_input": ("variable",),
        "ask_number_input": ("variable",),
    }
    for key in name_keys.get(block_type, ()):
        if not valid_name(str(s.get(key, ""))):
            return (
                None,
                f"I cannot use {s.get(key) or 'an empty value'} as a variable or function name.",
            )

    numeric_keys = {
        "set_number": ("value",),
        "change_variable": ("value",),
        "repeat_times": ("times",),
        "for_range": ("start", "stop"),
        "print_list_item": ("index",),
    }
    for key in numeric_keys.get(block_type, ()):
        ok, normalized = _number(s.get(key, ""))
        if not ok:
            return None, f"Block value {key} must be a safe number."
        s[key] = normalized

    expr_keys = {
        "set_expression": ("expression",),
        "expression": ("expression",),
        "if_condition": ("condition",),
        "if_else_condition": ("condition",),
        "while_condition": ("condition",),
        "return_value": ("value",),
        "compare_values": ("left", "right"),
        "add_numbers": ("left", "right"),
        "subtract_numbers": ("left", "right"),
        "multiply_numbers": ("left", "right"),
        "divide_numbers": ("left", "right"),
        "append_list": ("value",),
        "call_function": ("arguments",),
    }
    for key in expr_keys.get(block_type, ()):
        value = (
            _condition_words(str(s.get(key, "")))
            if key == "condition"
            else str(s.get(key, "")).strip()
        )
        if block_type == "call_function" and key == "arguments" and not value:
            s[key] = ""
            continue
        ok, reason = safe_expression(value)
        if not ok:
            return None, f"Block {key} is unsafe: {reason}"
        s[key] = value

    if block_type == "print_text":
        text = str(s.get("text", ""))[:300]
        if not text:
            return None, "A print text block needs text."
        s["text"] = text
    if block_type in {"set_text", "ask_input", "ask_number_input"}:
        s["text"] = str(s.get("text", ""))[:300]
    if block_type == "comment_note":
        text = str(s.get("text", "")).replace("\n", " ")[:300]
        if not text:
            return None, "A comment block needs a note."
        s["text"] = text
    if block_type == "create_list":
        values = str(s.get("values", "")).strip()
        expression = f"[{values}]" if not values.startswith("[") else values
        ok, reason = safe_expression(expression)
        if not ok or not isinstance(ast.parse(expression, mode="eval").body, ast.List):
            return None, f"List values are unsafe: {reason}"
        s["values"] = expression
    if block_type == "compare_values":
        operator = str(s.get("operator", "=="))
        if operator not in {"==", "!=", ">", ">=", "<", "<="}:
            return None, "That comparison operator is not supported."
        s["operator"] = operator
    return s, ""


def block_label(block_type: str, slots: Dict[str, Any]) -> str:
    s = slots
    labels = {
        "print_text": lambda: f"print text {s['text']}",
        "print_variable": lambda: f"print variable {s['variable']}",
        "set_number": lambda: f"set {s['variable']} to {s['value']}",
        "set_text": lambda: f"set {s['variable']} to text {s['text']}",
        "set_expression": lambda: f"set {s['variable']} to {s['expression']}",
        "change_variable": lambda: f"change {s['variable']} by {s['value']}",
        "add_numbers": lambda: f"set {s['variable']} to {s['left']} plus {s['right']}",
        "subtract_numbers": lambda: (
            f"set {s['variable']} to {s['left']} minus {s['right']}"
        ),
        "multiply_numbers": lambda: (
            f"set {s['variable']} to {s['left']} times {s['right']}"
        ),
        "divide_numbers": lambda: (
            f"set {s['variable']} to {s['left']} divided by {s['right']}"
        ),
        "expression": lambda: f"set {s['variable']} to expression {s['expression']}",
        "if_condition": lambda: f"if {s['condition']}",
        "if_else_condition": lambda: f"if else {s['condition']}",
        "compare_values": lambda: (
            f"set {s['variable']} to comparison {s['left']} {s['operator']} {s['right']}"
        ),
        "repeat_times": lambda: f"repeat {s['times']} times",
        "for_range": lambda: f"for {s['variable']} from {s['start']} to {s['stop']}",
        "while_condition": lambda: f"while {s['condition']}",
        "create_list": lambda: f"create list {s['variable']}",
        "append_list": lambda: f"append {s['value']} to {s['variable']}",
        "print_list_item": lambda: f"print item {s['index']} from {s['variable']}",
        "loop_list": lambda: f"loop through {s['variable']} as {s['item']}",
        "define_function": lambda: f"define function {s['name']}",
        "call_function": lambda: f"call function {s['name']}",
        "return_value": lambda: f"return {s['value']}",
        "ask_input": lambda: f"ask for {s['variable']}",
        "ask_number_input": lambda: f"ask for number {s['variable']}",
        "comment_note": lambda: f"comment {s['text']}",
    }
    return labels[block_type]()


def _find(workspace: Dict[str, Any], block_id: int) -> Optional[Dict[str, Any]]:
    return next(
        (block for block in workspace.get("blocks", []) if block.get("id") == block_id),
        None,
    )


def add_block(
    workspace: Dict[str, Any],
    block_type: str,
    slots: Dict[str, Any],
    *,
    parent_id: Optional[int] = None,
    branch: str = "body",
) -> Tuple[Optional[Dict[str, Any]], str]:
    if block_type not in BLOCK_LABELS:
        return None, "That block type is not available."
    if len(workspace.get("blocks", [])) >= MAX_BLOCKS:
        return None, f"A workspace can contain at most {MAX_BLOCKS} blocks."
    normalized, error = normalize_slots(block_type, slots)
    if normalized is None:
        return None, error
    if parent_id is not None:
        parent = _find(workspace, parent_id)
        if not parent or parent["type"] not in CONTAINERS:
            return None, "That parent block cannot contain child blocks."
        if branch == "else" and parent["type"] != "if_else_condition":
            return None, "Only an if else block has an else branch."
    _before_change(workspace)
    block_id = int(workspace.get("next_id", 1))
    block = {
        "id": block_id,
        "type": block_type,
        "label": block_label(block_type, normalized),
        "slots": normalized,
        "indent": 0,
        "parent_id": parent_id,
        "branch": branch if branch in {"body", "else"} else "body",
    }
    workspace.setdefault("blocks", []).append(block)
    workspace["next_id"] = block_id + 1
    workspace["cursor_id"] = block_id
    _changed(workspace)
    return block, ""


def edit_block(
    workspace: Dict[str, Any], block_id: int, updates: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], str]:
    block = _find(workspace, block_id)
    if not block:
        return None, f"Block {block_id} does not exist."
    slots = {**block["slots"], **updates}
    normalized, error = normalize_slots(block["type"], slots)
    if normalized is None:
        return None, error
    _before_change(workspace)
    block["slots"] = normalized
    block["label"] = block_label(block["type"], normalized)
    block.pop("incomplete", None)
    workspace["cursor_id"] = block_id
    _changed(workspace)
    return block, ""


def _descendants(workspace: Dict[str, Any], block_id: int) -> set[int]:
    found: set[int] = set()
    pending = [block_id]
    while pending:
        parent = pending.pop()
        children = [
            b["id"] for b in workspace.get("blocks", []) if b.get("parent_id") == parent
        ]
        found.update(children)
        pending.extend(children)
    return found


def _siblings(workspace: Dict[str, Any], block: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        b
        for b in workspace.get("blocks", [])
        if b.get("parent_id") == block.get("parent_id")
        and b.get("branch", "body") == block.get("branch", "body")
    ]


def move_relative(workspace: Dict[str, Any], block_id: int, direction: str) -> str:
    block = _find(workspace, block_id)
    if not block:
        return f"Block {block_id} does not exist."
    siblings = _siblings(workspace, block)
    position = siblings.index(block)
    target_position = position - 1 if direction == "up" else position + 1
    if target_position < 0 or target_position >= len(siblings):
        return f"Block {block_id} cannot move {direction} farther."
    target = siblings[target_position]
    blocks = workspace["blocks"]
    left, right = blocks.index(block), blocks.index(target)
    _before_change(workspace)
    blocks[left], blocks[right] = blocks[right], blocks[left]
    workspace["cursor_id"] = block_id
    _changed(workspace)
    return f"Moved block {block_id} {direction}."


def move_before_after(
    workspace: Dict[str, Any], block_id: int, target_id: int, position: str
) -> str:
    block, target = _find(workspace, block_id), _find(workspace, target_id)
    if not block or not target:
        return "Both block numbers must exist."
    if block_id == target_id or target_id in _descendants(workspace, block_id):
        return "A block cannot be moved relative to itself or its child."
    _before_change(workspace)
    blocks = workspace["blocks"]
    blocks.remove(block)
    block["parent_id"] = target.get("parent_id")
    block["branch"] = target.get("branch", "body")
    index = blocks.index(target) + (1 if position == "after" else 0)
    blocks.insert(index, block)
    workspace["cursor_id"] = block_id
    _changed(workspace)
    return f"Moved block {block_id} {position} block {target_id}."


def nest_block(
    workspace: Dict[str, Any], block_id: int, parent_id: int, *, branch: str = "body"
) -> str:
    block, parent = _find(workspace, block_id), _find(workspace, parent_id)
    if not block or not parent:
        return "Both block numbers must exist."
    if parent["type"] not in CONTAINERS:
        return f"Block {parent_id} cannot contain child blocks."
    if block_id == parent_id or parent_id in _descendants(workspace, block_id):
        return "A block cannot contain itself or one of its parents."
    if branch == "else" and parent["type"] != "if_else_condition":
        return "Only an if else block has an else branch."
    _before_change(workspace)
    block["parent_id"] = parent_id
    block["branch"] = branch
    workspace["cursor_id"] = block_id
    _changed(workspace)
    return f"Put block {block_id} inside block {parent_id}{' else branch' if branch == 'else' else ''}."


def outdent_block(workspace: Dict[str, Any], block_id: int) -> str:
    block = _find(workspace, block_id)
    if not block:
        return f"Block {block_id} does not exist."
    parent = _find(workspace, int(block.get("parent_id") or 0))
    if not parent:
        return f"Block {block_id} is already at the top level."
    _before_change(workspace)
    block["parent_id"] = parent.get("parent_id")
    block["branch"] = parent.get("branch", "body")
    workspace["cursor_id"] = block_id
    _changed(workspace)
    return f"Outdented block {block_id}."


def indent_block(workspace: Dict[str, Any], block_id: int) -> str:
    block = _find(workspace, block_id)
    if not block:
        return f"Block {block_id} does not exist."
    siblings = _siblings(workspace, block)
    position = siblings.index(block)
    if position == 0:
        return "There is no previous container block to indent into."
    parent = siblings[position - 1]
    return nest_block(workspace, block_id, parent["id"])


def delete_block(workspace: Dict[str, Any], block_id: int) -> str:
    block = _find(workspace, block_id)
    if not block:
        return f"Block {block_id} does not exist."
    ids = {block_id} | _descendants(workspace, block_id)
    _before_change(workspace)
    workspace["blocks"] = [b for b in workspace["blocks"] if b["id"] not in ids]
    workspace["cursor_id"] = (
        workspace["blocks"][0]["id"] if workspace["blocks"] else None
    )
    _changed(workspace)
    suffix = (
        f" and {len(ids) - 1} child block{'s' if len(ids) != 2 else ''}"
        if len(ids) > 1
        else ""
    )
    return f"Deleted block {block_id}{suffix}."


def undo(workspace: Dict[str, Any]) -> str:
    history = workspace.setdefault("undo", [])
    if not history:
        return "There is no block change to undo."
    workspace.setdefault("redo", []).append(_snapshot(workspace))
    _restore(workspace, history.pop())
    return "Undid the last block change."


def redo(workspace: Dict[str, Any]) -> str:
    history = workspace.setdefault("redo", [])
    if not history:
        return "There is no block change to redo."
    workspace.setdefault("undo", []).append(_snapshot(workspace))
    _restore(workspace, history.pop())
    return "Redid the block change."


def _children(
    workspace: Dict[str, Any], parent_id: Optional[int], branch: str = "body"
) -> List[Dict[str, Any]]:
    return [
        b
        for b in workspace.get("blocks", [])
        if b.get("parent_id") == parent_id and b.get("branch", "body") == branch
    ]


def _line_for(block: Dict[str, Any]) -> str:
    s, kind = block["slots"], block["type"]
    mapping = {
        "print_text": lambda: f"print({s['text']!r})",
        "print_variable": lambda: f"print({s['variable']})",
        "set_number": lambda: f"{s['variable']} = {s['value']}",
        "set_text": lambda: f"{s['variable']} = {s['text']!r}",
        "set_expression": lambda: f"{s['variable']} = {s['expression']}",
        "change_variable": lambda: f"{s['variable']} += {s['value']}",
        "add_numbers": lambda: f"{s['variable']} = {s['left']} + {s['right']}",
        "subtract_numbers": lambda: f"{s['variable']} = {s['left']} - {s['right']}",
        "multiply_numbers": lambda: f"{s['variable']} = {s['left']} * {s['right']}",
        "divide_numbers": lambda: f"{s['variable']} = {s['left']} / {s['right']}",
        "expression": lambda: f"{s['variable']} = {s['expression']}",
        "if_condition": lambda: f"if {s['condition']}:",
        "if_else_condition": lambda: f"if {s['condition']}:",
        "compare_values": lambda: (
            f"{s['variable']} = {s['left']} {s['operator']} {s['right']}"
        ),
        "repeat_times": lambda: f"for i in range({s['times']}):",
        "for_range": lambda: (
            f"for {s['variable']} in range({s['start']}, {s['stop']}):"
        ),
        "while_condition": lambda: f"while {s['condition']}:",
        "create_list": lambda: f"{s['variable']} = {s['values']}",
        "append_list": lambda: f"{s['variable']}.append({s['value']})",
        "print_list_item": lambda: f"print({s['variable']}[{s['index']}])",
        "loop_list": lambda: f"for {s['item']} in {s['variable']}:",
        "define_function": lambda: f"def {s['name']}():",
        "call_function": lambda: f"{s['name']}({s['arguments']})",
        "return_value": lambda: f"return {s['value']}",
        "ask_input": lambda: f"{s['variable']} = input({s['text']!r})",
        "ask_number_input": lambda: f"{s['variable']} = float(input({s['text']!r}))",
        "comment_note": lambda: f"# {s['text']}",
    }
    return mapping[kind]()


def compile_workspace(workspace: Dict[str, Any]) -> Tuple[Optional[str], str]:
    if not workspace.get("blocks"):
        return None, "The Audio Blocks workspace is empty."
    lines: List[str] = []

    def emit(blocks: Iterable[Dict[str, Any]], depth: int) -> Optional[str]:
        for block in blocks:
            if block.get("incomplete"):
                return f"Block {block['id']} is incomplete. Set its cleared value before compiling."
            lines.append("    " * depth + _line_for(block))
            if block["type"] in CONTAINERS:
                body = _children(workspace, block["id"], "body")
                if not body:
                    return f"Block {block['id']}, {block['label']}, needs at least one child block."
                error = emit(body, depth + 1)
                if error:
                    return error
                if block["type"] == "if_else_condition":
                    other = _children(workspace, block["id"], "else")
                    if not other:
                        return f"Block {block['id']} needs at least one block in its else branch."
                    lines.append("    " * depth + "else:")
                    error = emit(other, depth + 1)
                    if error:
                        return error
        return None

    problem = emit(_children(workspace, None), 0)
    if problem:
        return None, problem
    code = "\n".join(lines) + "\n"
    try:
        compile(code, "<audio-blocks>", "exec")
    except SyntaxError as exc:
        return None, f"Generated code is invalid near line {exc.lineno}: {exc.msg}."
    workspace["generated"] = True
    workspace["generated_code"] = code
    workspace["dirty"] = False
    return code, ""


class ImportProblem(ValueError):
    pass


def import_python(workspace: Dict[str, Any], code: str) -> Tuple[bool, str]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        return False, f"The Python has a syntax error on line {exc.lineno}: {exc.msg}."
    candidate = new_workspace()
    for line in (code or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and stripped[1:].strip():
            block, error = add_block(
                candidate, "comment_note", {"text": stripped[1:].strip()}
            )
            if not block:
                return False, error

    def expression(node: ast.AST) -> str:
        value = ast.unparse(node)
        ok, reason = safe_expression(value)
        if not ok:
            raise ImportProblem(reason)
        return value

    def append(
        kind: str, slots: Dict[str, Any], parent: Optional[int], branch: str = "body"
    ) -> int:
        block, error = add_block(
            candidate, kind, slots, parent_id=parent, branch=branch
        )
        if not block:
            raise ImportProblem(error)
        return block["id"]

    def convert_statements(
        statements: Iterable[ast.stmt],
        parent: Optional[int] = None,
        branch: str = "body",
    ) -> None:
        for node in statements:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
            ):
                name = node.value.func.id
                if name == "print" and len(node.value.args) == 1:
                    arg = node.value.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        append("print_text", {"text": arg.value}, parent, branch)
                    elif isinstance(arg, ast.Name):
                        append("print_variable", {"variable": arg.id}, parent, branch)
                    elif isinstance(arg, ast.Subscript) and isinstance(
                        arg.value, ast.Name
                    ):
                        append(
                            "print_list_item",
                            {"variable": arg.value.id, "index": expression(arg.slice)},
                            parent,
                            branch,
                        )
                    else:
                        raise ImportProblem(
                            f"unsupported print expression on line {node.lineno}"
                        )
                elif name not in {"eval", "exec", "open", "__import__"}:
                    args = ", ".join(expression(arg) for arg in node.value.args)
                    append(
                        "call_function",
                        {"name": name, "arguments": args},
                        parent,
                        branch,
                    )
                else:
                    raise ImportProblem(f"unsafe call on line {node.lineno}")
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                name, value = node.targets[0].id, node.value
                if isinstance(value, ast.Constant) and isinstance(
                    value.value, (int, float)
                ):
                    append(
                        "set_number",
                        {"variable": name, "value": value.value},
                        parent,
                        branch,
                    )
                elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                    append(
                        "set_text",
                        {"variable": name, "text": value.value},
                        parent,
                        branch,
                    )
                elif isinstance(value, ast.List):
                    append(
                        "create_list",
                        {"variable": name, "values": expression(value)},
                        parent,
                        branch,
                    )
                else:
                    append(
                        "set_expression",
                        {"variable": name, "expression": expression(value)},
                        parent,
                        branch,
                    )
            elif (
                isinstance(node, ast.AugAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.op, ast.Add)
            ):
                append(
                    "change_variable",
                    {"variable": node.target.id, "value": expression(node.value)},
                    parent,
                    branch,
                )
            elif isinstance(node, ast.If):
                kind = "if_else_condition" if node.orelse else "if_condition"
                block_id = append(
                    kind, {"condition": expression(node.test)}, parent, branch
                )
                convert_statements(node.body, block_id, "body")
                if node.orelse:
                    convert_statements(node.orelse, block_id, "else")
            elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                if (
                    isinstance(node.iter, ast.Call)
                    and isinstance(node.iter.func, ast.Name)
                    and node.iter.func.id == "range"
                ):
                    args = node.iter.args
                    if len(args) == 1:
                        block_id = append(
                            "repeat_times",
                            {"times": expression(args[0])},
                            parent,
                            branch,
                        )
                    elif len(args) == 2:
                        block_id = append(
                            "for_range",
                            {
                                "variable": node.target.id,
                                "start": expression(args[0]),
                                "stop": expression(args[1]),
                            },
                            parent,
                            branch,
                        )
                    else:
                        raise ImportProblem(f"unsupported range on line {node.lineno}")
                elif isinstance(node.iter, ast.Name):
                    block_id = append(
                        "loop_list",
                        {"variable": node.iter.id, "item": node.target.id},
                        parent,
                        branch,
                    )
                else:
                    raise ImportProblem(f"unsupported for loop on line {node.lineno}")
                convert_statements(node.body, block_id)
            elif isinstance(node, ast.While):
                block_id = append(
                    "while_condition",
                    {"condition": expression(node.test)},
                    parent,
                    branch,
                )
                convert_statements(node.body, block_id)
            elif isinstance(node, ast.FunctionDef) and not node.args.args:
                block_id = append(
                    "define_function", {"name": node.name}, parent, branch
                )
                convert_statements(node.body, block_id)
            elif isinstance(node, ast.Return):
                append(
                    "return_value", {"value": expression(node.value)}, parent, branch
                )
            elif (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "append"
                and isinstance(node.value.func.value, ast.Name)
                and len(node.value.args) == 1
            ):
                append(
                    "append_list",
                    {
                        "variable": node.value.func.value.id,
                        "value": expression(node.value.args[0]),
                    },
                    parent,
                    branch,
                )
            else:
                raise ImportProblem(
                    f"Audio Blocks Mode does not support {type(node).__name__} on line {getattr(node, 'lineno', 1)}"
                )

    try:
        convert_statements(tree.body)
    except ImportProblem as exc:
        return False, str(exc)
    _before_change(workspace)
    lesson = workspace.get("lesson")
    workspace.update(
        {key: copy.deepcopy(value) for key, value in _snapshot(candidate).items()}
    )
    workspace["lesson"] = lesson
    workspace["dirty"] = True
    workspace["generated"] = False
    return (
        True,
        f"Imported {len(workspace['blocks'])} Python statements into Audio Blocks Mode. The Python editor was unchanged.",
    )


LESSONS = [
    (
        "Print hello world",
        [("print_text", {"text": "Hello world"}, None, "body")],
        "Add a print text block.",
        "Use add print text hello world.",
    ),
    (
        "Store and print a variable",
        [
            ("set_number", {"variable": "score", "value": 10}, None, "body"),
            ("print_variable", {"variable": "score"}, None, "body"),
        ],
        "Store score and print it.",
        "Add a variable block, then print score.",
    ),
    (
        "Count with a loop",
        [
            ("repeat_times", {"times": 3}, None, "body"),
            ("print_text", {"text": "Count"}, 1, "body"),
        ],
        "Repeat output three times.",
        "Put a print block inside repeat.",
    ),
    (
        "Add numbers in a loop",
        [
            ("set_number", {"variable": "total", "value": 0}, None, "body"),
            ("repeat_times", {"times": 3}, None, "body"),
            ("change_variable", {"variable": "total", "value": 1}, 2, "body"),
            ("print_variable", {"variable": "total"}, None, "body"),
        ],
        "Build a total in a loop.",
        "Set total first, then change it inside repeat.",
    ),
    (
        "Use if else",
        [
            ("set_number", {"variable": "score", "value": 70}, None, "body"),
            ("if_else_condition", {"condition": "score >= 50"}, None, "body"),
            ("print_text", {"text": "Pass"}, 2, "body"),
            ("print_text", {"text": "Try again"}, 2, "else"),
        ],
        "Print a result from if and else.",
        "The if else block needs children in both branches.",
    ),
    (
        "Build and print a list",
        [
            (
                "create_list",
                {"variable": "scores", "values": "[70, 80, 90]"},
                None,
                "body",
            ),
            ("print_variable", {"variable": "scores"}, None, "body"),
        ],
        "Create scores and print it.",
        "Use a create list block before print.",
    ),
    (
        "Define and call a function",
        [
            ("define_function", {"name": "greet"}, None, "body"),
            ("print_text", {"text": "Hello"}, 1, "body"),
            ("call_function", {"name": "greet", "arguments": ""}, None, "body"),
        ],
        "Define greet and call it.",
        "Put output inside the function, then call it.",
    ),
    (
        "Convert blocks to Python",
        [
            ("set_number", {"variable": "total", "value": 3}, None, "body"),
            ("print_variable", {"variable": "total"}, None, "body"),
        ],
        "Compile a small workspace to Python.",
        "Build the two blocks, then say compile blocks to Python.",
    ),
]


def _lesson_signature(
    workspace: Dict[str, Any],
) -> List[Tuple[str, Dict[str, Any], Optional[int], str]]:
    return [
        (b["type"], b["slots"], b.get("parent_id"), b.get("branch", "body"))
        for b in workspace.get("blocks", [])
    ]


def load_lesson_solution(workspace: Dict[str, Any], index: int) -> None:
    solution = LESSONS[index][1]
    _before_change(workspace)
    workspace["blocks"] = []
    workspace["next_id"] = 1
    id_map: Dict[int, int] = {}
    for expected_number, (kind, slots, parent_number, branch) in enumerate(solution, 1):
        parent_id = id_map.get(parent_number) if parent_number else None
        block, error = add_block(
            workspace, kind, slots, parent_id=parent_id, branch=branch
        )
        if not block:
            raise ValueError(error)
        id_map[expected_number] = block["id"]
    workspace["undo"] = workspace["undo"][:1]
    workspace["lesson"] = {"index": index}


def public_workspace(workspace: Dict[str, Any]) -> Dict[str, Any]:
    blocks = copy.deepcopy(workspace.get("blocks", []))
    by_id = {b["id"]: b for b in blocks}
    for block in blocks:
        parent = by_id.get(block.get("parent_id"))
        block["indent"] = (parent.get("indent", 0) + 1) if parent else 0
        block["children"] = [
            b["id"] for b in blocks if b.get("parent_id") == block["id"]
        ]
        block["generated"] = _line_for(block)
    return {
        "mode": workspace.get("mode", "code"),
        "blocks": blocks,
        "cursor_id": workspace.get("cursor_id"),
        "generated": bool(workspace.get("generated")),
        "dirty": bool(workspace.get("dirty")),
        "code_preview": workspace.get("generated_code", ""),
        "lesson": copy.deepcopy(workspace.get("lesson")),
    }


def serialize_workspace(workspace: Dict[str, Any]) -> str:
    return json.dumps(public_workspace(workspace), indent=2, sort_keys=True)


def notes() -> str:
    return """# Audio Blocks project

Read `audio_blocks_workspace.json` in block order. Each block has a stable ID,
type, accessible label, slots, nesting level, and parent relationship.

`main.py` is the beginner-readable Python compiled from those blocks. Continue
editing it in Code Mode or open the exported project in VS Code with your usual
screen reader. Audio Blocks Mode is a CodeUp learning feature; it is not Scratch
and does not provide Scratch integration.
"""


def export_files(workspace: Dict[str, Any]) -> Tuple[Optional[Dict[str, str]], str]:
    code, error = compile_workspace(workspace)
    if not code:
        return None, error
    files = {
        "main.py": code,
        "audio_blocks_workspace.json": serialize_workspace(workspace),
        "AUDIO_BLOCKS_NOTES.md": notes(),
    }
    if any(
        marker in content.lower()
        for content in files.values()
        for marker in SECRET_MARKERS
    ):
        return (
            None,
            "The Audio Blocks workspace may contain a secret, so it was not exported.",
        )
    return files, ""


def _message(text: str, workspace: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    return {
        "success": True,
        "action": "deterministic_message",
        "message": text,
        "speech": text,
        "audio_blocks": public_workspace(workspace),
        **extra,
    }


def _edit_response(code: str, text: str, workspace: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": True,
        "action": "conversational_edit",
        "message": text,
        "speech": text,
        "audio_blocks": public_workspace(workspace),
        "ai_action": {
            "action": "replace_code",
            "target": {"line_number": None, "position": ""},
            "code": code,
            "spoken_confirmation": text,
            "confidence": 1.0,
            "requires_confirmation": False,
            "source": "audio_blocks",
        },
    }


def _read_block(workspace: Dict[str, Any], block: Dict[str, Any]) -> str:
    children = _children(workspace, block["id"], "body") + _children(
        workspace, block["id"], "else"
    )
    depth = 0
    parent = _find(workspace, int(block.get("parent_id") or 0))
    while parent:
        depth += 1
        parent = _find(workspace, int(parent.get("parent_id") or 0))
    suffix = (
        f", contains {len(children)} child block{'s' if len(children) != 1 else ''}"
        if children
        else ""
    )
    return f"Block {block['id']}, {BLOCK_LABELS[block['type']]}, {block['label']}, nesting level {depth}{suffix}."


def _parse_add(text: str) -> Tuple[Optional[str], Dict[str, Any]]:
    patterns = [
        (r"^add print block$", "print_text", {"text": "Hello world"}),
        (r"^add print text (.+)$", "print_text", lambda m: {"text": m.group(1)}),
        (
            r"^add print variable (\w+)$",
            "print_variable",
            lambda m: {"variable": m.group(1)},
        ),
        (
            r"^add variable (\w+) equals (-?[\d.]+)$",
            "set_number",
            lambda m: {"variable": m.group(1), "value": m.group(2)},
        ),
        (
            r"^set variable (\w+) to (.+)$",
            "set_text",
            lambda m: {"variable": m.group(1), "text": m.group(2)},
        ),
        (
            r"^add change (\w+) by (-?[\d.]+)$",
            "change_variable",
            lambda m: {"variable": m.group(1), "value": m.group(2)},
        ),
        (
            r"^add variable (\w+) text (.+)$",
            "set_text",
            lambda m: {"variable": m.group(1), "text": m.group(2)},
        ),
        (
            r"^add variable (\w+) expression (.+)$",
            "set_expression",
            lambda m: {"variable": m.group(1), "expression": m.group(2)},
        ),
        (
            r"^add (.+) plus (.+) into (\w+)$",
            "add_numbers",
            lambda m: {"left": m.group(1), "right": m.group(2), "variable": m.group(3)},
        ),
        (
            r"^add (.+) minus (.+) into (\w+)$",
            "subtract_numbers",
            lambda m: {"left": m.group(1), "right": m.group(2), "variable": m.group(3)},
        ),
        (
            r"^add (.+) times (.+) into (\w+)$",
            "multiply_numbers",
            lambda m: {"left": m.group(1), "right": m.group(2), "variable": m.group(3)},
        ),
        (
            r"^add (.+) divided by (.+) into (\w+)$",
            "divide_numbers",
            lambda m: {"left": m.group(1), "right": m.group(2), "variable": m.group(3)},
        ),
        (
            r"^add expression (\w+) equals (.+)$",
            "expression",
            lambda m: {"variable": m.group(1), "expression": m.group(2)},
        ),
        (
            r"^add repeat (\d+) times block$",
            "repeat_times",
            lambda m: {"times": m.group(1)},
        ),
        (
            r"^add for range block from (-?[\d.]+) to (-?[\d.]+)$",
            "for_range",
            lambda m: {"variable": "number", "start": m.group(1), "stop": m.group(2)},
        ),
        (
            r"^add if else (.+)$",
            "if_else_condition",
            lambda m: {"condition": m.group(1)},
        ),
        (r"^add if (.+)$", "if_condition", lambda m: {"condition": m.group(1)}),
        (
            r"^add comparison (.+) (==|!=|>=|<=|>|<) (.+) into (\w+)$",
            "compare_values",
            lambda m: {
                "left": m.group(1),
                "operator": m.group(2),
                "right": m.group(3),
                "variable": m.group(4),
            },
        ),
        (
            r"^add while (.+)$",
            "while_condition",
            lambda m: {"condition": m.group(1)},
        ),
        (
            r"^add list named (\w+)$",
            "create_list",
            lambda m: {"variable": m.group(1), "values": "[]"},
        ),
        (
            r"^append (.+) to (\w+)$",
            "append_list",
            lambda m: {"variable": m.group(2), "value": m.group(1)},
        ),
        (
            r"^add list (\w+) with (.+)$",
            "create_list",
            lambda m: {"variable": m.group(1), "values": m.group(2)},
        ),
        (
            r"^add print item (-?\d+) from (\w+)$",
            "print_list_item",
            lambda m: {"index": m.group(1), "variable": m.group(2)},
        ),
        (
            r"^add loop through (\w+) as (\w+)$",
            "loop_list",
            lambda m: {"variable": m.group(1), "item": m.group(2)},
        ),
        (
            r"^add function named (\w+)$",
            "define_function",
            lambda m: {"name": m.group(1)},
        ),
        (
            r"^add call function (\w+)$",
            "call_function",
            lambda m: {"name": m.group(1), "arguments": ""},
        ),
        (
            r"^add call function (\w+) with (.+)$",
            "call_function",
            lambda m: {"name": m.group(1), "arguments": m.group(2)},
        ),
        (r"^add return (.+)$", "return_value", lambda m: {"value": m.group(1)}),
        (
            r"^add input block for (\w+)$",
            "ask_input",
            lambda m: {"variable": m.group(1), "text": f"Enter {m.group(1)}: "},
        ),
        (
            r"^add number input block for (\w+)$",
            "ask_number_input",
            lambda m: {"variable": m.group(1), "text": f"Enter {m.group(1)}: "},
        ),
        (r"^add comment (.+)$", "comment_note", lambda m: {"text": m.group(1)}),
    ]
    for pattern, kind, values in patterns:
        match = re.match(pattern, text)
        if match:
            return kind, values(match) if callable(values) else values
    return None, {}


# Phrases that *enter* Audio Blocks Mode. Entry is deliberately restricted to a
# real spoken voice command so the Python editor stays the default and learners
# are never dropped into block mode by a stray typed phrase or restored session
# state. route_command refuses these unless source == "voice" (see below).
ENTER_PHRASES = {
    "enter block mode",
    "open block mode",
    "switch to block mode",
    "open audio blocks",
    "enter audio blocks",
    "switch to audio blocks",
    "start audio blocks mode",
}


def handles(text: str) -> bool:
    value = " ".join(str(text or "").lower().strip().rstrip(".!?").split())
    exact = {
        "enter block mode",
        "open block mode",
        "switch to block mode",
        "exit block mode",
        "switch to code mode",
        "what mode am i in",
        "list block categories",
        "what blocks can i add",
        "help with blocks",
        "read block workspace",
        "read block order",
        "read current block",
        "next block",
        "previous block",
        "first block",
        "last block",
        "where am i in blocks",
        "summarize blocks",
        "read nested blocks",
        "undo block change",
        "redo block change",
        "clear block workspace",
        "compile blocks to python",
        "convert blocks to code",
        "send blocks to editor",
        "preview generated code",
        "run blocks",
        "explain generated code",
        "compare blocks and code",
        "convert code to blocks",
        "import code into blocks",
        "explain why code cannot become blocks",
        "start block lesson",
        "next block lesson",
        "check block lesson",
        "give block lesson hint",
        "show block lesson solution",
        "exit block lesson",
        "export block project",
        "download block project",
        "export blocks and python",
    }
    return value in exact or value in ENTER_PHRASES or bool(
        re.match(
            r"^(?:list (?:output|variable|math|condition|loop|list|function|input|comment) blocks|read block \d+|read children of block \d+|move block \d+ (?:up|down|before block \d+|after block \d+)|(?:indent|outdent|delete) block \d+|put block \d+ inside (?:else of )?block \d+|remove block \d+ from loop|edit block \d+|set block \d+ (?:text|variable|condition) to .+|rename block variable \w+ to \w+|clear block \d+ value|add .+|set variable .+|append .+ to .+)$",
            value,
        )
    )


def route_command(
    text: str, code: str, mem: Dict[str, Any], source: str = "typed"
) -> Optional[Dict[str, Any]]:
    t = " ".join(str(text or "").lower().strip().rstrip(".!?").split())
    if not handles(t):
        return None
    existing = get_workspace(mem)
    overlaps_parsons = bool(
        t
        in {
            "read block order",
            "read current block",
            "next block",
            "previous block",
            "convert blocks to code",
        }
        or re.match(
            r"^(?:read block \d+|move block \d+ (?:up|down)|(?:indent|outdent) block \d+)$",
            t,
        )
    )
    if overlaps_parsons and (not existing or existing.get("mode") != "audio_blocks"):
        return None
    workspace = get_workspace(mem, create=True)
    assert workspace is not None

    if t in ENTER_PHRASES:
        already_in_blocks = workspace.get("mode") == "audio_blocks"
        # Only a real spoken command may enter Audio Blocks Mode. Typed or
        # unknown sources are refused so the Python editor stays the default.
        # Re-issuing the phrase while already inside block mode is harmless.
        if source != "voice" and not already_in_blocks:
            return _message(
                "Audio Blocks Mode can only be opened by voice. "
                "Press the voice button and say open audio blocks.",
                workspace,
            )
        workspace["mode"] = "audio_blocks"
        status = (
            "Blocks have changed and are not compiled yet."
            if workspace.get("dirty")
            else "The block workspace is ready."
        )
        return _message(
            f"Audio Blocks Mode is on. {status} Say list block categories or add print block.",
            workspace,
        )
    if t in {"exit block mode", "switch to code mode"}:
        workspace["mode"] = "code"
        status = (
            " Blocks have changed but code has not been generated yet."
            if workspace.get("dirty")
            else ""
        )
        return _message(
            f"Code Mode is on. Your Python editor is unchanged.{status}", workspace
        )
    if t == "what mode am i in":
        name = (
            "Audio Blocks Mode"
            if workspace.get("mode") == "audio_blocks"
            else "Code Mode"
        )
        return _message(f"You are in {name}.", workspace)

    if t in {"list block categories", "what blocks can i add", "help with blocks"}:
        return _message(
            "Categories are output, variables, math, conditions, loops, lists, functions, input, and comments. Say list loop blocks.",
            workspace,
        )
    match = re.match(
        r"^list (output|variable|math|condition|loop|list|function|input|comment) blocks$",
        t,
    )
    if match:
        aliases = {
            "variable": "variables",
            "condition": "conditions",
            "loop": "loops",
            "list": "lists",
            "function": "functions",
            "comment": "comments",
        }
        category = aliases.get(match.group(1), match.group(1))
        labels = [label for _kind, label in CATALOG[category]]
        return _message(
            f"{category.title()} blocks include {', '.join(labels[:4])}."
            + (" Say help with blocks for categories." if len(labels) > 4 else ""),
            workspace,
        )

    kind, slots = _parse_add(t)
    if kind:
        block, error = add_block(workspace, kind, slots)
        return _message(
            error if not block else f"Added block {block['id']}: {block['label']}.",
            workspace,
        )

    match = re.match(r"^set block (\d+) (text|variable|condition) to (.+)$", t)
    if match:
        key = match.group(2)
        block, error = edit_block(workspace, int(match.group(1)), {key: match.group(3)})
        return _message(
            error if not block else f"Updated block {block['id']}: {block['label']}.",
            workspace,
        )
    match = re.match(r"^rename block variable (\w+) to (\w+)$", t)
    if match:
        old, new = match.groups()
        if not valid_name(new):
            return _message(f"I cannot use {new} as a variable name.", workspace)
        changed = 0
        for block in list(workspace["blocks"]):
            updates = {
                key: new
                for key, value in block["slots"].items()
                if key in {"variable", "item"} and value == old
            }
            if updates:
                edit_block(workspace, block["id"], updates)
                changed += 1
        return _message(
            f"Renamed {old} to {new} in {changed} block{'s' if changed != 1 else ''}.",
            workspace,
        )
    match = re.match(r"^clear block (\d+) value$", t)
    if match:
        block = _find(workspace, int(match.group(1)))
        if not block:
            return _message(f"Block {match.group(1)} does not exist.", workspace)
        keys = ("value", "text", "expression", "condition", "times", "variable")
        key = next((name for name in keys if name in block["slots"]), "")
        if not key:
            return _message(f"Block {block['id']} has no clearable value.", workspace)
        _before_change(workspace)
        block["slots"][key] = ""
        block["label"] = f"{BLOCK_LABELS[block['type']]} incomplete"
        block["incomplete"] = True
        workspace["cursor_id"] = block["id"]
        _changed(workspace)
        return _message(
            f"Cleared block {block['id']} {key}. Set it before compiling.", workspace
        )
    match = re.match(r"^edit block (\d+)$", t)
    if match:
        block = _find(workspace, int(match.group(1)))
        return _message(
            _read_block(workspace, block)
            + " Say set block number and the slot to change."
            if block
            else f"Block {match.group(1)} does not exist.",
            workspace,
        )

    if t in {"read block workspace", "read block order", "summarize blocks"}:
        blocks = workspace.get("blocks", [])
        if not blocks:
            return _message("The Audio Blocks workspace is empty.", workspace)
        summary = " ".join(f"Block {b['id']}: {b['label']}." for b in blocks)
        return _message(f"Workspace has {len(blocks)} blocks. {summary}", workspace)
    if t in {"read current block", "where am i in blocks"}:
        block = _find(workspace, int(workspace.get("cursor_id") or 0))
        return _message(
            _read_block(workspace, block)
            if block
            else "The Audio Blocks workspace is empty.",
            workspace,
        )
    match = re.match(r"^read block (\d+)$", t)
    if match:
        block = _find(workspace, int(match.group(1)))
        if block:
            workspace["cursor_id"] = block["id"]
        return _message(
            _read_block(workspace, block)
            if block
            else f"Block {match.group(1)} does not exist.",
            workspace,
        )
    if t in {"next block", "previous block", "first block", "last block"}:
        blocks = workspace.get("blocks", [])
        if not blocks:
            return _message("The Audio Blocks workspace is empty.", workspace)
        current = next(
            (i for i, b in enumerate(blocks) if b["id"] == workspace.get("cursor_id")),
            0,
        )
        if t == "next block":
            current = min(len(blocks) - 1, current + 1)
        elif t == "previous block":
            current = max(0, current - 1)
        elif t == "first block":
            current = 0
        else:
            current = len(blocks) - 1
        workspace["cursor_id"] = blocks[current]["id"]
        return _message(
            f"Current block is {current + 1} of {len(blocks)}. {_read_block(workspace, blocks[current])}",
            workspace,
        )
    match = re.match(r"^read children of block (\d+)$", t)
    if match:
        children = _children(workspace, int(match.group(1)), "body") + _children(
            workspace, int(match.group(1)), "else"
        )
        return _message(
            " ".join(_read_block(workspace, b) for b in children)
            if children
            else f"Block {match.group(1)} has no child blocks.",
            workspace,
        )
    if t == "read nested blocks":
        nested = [
            b for b in workspace.get("blocks", []) if b.get("parent_id") is not None
        ]
        return _message(
            " ".join(_read_block(workspace, b) for b in nested)
            if nested
            else "There are no nested blocks.",
            workspace,
        )

    match = re.match(r"^move block (\d+) (up|down)$", t)
    if match:
        return _message(
            move_relative(workspace, int(match.group(1)), match.group(2)), workspace
        )
    match = re.match(r"^move block (\d+) (before|after) block (\d+)$", t)
    if match:
        return _message(
            move_before_after(
                workspace, int(match.group(1)), int(match.group(3)), match.group(2)
            ),
            workspace,
        )
    match = re.match(r"^put block (\d+) inside (else of )?block (\d+)$", t)
    if match:
        return _message(
            nest_block(
                workspace,
                int(match.group(1)),
                int(match.group(3)),
                branch="else" if match.group(2) else "body",
            ),
            workspace,
        )
    match = re.match(r"^remove block (\d+) from loop$", t)
    if match:
        return _message(outdent_block(workspace, int(match.group(1))), workspace)
    match = re.match(r"^(indent|outdent|delete) block (\d+)$", t)
    if match:
        action, block_id = match.group(1), int(match.group(2))
        result = (
            indent_block(workspace, block_id)
            if action == "indent"
            else outdent_block(workspace, block_id)
            if action == "outdent"
            else delete_block(workspace, block_id)
        )
        return _message(result, workspace)
    if t == "undo block change":
        return _message(undo(workspace), workspace)
    if t == "redo block change":
        return _message(redo(workspace), workspace)
    if t == "clear block workspace":
        _before_change(workspace)
        workspace["blocks"] = []
        workspace["cursor_id"] = None
        _changed(workspace)
        return _message(
            "Cleared the Audio Blocks workspace. The Python editor was unchanged.",
            workspace,
        )

    if t in {
        "compile blocks to python",
        "convert blocks to code",
        "send blocks to editor",
    }:
        generated, error = compile_workspace(workspace)
        return (
            _message(error, workspace)
            if not generated
            else _edit_response(
                generated,
                "Compiled Audio Blocks into Python and sent the code to the editor.",
                workspace,
            )
        )
    if t == "preview generated code":
        generated, error = compile_workspace(workspace)
        return _message(
            error if not generated else f"Generated Python preview:\n{generated}",
            workspace,
            code_preview=generated or "",
        )
    if t == "run blocks":
        generated, error = compile_workspace(workspace)
        if not generated:
            return _message(error, workspace)
        return {
            "success": True,
            "action": "audio_blocks_run",
            "code": generated,
            "message": "Compiled Audio Blocks and running them through the CodeUp Python runner.",
            "speech": "Compiled Audio Blocks and running them through the CodeUp Python runner.",
            "audio_blocks": public_workspace(workspace),
        }
    if t == "explain generated code":
        generated, error = compile_workspace(workspace)
        return _message(
            error
            if not generated
            else f"The generated program has {len(generated.splitlines())} Python lines from {len(workspace['blocks'])} blocks. Nested blocks become indented Python suites.",
            workspace,
        )
    if t == "compare blocks and code":
        generated, error = compile_workspace(workspace)
        if not generated:
            return _message(error, workspace)
        same = (
            ast.dump(ast.parse(generated)) == ast.dump(ast.parse(code or ""))
            if code.strip()
            else False
        )
        return _message(
            "The editor matches the generated block code."
            if same
            else "The editor differs from the generated block code. Compile blocks to replace it explicitly.",
            workspace,
        )
    if t in {"convert code to blocks", "import code into blocks"}:
        success, message = import_python(workspace, code)
        return _message(message, workspace, imported=success)
    if t == "explain why code cannot become blocks":
        probe = new_workspace()
        success, message = import_python(probe, code)
        return _message(
            "This code can become blocks safely." if success else message, workspace
        )

    if t == "start block lesson":
        workspace["mode"] = "audio_blocks"
        workspace["lesson"] = {"index": 0}
        workspace["blocks"] = []
        workspace["next_id"] = 1
        workspace["cursor_id"] = None
        title, _solution, goal, _hint = LESSONS[0]
        return _message(f"Block lesson 1: {title}. Goal: {goal}", workspace)
    if t == "next block lesson":
        index = min(
            len(LESSONS) - 1, int((workspace.get("lesson") or {}).get("index", 0)) + 1
        )
        workspace["lesson"] = {"index": index}
        workspace["blocks"] = []
        workspace["next_id"] = 1
        workspace["cursor_id"] = None
        title, _solution, goal, _hint = LESSONS[index]
        return _message(f"Block lesson {index + 1}: {title}. Goal: {goal}", workspace)
    if t == "exit block lesson":
        workspace["lesson"] = None
        return _message(
            "Block lesson closed. Your workspace remains available.", workspace
        )
    if t in {
        "check block lesson",
        "give block lesson hint",
        "show block lesson solution",
    }:
        lesson = workspace.get("lesson")
        if not lesson:
            return _message(
                "No block lesson is active. Say start block lesson.", workspace
            )
        index = int(lesson["index"])
        title, expected, goal, hint = LESSONS[index]
        if t == "give block lesson hint":
            return _message(hint, workspace)
        if t == "show block lesson solution":
            load_lesson_solution(workspace, index)
            generated, _error = compile_workspace(workspace)
            return _message(
                f"Loaded the solution for {title}. Generated Python:\n{generated}",
                workspace,
            )
        expected_workspace = new_workspace()
        load_lesson_solution(expected_workspace, index)
        passed = _lesson_signature(workspace) == _lesson_signature(expected_workspace)
        generated, _error = compile_workspace(workspace) if passed else (None, "")
        return _message(
            f"Block lesson passed. Generated Python:\n{generated}"
            if passed
            else f"Not yet. Goal: {goal} Hint: {hint}",
            workspace,
            lesson_passed=passed,
        )

    if t in {
        "export block project",
        "download block project",
        "export blocks and python",
    }:
        files, error = export_files(workspace)
        if not files:
            return _message(error, workspace)
        return {
            "success": True,
            "action": "export_audio_blocks",
            "message": "Preparing a safe Audio Blocks project export.",
            "speech": "Preparing a safe Audio Blocks project export.",
            "audio_blocks": public_workspace(workspace),
        }
    return None
