"""
Non-visual code understanding: deterministic structure snapshot + navigation.

Pure / Flask-free and AST-based. Powers three Sprint-2 features:
  * build_structure_snapshot(code) — a short spoken/visible overview of what is
    in a program (functions, loops, conditions, prints, calls, imports, ...).
  * navigate(code, target, ...) — find the first function/loop/condition/print/
    error/etc. (or next/previous/current block) and return its line + block text.
  * find_symbol(code, name, mode) — where a variable is used / changed / defined.

Everything is derived from the AST (with safe regex fallbacks for unparseable
code). Nothing is invented and no cloud AI is involved.
"""

from __future__ import annotations

import ast
import re
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
    """A short one-line preview of a block (first non-empty line, trimmed)."""
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
    """All compound blocks (functions, loops, conditions, ...) sorted by line."""
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
    """Maximum nesting depth of compound blocks."""
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
    """Return a deterministic structure overview of ``code``."""
    code = code or ""
    code_lines = code.splitlines()
    line_count = len(code_lines)

    if not code.strip():
        return {"summary": "There is no code to summarize yet.", "items": [],
                "blocks": [], "concepts": [], "line_count": 0, "has_code": False}

    tree = _safe_parse(code)
    if tree is None:
        # Unparseable: light regex cues so we still say something useful.
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

    # Build the spoken summary from real counts.
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


# ---------------------------------------------------------------------------
# Navigation by meaning
# ---------------------------------------------------------------------------

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
    """Find a code target and return navigation info (deterministic)."""
    code = code or ""
    code_lines = code.splitlines()
    target = (target or "").strip().lower()

    if not code.strip():
        return _nav_none("There is no code to navigate yet.")

    # The error target uses the last run's error line, not the AST.
    if target in ("error", "the error", "mistake", "bug"):
        if last_error_line and 1 <= last_error_line <= max(1, len(code_lines)):
            preview = code_lines[last_error_line - 1].strip() if last_error_line <= len(code_lines) else ""
            return _nav_hit(last_error_line, last_error_line, f"The last error was around line {last_error_line}.",
                            preview, "error", 1)
        return _nav_none("I do not have a recent error line. Run your code first so I can find it.")

    tree = _safe_parse(code)
    blocks = _collect_blocks(tree, code_lines) if tree is not None else []

    # next / previous / current block relative to cursor (or last navigated block).
    if "block" in target and any(w in target for w in ("next", "previous", "prev", "current", "this")):
        return _nav_relative_block(blocks, target, cursor_line, last_block)

    # print statement target (prints are calls, not compound blocks).
    if "print" in target:
        hits = _find_print_lines(tree, code_lines) if tree is not None else []
        if hits:
            line = hits[0]
            return _nav_hit(line, line, _match_message("print statement", line, len(hits)),
                            code_lines[line - 1].strip() if line <= len(code_lines) else "", "print", len(hits))
        return _nav_none("I could not find a print statement in this program.")

    # Compound-block targets.
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
    # previous
    before = [b for b in blocks if b["line"] < ref_line]
    if before:
        return emit(before[-1], "The previous")
    return _nav_none("You are already at the first block.")


# ---------------------------------------------------------------------------
# Symbol search: where is X used / changed / defined
# ---------------------------------------------------------------------------

def find_symbol(code: str, name: str, mode: str = "used") -> Dict[str, Any]:
    """Find lines where ``name`` is used / changed / defined."""
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
