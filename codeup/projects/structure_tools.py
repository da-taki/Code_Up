from __future__ import annotations

import ast
import io
import re
import tokenize
from typing import Any, Dict, List, Optional

_NUM_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
              7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _count_word(n: int) -> str:
    return _NUM_WORDS.get(n, str(n))


def _safe_parse(code: str) -> Optional[ast.AST]:
    try:
        return ast.parse(code or "")
    except SyntaxError:
        return None


def _end_line(node: ast.AST) -> int:
    return int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0)


def _preview(code_lines: List[str], line: int, end_line: int) -> str:
    for idx in range(line - 1, min(end_line, len(code_lines))):
        text = code_lines[idx].strip()
        if text:
            return text[:80]
    return ""


_BLOCK_TYPES = {
    ast.For: "for loop", ast.AsyncFor: "for loop", ast.While: "while loop",
    ast.If: "condition", ast.FunctionDef: "function", ast.AsyncFunctionDef: "function",
    ast.ClassDef: "class", ast.Try: "try/except", ast.With: "with block",
    ast.AsyncWith: "with block",
}


def _block_label(node: ast.AST) -> str:
    for cls, label in _BLOCK_TYPES.items():
        if isinstance(node, cls):
            return label
    return "block"


def _collect_blocks(tree: ast.AST, code_lines: List[str]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, tuple(_BLOCK_TYPES.keys())):
            line = int(getattr(node, "lineno", 0) or 0)
            if not line:
                continue
            end = _end_line(node)
            name = getattr(node, "name", "") if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ""
            blocks.append({
                "type": _block_label(node),
                "name": name,
                "line": line,
                "end_line": end,
                "preview": _preview(code_lines, line, end),
            })
    blocks.sort(key=lambda b: b["line"])
    return blocks


def _nesting_depth(tree: ast.AST) -> int:
    best = 0

    def walk(node, depth):
        nonlocal best
        for child in ast.iter_child_nodes(node):
            if isinstance(child, tuple(_BLOCK_TYPES.keys())):
                best = max(best, depth + 1)
                walk(child, depth + 1)
            else:
                walk(child, depth)

    walk(tree, 0)
    return best


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def build_structure_snapshot(code: str) -> Dict[str, Any]:
    code = code or ""
    code_lines = code.splitlines()
    line_count = len(code_lines)

    if not code.strip():
        return {"summary": "There is no code to summarize yet.", "items": [],
                "blocks": [], "concepts": [], "line_count": 0, "has_code": False}

    tree = _safe_parse(code)
    if tree is None:
        cues = []
        if re.search(r"\bdef\s+\w+", code):
            cues.append("a function")
        if re.search(r"\bfor\b|\bwhile\b", code):
            cues.append("a loop")
        if re.search(r"\bprint\s*\(", code):
            cues.append("a print statement")
        detail = (" I can see " + ", ".join(cues) + ".") if cues else ""
        return {
            "summary": "This program has a syntax error, so I cannot fully parse it." + detail,
            "items": [], "blocks": [], "concepts": ["syntax error"],
            "line_count": line_count, "has_code": True, "has_error": True,
        }

    imports, functions, classes, loops, conditions = [], [], [], [], []
    prints = inputs = calls = tries = assigns = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(int(getattr(node, "lineno", 0) or 0))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({"type": "function", "name": node.name, "line": node.lineno})
        elif isinstance(node, ast.ClassDef):
            classes.append({"type": "class", "name": node.name, "line": node.lineno})
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            loops.append({"type": "loop", "name": "", "line": node.lineno})
        elif isinstance(node, ast.If):
            conditions.append({"type": "condition", "name": "", "line": node.lineno})
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            assigns += 1
        elif isinstance(node, ast.Try):
            tries += 1
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "print":
                prints += 1
            elif name == "input":
                inputs += 1
            else:
                calls += 1

    blocks = _collect_blocks(tree, code_lines)
    depth = _nesting_depth(tree)

    def _phrase(n, singular, plural=None):
        plural = plural or (singular + "s")
        return f"{_count_word(n)} {singular if n == 1 else plural}"

    pieces = []
    if functions:
        names = ", ".join(f["name"] for f in functions[:3])
        pieces.append(_phrase(len(functions), "function") + (f" named {names}" if len(functions) <= 3 else ""))
    if classes:
        pieces.append(_phrase(len(classes), "class", "classes"))
    if loops:
        pieces.append(_phrase(len(loops), "loop"))
    if conditions:
        pieces.append(_phrase(len(conditions), "condition"))
    if prints:
        pieces.append(_phrase(prints, "print statement"))
    if inputs:
        pieces.append(_phrase(inputs, "input call"))
    if calls:
        pieces.append(_phrase(calls, "function call"))
    if imports:
        pieces.append(_phrase(len(imports), "import"))
    if assigns:
        pieces.append(_phrase(assigns, "assignment"))

    if pieces:
        summary = "This program has " + _join(pieces) + f". It is {line_count} lines long."
    else:
        summary = f"This program is {line_count} lines long with simple top-level statements."
    if depth >= 2:
        summary += f" The deepest nesting is {depth} levels."
    if tries:
        summary += " It uses try/except for error handling."

    items = sorted(functions + classes + loops + conditions, key=lambda x: x["line"])
    concepts = _concepts(imports, assigns, loops, conditions, functions, classes, prints, inputs, tries)

    return {
        "summary": summary,
        "items": items,
        "blocks": blocks,
        "concepts": concepts,
        "counts": {
            "functions": len(functions), "classes": len(classes), "loops": len(loops),
            "conditions": len(conditions), "prints": prints, "inputs": inputs,
            "calls": calls, "imports": len(imports), "assignments": assigns, "try_except": tries,
        },
        "nesting_depth": depth,
        "line_count": line_count,
        "has_code": True,
    }


def _build_hierarchy(node: ast.AST) -> List[Dict[str, Any]]:
    children: List[Dict[str, Any]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, tuple(_BLOCK_TYPES.keys())):
            name = getattr(child, "name", "") if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ""
            kind = _block_label(child)
            children.append({
                "label": f"{kind} {name}" if name else kind,
                "line": int(getattr(child, "lineno", 0) or 0),
                "end_line": _end_line(child),
                "children": _build_hierarchy(child),
            })
        else:
            children.extend(_build_hierarchy(child))
    return children


def _speak_hierarchy(nodes: List[Dict[str, Any]], depth: int = 0) -> List[str]:
    lines: List[str] = []
    for n in nodes:
        prefix = "At the top level, " if depth == 0 else f"At depth {depth}, inside it, "
        lines.append(f"{prefix}{n['label']}, lines {n['line']} to {n['end_line']}.")
        lines.extend(_speak_hierarchy(n["children"], depth + 1))
    return lines


def code_map_hierarchy(code: str) -> Dict[str, Any]:
    """A NESTED code map: every function/class/loop/condition/try/with block with
    its own start-end line range, and its children -- so a learner can hear which
    blocks are inside which, not just a flat 'N loops, M functions' count.
    Deterministic, AST-based, no AI. Spoken linearly (outer to inner, in source
    order) since a tree cannot be read aloud as a picture.
    """
    tree = _safe_parse(code)
    if tree is None:
        return {"hierarchy": [], "speech": "I cannot read the structure because of a syntax error."}
    hierarchy = _build_hierarchy(tree)
    if not hierarchy:
        return {"hierarchy": [], "speech": "This program has no functions, loops, classes, or "
                                            "conditions to map -- just simple top-level statements."}
    lines = _speak_hierarchy(hierarchy)
    return {"hierarchy": hierarchy, "speech": " ".join(lines)}


def _join(pieces: List[str]) -> str:
    if len(pieces) == 1:
        return pieces[0]
    if len(pieces) == 2:
        return pieces[0] + " and " + pieces[1]
    return ", ".join(pieces[:-1]) + ", and " + pieces[-1]


def _concepts(imports, assigns, loops, conditions, functions, classes, prints, inputs, tries) -> List[str]:
    order = []
    if imports:
        order.append("imports")
    if assigns:
        order.append("variables")
    if loops:
        order.append("loops")
    if conditions:
        order.append("conditions")
    if functions:
        order.append("functions")
    if classes:
        order.append("classes")
    if prints:
        order.append("print output")
    if inputs:
        order.append("input")
    if tries:
        order.append("error handling")
    return order



_TARGET_BLOCKLABELS = {
    "function": {"function"},
    "loop": {"for loop", "while loop"},
    "for loop": {"for loop"},
    "while loop": {"while loop"},
    "condition": {"condition"},
    "if": {"condition"},
    "class": {"class"},
    "try": {"try/except"},
}


def navigate(code: str, target: str, *, cursor_line: Optional[int] = None,
             last_error_line: Optional[int] = None,
             last_block: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    code = code or ""
    code_lines = code.splitlines()
    target = (target or "").strip().lower()

    if not code.strip():
        return _nav_none("There is no code to navigate yet.")

    if target in ("error", "the error", "mistake", "bug"):
        if last_error_line and 1 <= last_error_line <= max(1, len(code_lines)):
            preview = code_lines[last_error_line - 1].strip() if last_error_line <= len(code_lines) else ""
            return _nav_hit(last_error_line, last_error_line, f"The last error was around line {last_error_line}.",
                            preview, "error", 1)
        return _nav_none("I do not have a recent error line. Run your code first so I can find it.")

    tree = _safe_parse(code)
    blocks = _collect_blocks(tree, code_lines) if tree is not None else []

    if "block" in target and any(w in target for w in ("next", "previous", "prev", "current", "this")):
        return _nav_relative_block(blocks, target, cursor_line, last_block)

    if "print" in target:
        hits = _find_print_lines(tree, code_lines) if tree is not None else []
        if hits:
            line = hits[0]
            return _nav_hit(line, line, _match_message("print statement", line, len(hits)),
                            code_lines[line - 1].strip() if line <= len(code_lines) else "", "print", len(hits))
        return _nav_none("I could not find a print statement in this program.")

    wanted = None
    for key, labels in _TARGET_BLOCKLABELS.items():
        if key in target:
            wanted = labels
            break
    if wanted is None:
        return _nav_none(f"I am not sure what to navigate to for '{target}'.")
    matches = [b for b in blocks if b["type"] in wanted]
    if not matches:
        nice = "loop" if "loop" in target else ("condition" if ("condition" in target or "if" in target) else target)
        return _nav_none(f"I could not find a {nice} in this program.")
    first = matches[0]
    block_text = "\n".join(code_lines[first["line"] - 1:first["end_line"]])
    kind = first["type"]
    return {
        "found": True, "action": "navigate_code", "line": first["line"],
        "end_line": first["end_line"], "block_type": kind, "count": len(matches),
        "message": _match_message(kind, first["line"], len(matches)),
        "code": block_text, "preview": first["preview"],
    }


def _nav_hit(line, end_line, message, preview, block_type, count) -> Dict[str, Any]:
    return {"found": True, "action": "navigate_code", "line": line, "end_line": end_line,
            "message": message, "code": preview, "preview": preview,
            "block_type": block_type, "count": count}


def _nav_none(message: str) -> Dict[str, Any]:
    return {"found": False, "action": "navigate_code", "line": None, "end_line": None,
            "message": message, "code": "", "preview": "", "block_type": "", "count": 0}


def _match_message(kind: str, line: int, count: int) -> str:
    if count > 1:
        return f"I found {_count_word(count)} {kind}s. The first one starts at line {line}."
    return f"The {kind} starts at line {line}."


def _find_print_lines(tree: ast.AST, code_lines: List[str]) -> List[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "print":
            lines.append(int(getattr(node, "lineno", 0) or 0))
    return sorted(set(n for n in lines if n))


def _nav_relative_block(blocks, target, cursor_line, last_block) -> Dict[str, Any]:
    if not blocks:
        return _nav_none("There are no blocks to read yet.")
    ref_line = cursor_line or (last_block or {}).get("line") or 1

    def emit(b, verb):
        return {"found": True, "action": "navigate_code", "line": b["line"], "end_line": b["end_line"],
                "message": f"{verb} {b['type']} starts at line {b['line']}.", "code": b.get("preview", ""),
                "preview": b.get("preview", ""), "block_type": b["type"], "count": len(blocks)}

    if "current" in target or "this" in target:
        cur = None
        for b in blocks:
            if b["line"] <= ref_line <= b["end_line"]:
                cur = b
        cur = cur or min(blocks, key=lambda b: abs(b["line"] - ref_line))
        return emit(cur, "The current")
    if "next" in target:
        after = [b for b in blocks if b["line"] > ref_line]
        if after:
            return emit(after[0], "The next")
        return _nav_none("You are already at the last block.")
    before = [b for b in blocks if b["line"] < ref_line]
    if before:
        return emit(before[-1], "The previous")
    return _nav_none("You are already at the first block.")



def find_symbol(code: str, name: str, mode: str = "used") -> Dict[str, Any]:
    code = code or ""
    name = (name or "").strip()
    mode = (mode or "used").lower()
    if not code.strip():
        return {"found": False, "name": name, "mode": mode, "lines": [],
                "message": "There is no code to search yet."}
    tree = _safe_parse(code)
    if tree is None or not name:
        return {"found": False, "name": name, "mode": mode, "lines": [],
                "message": f"I could not search for {name or 'that name'} in this program."}

    changed, used, defined = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            (changed if isinstance(node.ctx, ast.Store) else used).add(node.lineno)
        elif isinstance(node, ast.arg) and node.arg == name:
            defined.add(node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            defined.add(node.lineno)

    if mode in ("changed", "change", "updated", "assigned", "set"):
        lines, label = sorted(changed), "changed"
    elif mode in ("defined", "define", "created"):
        lines, label = sorted(changed | defined), "defined"
    else:
        lines, label = sorted(used | changed), "used"

    if not lines:
        return {"found": False, "name": name, "mode": label, "lines": [],
                "message": f"I could not find where {name} is {label} in this program."}
    first = lines[0]
    where = f"line {first}" if len(lines) == 1 else "lines " + ", ".join(str(n) for n in lines[:6])
    return {"found": True, "name": name, "mode": label, "lines": lines, "line": first,
            "action": "navigate_code", "end_line": first,
            "message": f"{name} is {label} on {where}."}


def _clamp_line(code_lines: List[str], line: Optional[int]) -> int:
    n = line if isinstance(line, int) and line >= 1 else 1
    return min(n, len(code_lines)) if code_lines else 1


def _speak_expr(expr: str) -> str:
    return re.sub(r"\s+", " ", str(expr or "").strip())[:80]


def _range_times(iterable: str) -> Optional[str]:
    match = re.fullmatch(r"range\s*\(\s*([^)]*)\)", iterable.strip())
    if not match:
        return None
    args = [a.strip() for a in match.group(1).split(",") if a.strip()]
    if not args or not all(re.fullmatch(r"-?\d+", a) for a in args):
        return "a number of times"
    nums = [int(a) for a in args]
    if len(args) == 1:
        count = max(0, nums[0])
    elif len(args) == 2:
        count = max(0, nums[1] - nums[0])
    else:
        return "a number of times"
    return "one time" if count == 1 else f"{_count_word(count)} times"


def explain_line(code: str, line: Optional[int]) -> str:
    """One short, spoken, deterministic explanation of a single source line."""
    code_lines = (code or "").splitlines()
    if not code_lines:
        return "There is no code to explain yet."
    n = _clamp_line(code_lines, line)
    raw = code_lines[n - 1]
    s = raw.strip()
    if not s:
        return "This line is blank."
    if s.startswith("#"):
        return "This line is a comment, so Python ignores it when the code runs."

    m = re.match(r"^for\s+(\w+)\s+in\s+(.+?):$", s)
    if m:
        times = _range_times(m.group(2))
        if times:
            return f"This starts a for loop. The indented lines after it run {times}."
        return ("This starts a for loop. The indented lines after it run once for each item in "
                f"{_speak_expr(m.group(2))}.")
    if re.match(r"^while\s+.+:$", s):
        return "This starts a while loop. The indented lines repeat while the condition is true."
    if re.match(r"^if\s+.+:$", s):
        return "This starts an if statement. The indented lines run only if the condition is true."
    if re.match(r"^elif\s+.+:$", s):
        return "This is an elif branch. Its lines run if the earlier conditions were false and this one is true."
    if re.match(r"^else\s*:$", s):
        return "This is an else branch. Its lines run when the earlier conditions were false."

    m = re.match(r"^def\s+(\w+)\s*\(", s)
    if m:
        return f"This defines a function named {m.group(1)}."
    m = re.match(r"^class\s+(\w+)", s)
    if m:
        return f"This defines a class named {m.group(1)}."
    if re.match(r"^return\b", s):
        return "This returns a value from the function."
    if re.match(r"^(?:import|from)\s+\w", s):
        return "This imports code from another module."

    m = re.match(r"^print\s*\((.*)\)$", s)
    if m:
        arg = m.group(1).strip()
        if not arg:
            return "This prints a blank line."
        if re.fullmatch(r"[A-Za-z_]\w*", arg):
            return f"This prints the value of {arg}."
        if re.fullmatch(r"""(['"]).*\1""", arg):
            return "This prints a fixed message."
        return "This prints a value to the screen."

    m = re.match(r"^([A-Za-z_]\w*)\s*\+=\s*(.+)$", s)
    if m:
        return f"This updates {m.group(1)} using its old value plus {_speak_expr(m.group(2))}."
    m = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", s)
    if m:
        name, rhs = m.group(1), m.group(2).strip()
        plus = re.match(rf"^{re.escape(name)}\s*\+\s*(.+)$", rhs)
        if plus:
            return f"This updates {name} using its old value plus {_speak_expr(plus.group(1))}."
        if re.search(rf"\b{re.escape(name)}\b", rhs):
            return f"This updates {name} using its old value."
        return f"This sets {name} to {_speak_expr(rhs)}."

    return f"This line runs: {_speak_expr(s)}."


def _spoken_source_line(raw: str) -> str:
    s = raw.strip()
    if not s:
        return "blank"
    indent = "indented " if raw[:1] in (" ", "\t") else ""
    body = re.sub(r"\s+", " ", s.replace("(", " ").replace(")", " ")).strip()
    if body.endswith(":"):
        body = body[:-1].strip() + ", colon"
    return indent + body


def read_around(code: str, line: Optional[int], radius: int = 2) -> str:
    """Read a couple of lines above and below the cursor, with line numbers."""
    code_lines = (code or "").splitlines()
    if not code_lines:
        return "There is no code to read yet."
    n = _clamp_line(code_lines, line)
    start = max(1, n - radius)
    end = min(len(code_lines), n + radius)
    parts = [f"Line {i}: {_spoken_source_line(code_lines[i - 1])}." for i in range(start, end + 1)]
    return " ".join(parts)


_CONTAINER_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.For, ast.AsyncFor,
                     ast.While, ast.If, ast.With, ast.AsyncWith, ast.Try, ast.ExceptHandler)


def _cond_text(code: str, node: Optional[ast.AST]) -> str:
    seg = None
    if node is not None:
        try:
            seg = ast.get_source_segment(code, node)
        except Exception:
            seg = None
    if seg:
        return re.sub(r"\s+", " ", seg.strip())
    return "the condition"


def _find_keyword_line(code_lines: List[str], start: int, end: int, keyword_text: str) -> Optional[int]:
    """Scan lines [start, end] (1-indexed, inclusive) for a line whose stripped text
    starts with keyword_text and ends with ':' -- AST records no dedicated node/lineno
    for bare 'else:'/'finally:' keywords, so this locates them by source scan."""
    for i in range(max(1, start), max(start, end) + 1):
        if 1 <= i <= len(code_lines):
            s = code_lines[i - 1].strip()
            if s.startswith(keyword_text) and s.rstrip().endswith(":"):
                return i
    return None


def _ancestor_chain(code: str, line: int) -> List[Dict[str, Any]]:
    """Ordered outer -> inner list of every block that contains `line`.

    Deterministic, AST-based (no AI). Each entry: {kind, line, name, label} where
    `label` is a short spoken phrase like "function analyze" or "the condition n > 5",
    and `line` is the exact start line of that block -- the full ancestor chain the
    'where am I' / 'why is this line indented' commands are built from.
    """
    tree = _safe_parse(code)
    if tree is None:
        return []
    code_lines = code.splitlines()
    chain: List[Dict[str, Any]] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            start = getattr(child, "lineno", None)
            stop = getattr(child, "end_lineno", None)
            if start is None or stop is None or not (start <= line <= stop):
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain.append({"kind": "function", "line": start, "name": child.name,
                              "label": f"function {child.name}"})
                visit(child)
            elif isinstance(child, ast.ClassDef):
                chain.append({"kind": "class", "line": start, "name": child.name,
                              "label": f"class {child.name}"})
                visit(child)
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                chain.append({"kind": "for loop", "line": start, "name": None,
                              "label": "the for loop"})
                visit(child)
            elif isinstance(child, ast.While):
                chain.append({"kind": "while loop", "line": start, "name": None,
                              "label": "the while loop"})
                visit(child)
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                chain.append({"kind": "with block", "line": start, "name": None,
                              "label": "the with block"})
                visit(child)
            elif isinstance(child, ast.If):
                body_end = _end_line(child.body[-1]) if child.body else start
                in_body = bool(child.body) and child.body[0].lineno <= line <= body_end
                if in_body or not child.orelse:
                    cond = _cond_text(code, child.test)
                    is_elif = start - 1 < len(code_lines) and code_lines[start - 1].strip().startswith("elif")
                    if is_elif:
                        chain.append({"kind": "elif", "line": start, "name": None,
                                      "label": f"the elif branch {cond}"})
                    else:
                        chain.append({"kind": "if", "line": start, "name": None,
                                      "label": f"the condition {cond}"})
                    visit(child)
                elif len(child.orelse) == 1 and isinstance(child.orelse[0], ast.If):
                    # elif -- represented as a nested If in orelse; it reports itself.
                    visit(child)
                else:
                    else_line = _find_keyword_line(code_lines, body_end + 1,
                                                    child.orelse[0].lineno, "else") or child.orelse[0].lineno
                    chain.append({"kind": "else", "line": else_line, "name": None,
                                  "label": "the else branch"})
                    visit(child)
            elif isinstance(child, ast.Try):
                body_end = _end_line(child.body[-1]) if child.body else start
                if bool(child.body) and child.body[0].lineno <= line <= body_end:
                    chain.append({"kind": "try", "line": start, "name": None, "label": "the try block"})
                visit(child)
            elif isinstance(child, ast.ExceptHandler):
                exc_type = _cond_text(code, child.type) if getattr(child, "type", None) else ""
                label = f"the except block for {exc_type}" if exc_type else "the except block"
                chain.append({"kind": "except", "line": start, "name": exc_type or None, "label": label})
                visit(child)
            else:
                visit(child)

    visit(tree)
    return chain


def _join_inside(phrases: List[str]) -> str:
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    return ", inside ".join(phrases[:-1]) + ", and inside " + phrases[-1]


def cursor_context(code: str, line: Optional[int]) -> Dict[str, Any]:
    """Deterministic 'where am I' data for the CURRENT cursor line: exact indentation
    depth (a number) plus the full ancestor chain of enclosing blocks (block types,
    names, and start lines) -- AST-based, no AI, no hallucination.
    """
    code_lines = (code or "").splitlines()
    if not code_lines:
        return {"found": False, "line": None, "depth": 0, "ancestors": [],
                "speech": "There is no code yet."}
    tree = _safe_parse(code)
    if tree is None:
        return {"found": False, "line": None, "depth": 0, "ancestors": [],
                "speech": "I cannot tell you where you are because of a syntax error. "
                          "Fix the syntax error first, then ask again."}
    n = _clamp_line(code_lines, line)
    raw = code_lines[n - 1]
    ancestors = _ancestor_chain(code, n)
    depth = len(ancestors)
    indent_spaces = len(raw) - len(raw.lstrip(" \t"))

    if not ancestors:
        speech = f"Line {n}. Indentation depth 0. You are at the top level of the file."
    else:
        phrases = [f"{a['label']} on line {a['line']}" for a in ancestors]
        speech = f"Line {n}. Indentation depth {depth}. You are inside {_join_inside(phrases)}."

    return {"found": True, "line": n, "depth": depth, "indent_spaces": indent_spaces,
            "ancestors": ancestors, "speech": speech}


def why_indented(code: str, line: Optional[int]) -> str:
    """Explain WHY a line is indented: which block it belongs to (semantic ownership),
    not just how many spaces it has."""
    code_lines = (code or "").splitlines()
    if not code_lines:
        return "There is no code yet."
    tree = _safe_parse(code)
    if tree is None:
        return "I cannot explain the indentation because of a syntax error."
    n = _clamp_line(code_lines, line)
    raw = code_lines[n - 1]
    indent_spaces = len(raw) - len(raw.lstrip(" \t"))
    ancestors = _ancestor_chain(code, n)
    if not ancestors:
        if indent_spaces:
            return (f"Line {n} is indented {indent_spaces} spaces, but it does not belong to any "
                    "block I can identify.")
        return f"Line {n} is not indented. It is at the top level of the file."
    innermost = ancestors[-1]
    reason = (f"This line is indented {indent_spaces} spaces because it belongs to "
              f"{innermost['label']} on line {innermost['line']}.")
    if len(ancestors) > 1:
        parent = ancestors[-2]
        reason += f" That block is itself inside {parent['label']} on line {parent['line']}."
    return reason


def indentation_level(code: str, line: Optional[int]) -> str:
    """'How deep am I' / 'what is my indentation level', at the CURRENT cursor line
    (not the whole file's maximum nesting depth)."""
    code_lines = (code or "").splitlines()
    if not code_lines:
        return "There is no code yet."
    tree = _safe_parse(code)
    if tree is None:
        return "I cannot compute indentation depth because of a syntax error."
    n = _clamp_line(code_lines, line)
    raw = code_lines[n - 1]
    indent_spaces = len(raw) - len(raw.lstrip(" \t"))
    depth = len(_ancestor_chain(code, n))
    if depth == 0:
        return f"Line {n} is indented {indent_spaces} spaces. It is at the top level of the file, depth 0."
    return f"Line {n} is indented {indent_spaces} spaces, which is indentation depth {depth}."


def innermost_block_label(code: str, line: Optional[int]) -> Optional[str]:
    """The label of the block immediately containing `line` (e.g. 'the condition
    n > 5', 'function analyze'), or None if it's at the top level or the code
    does not parse. Used by audio_diff to explain indentation changes
    semantically (which block a line moved into/out of) instead of just
    reporting a spaces-changed count."""
    code_lines = (code or "").splitlines()
    if not code_lines:
        return None
    tree = _safe_parse(code)
    if tree is None:
        return None
    n = _clamp_line(code_lines, line)
    ancestors = _ancestor_chain(code, n)
    return ancestors[-1]["label"] if ancestors else None


def what_contains(code: str, line: Optional[int]) -> str:
    """'What block contains this line' / 'what contains this line' -- the immediate
    (innermost) enclosing block, named, with its start line."""
    code_lines = (code or "").splitlines()
    if not code_lines:
        return "There is no code yet."
    tree = _safe_parse(code)
    if tree is None:
        return "I cannot tell what contains this line because of a syntax error."
    n = _clamp_line(code_lines, line)
    ancestors = _ancestor_chain(code, n)
    if not ancestors:
        return f"Line {n} is at the top level of the file. Nothing else contains it."
    innermost = ancestors[-1]
    return f"Line {n} is inside {innermost['label']}, which starts on line {innermost['line']}."


def _innermost_container(tree: ast.AST, line: int) -> Optional[ast.AST]:
    best: Optional[ast.AST] = None

    def visit(node: ast.AST) -> None:
        nonlocal best
        for child in ast.iter_child_nodes(node):
            start = getattr(child, "lineno", None)
            stop = getattr(child, "end_lineno", None)
            if (isinstance(child, _CONTAINER_TYPES) and start is not None and stop is not None
                    and start <= line <= stop):
                best = child
            visit(child)

    visit(tree)
    return best


def describe_contents(code: str, line: Optional[int]) -> str:
    """'What is inside this loop/condition/function' at the cursor -- lists the
    direct child statements of the block containing the cursor, deterministically."""
    code_lines = (code or "").splitlines()
    if not code_lines:
        return "There is no code yet."
    tree = _safe_parse(code)
    if tree is None:
        return "I cannot read the structure because of a syntax error."
    n = _clamp_line(code_lines, line)
    container = _innermost_container(tree, n)
    if container is None:
        return "That is at the top level of the file, not inside a block."
    kind = _block_label(container)
    name = getattr(container, "name", "") or ""
    label = f"{kind} {name}" if name else kind
    body = list(getattr(container, "body", []))
    if not body:
        return f"The {label} starting on line {container.lineno} has no statements inside it yet."
    shown = body[:6]
    parts = [f"line {getattr(stmt, 'lineno', '?')}: {explain_line(code, getattr(stmt, 'lineno', None))}"
             for stmt in shown]
    tail = ""
    if len(body) > len(shown):
        remaining = len(body) - len(shown)
        tail = f" And {_count_word(remaining)} more line{'s' if remaining != 1 else ''}."
    result = f"Inside the {label}, starting on line {container.lineno}: " + " ".join(parts) + tail
    return result if result.endswith(".") else result + "."


def _header_kind(text: str) -> str:
    text = text.strip()
    if text.startswith("async def ") or text.startswith("def "):
        return "a function"
    if text.startswith("class "):
        return "a class"
    if text.startswith("async for ") or text.startswith("for "):
        return "a for loop"
    if text.startswith("while "):
        return "a while loop"
    if text.startswith("elif "):
        return "an elif block"
    if text.startswith("if "):
        return "an if block"
    if text.startswith("else"):
        return "an else block"
    if text.startswith("finally"):
        return "a finally block"
    if text.startswith("except"):
        return "an except block"
    if text.startswith("try"):
        return "a try block"
    if text.startswith("async with ") or text.startswith("with "):
        return "a with block"
    return "a block"


def explain_indentation_error(code: str) -> Optional[str]:
    """If `code` fails to parse with an 'expected an indented block' IndentationError,
    return a precise deterministic explanation naming both the offending line and the
    block header above it that required indentation. Returns None if the code parses,
    or if the SyntaxError is not this specific indentation shape (caller should fall
    back to its own generic syntax-error message in that case).
    """
    try:
        ast.parse(code or "")
        return None
    except IndentationError as exc:
        msg = str(exc.msg or "")
        lines = (code or "").splitlines()
        err_line = exc.lineno or 1
        if "expected an indented block" in msg:
            header_line = None
            for i in range(err_line - 1, 0, -1):
                if i <= len(lines) and lines[i - 1].rstrip().endswith(":"):
                    header_line = i
                    break
            if header_line:
                kind = _header_kind(lines[header_line - 1])
                return f"Line {err_line} should be indented because line {header_line} starts {kind}."
            return f"Line {err_line} should be indented, but I could not find the block header above it."
        return f"There is an indentation problem near line {err_line}: {msg}."
    except SyntaxError:
        return None


def assigned_variable_names(code: str) -> List[str]:
    """AST names assigned anywhere in the code, including loop targets, in source order."""
    tree = _safe_parse(code)
    if tree is None:
        return []
    first_line: Dict[str, int] = {}

    def note(name: str, lineno: int) -> None:
        if name and name != "_":
            ln = lineno or 0
            if name not in first_line or ln < first_line[name]:
                first_line[name] = ln

    def add_target(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            note(target.id, getattr(target, "lineno", 0))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                add_target(elt)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                add_target(target)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            note(node.target.id, getattr(node, "lineno", 0))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            add_target(node.target)
        elif isinstance(node, ast.comprehension):
            add_target(node.target)

    return [name for name, _ln in sorted(first_line.items(), key=lambda kv: (kv[1], kv[0]))]


def list_variables_speech(code: str) -> str:
    names = assigned_variable_names(code)
    if not names:
        return "I do not see any variables yet."
    if len(names) == 1:
        return f"You have 1 variable: {names[0]}."
    if len(names) == 2:
        listed = f"{names[0]} and {names[1]}"
    else:
        listed = ", ".join(names[:-1]) + ", and " + names[-1]
    return f"You have {len(names)} variables: {listed}."


def collect_comments(code: str) -> List[Dict[str, Any]]:
    """Return [{line, text}] for every comment. tokenize first, regex fallback."""
    comments: List[Dict[str, Any]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code or "").readline):
            if tok.type == tokenize.COMMENT:
                comments.append({"line": tok.start[0], "text": tok.string.lstrip("#").strip()})
        return comments
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        comments = []
        for i, raw in enumerate((code or "").splitlines(), start=1):
            # Best-effort: a # that is not inside an obvious string literal.
            m = re.search(r"(?<!['\"])#(.*)$", raw)
            if m and raw.count("'") % 2 == 0 and raw.count('"') % 2 == 0:
                comments.append({"line": i, "text": m.group(1).strip()})
        return comments


def read_comments_speech(code: str) -> str:
    comments = collect_comments(code)
    if not comments:
        return "There are no comments in this code yet."
    n = len(comments)
    head = f"There {'is' if n == 1 else 'are'} {n} comment{'s' if n != 1 else ''}. "
    shown = comments[:6]
    bits = [f"Line {c['line']}: {c['text'] or '(empty comment)'}" for c in shown]
    tail = f" And {n - len(shown)} more." if n > len(shown) else ""
    return head + ". ".join(bits) + "." + tail
