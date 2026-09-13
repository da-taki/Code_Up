"""Deterministic, screen-reader-friendly Python explanations.

This module deliberately sits behind the existing ``analyze`` and
``analyze_deep`` actions.  It is not another intent router.  Structural facts
come from Python's AST/tokenizer and the stable ``structure_tools`` helpers;
no AI provider is involved.
"""

from __future__ import annotations

import ast
import io
import token
import tokenize
from typing import Any, Dict, Iterable, List, Optional

from codeup.projects import structure_tools


_BLOCK_LABELS = {
    ast.For: "for loop",
    ast.AsyncFor: "for loop",
    ast.While: "while loop",
    ast.If: "if statement",
    ast.FunctionDef: "function",
    ast.AsyncFunctionDef: "function",
    ast.ClassDef: "class",
    ast.Try: "try block",
    ast.With: "with block",
    ast.AsyncWith: "with block",
}


def _parse(code: str) -> Optional[ast.AST]:
    try:
        return ast.parse(code or "")
    except SyntaxError:
        return None


def _block_label(node: ast.AST) -> str:
    base = next((label for cls, label in _BLOCK_LABELS.items() if isinstance(node, cls)), "block")
    name = getattr(node, "name", "")
    return f"{base} {name}" if name else base


def _containing_block(tree: Optional[ast.AST], line: int) -> Optional[ast.AST]:
    if tree is None:
        return None
    matches: List[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, tuple(_BLOCK_LABELS)):
            continue
        start = int(getattr(node, "lineno", 0) or 0)
        end = int(getattr(node, "end_lineno", start) or start)
        if start < line <= end:
            matches.append(node)
    if not matches:
        return None
    return max(matches, key=lambda node: int(getattr(node, "lineno", 0) or 0))


def _nodes_on_line(tree: Optional[ast.AST], line: int) -> List[ast.AST]:
    if tree is None:
        return []
    return [node for node in ast.walk(tree) if int(getattr(node, "lineno", 0) or 0) == line]


def _tokens_by_line(code: str) -> Dict[int, List[tokenize.TokenInfo]]:
    result: Dict[int, List[tokenize.TokenInfo]] = {}
    try:
        stream: Iterable[tokenize.TokenInfo] = tokenize.generate_tokens(io.StringIO(code or "").readline)
        for item in stream:
            if item.type in {token.ENDMARKER, token.NEWLINE, tokenize.NL, token.INDENT, token.DEDENT}:
                continue
            result.setdefault(item.start[0], []).append(item)
    except (IndentationError, SyntaxError, tokenize.TokenError):
        # A partial token stream is still useful, and line meaning has a
        # conservative raw-source fallback below.
        pass
    return result


def _unique(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _beginner_meaning(raw: str, nodes: List[ast.AST], fallback: str) -> str:
    """Add concrete beginner wording for the two most common I/O lines."""
    assignment = next((node for node in nodes if isinstance(node, (ast.Assign, ast.AnnAssign))), None)
    calls = [node for node in nodes if isinstance(node, ast.Call)]
    for call in calls:
        if isinstance(call.func, ast.Name) and call.func.id == "input" and assignment is not None:
            target = "the variable"
            target_node = assignment.targets[0] if isinstance(assignment, ast.Assign) else assignment.target
            if isinstance(target_node, ast.Name):
                target = f"the variable {target_node.id}"
            return f"This asks the learner for input and stores the answer in {target}."
        if isinstance(call.func, ast.Name) and call.func.id == "print":
            return "This displays the value inside the parentheses in the Output area."
    return fallback


def _syntax_facts(raw: str, line_tokens: List[tokenize.TokenInfo], nodes: List[ast.AST]) -> List[str]:
    ops = [item.string for item in line_tokens if item.type == token.OP]
    facts: List[str] = []
    has_call = any(isinstance(node, ast.Call) for node in nodes)
    has_subscript = any(isinstance(node, ast.Subscript) for node in nodes)
    has_slice = any(isinstance(node, ast.Slice) for node in nodes)
    has_dict = any(isinstance(node, ast.Dict) for node in nodes)
    has_set = any(isinstance(node, ast.Set) for node in nodes)
    is_block_header = raw.strip().endswith(":") and any(
        isinstance(node, tuple(_BLOCK_LABELS)) for node in nodes
    )

    if "=" in ops:
        facts.append("The equals sign assigns a value to a name.")
    comparisons = {
        "==": "Equals equals compares two values for equality.",
        "!=": "Not equals checks that two values are different.",
        ">": "The greater-than sign compares two values.",
        "<": "The less-than sign compares two values.",
        ">=": "Greater-than-or-equal compares two values.",
        "<=": "Less-than-or-equal compares two values.",
    }
    facts.extend(comparisons[op] for op in ops if op in comparisons)
    if "(" in ops or ")" in ops:
        facts.append(
            "The parentheses contain the arguments passed to a function."
            if has_call else "The parentheses group this expression."
        )
    if "[" in ops or "]" in ops:
        facts.append(
            "The square brackets select an item or slice from a value."
            if has_subscript else "The square brackets create a list."
        )
    if "{" in ops or "}" in ops:
        if has_dict:
            facts.append("The curly braces create a dictionary of keys and values.")
        elif has_set:
            facts.append("The curly braces create a set of unique values.")
    if ":" in ops:
        if has_slice:
            facts.append("Inside the square brackets, the colon separates the start and end of a slice.")
        elif is_block_header:
            facts.append("The colon says that an indented block follows.")
        elif has_dict:
            facts.append("In the dictionary, the colon separates a key from its value.")
    if "," in ops:
        facts.append("The comma separates arguments or items.")
    if any(item.type == token.STRING for item in line_tokens):
        facts.append("The quote marks delimit text stored as a string.")
    if any(item.type == tokenize.COMMENT for item in line_tokens):
        facts.append("The hash sign starts a comment that Python does not run.")
    if "." in ops:
        facts.append("The dot accesses an attribute or method on a value.")
    operator_meanings = {
        "+": "The plus sign adds or combines values.",
        "-": "The minus sign subtracts or negates a value.",
        "*": "The star multiplies values.",
        "/": "The slash divides values.",
        "//": "Double slash performs floor division.",
        "%": "The percent sign gives the remainder after division.",
        "**": "Double star raises a value to a power.",
        "->": "The arrow introduces the function's return-type annotation.",
    }
    facts.extend(operator_meanings[op] for op in ops if op in operator_meanings)
    return _unique(facts)


def explain_code(code: str, *, deep: bool = False, start: int = 0) -> Dict[str, Any]:
    """Explain non-empty source lines, with bounded auditory disclosure."""
    raw_lines = (code or "").splitlines()
    non_empty = [(number, raw) for number, raw in enumerate(raw_lines, start=1) if raw.strip()]
    total = len(non_empty)
    if not total:
        return {"analysis": "There is no code to explain yet.", "total": 0, "start": 0, "next_start": None}

    try:
        requested_start = int(start or 0)
    except (TypeError, ValueError):
        requested_start = 0
    start = max(0, min(requested_start, total - 1))
    limit = total if total <= 15 else 10
    selected = non_empty[start:start + limit]
    tree = _parse(code)
    tokens = _tokens_by_line(code)
    parts: List[str] = []

    if total > 15:
        first = start + 1
        last = start + len(selected)
        parts.append(f"Your program has {total} non-empty lines. Explaining lines {first} through {last} of that list.")

    for number, raw in selected:
        nodes = _nodes_on_line(tree, number)
        meaning = _beginner_meaning(raw, nodes, structure_tools.explain_line(code, number))
        sentence = f"Line {number}: {raw.strip()}. {meaning}"
        indent_text = raw[: len(raw) - len(raw.lstrip(" \t"))]
        spaces = len(indent_text.expandtabs(4))
        container = _containing_block(tree, number)
        if spaces:
            sentence += f" It is indented {spaces} spaces"
            if container is not None:
                sentence += f", so it belongs to the {_block_label(container)} starting on line {getattr(container, 'lineno', '?')}"
            sentence += "."
        elif deep:
            sentence += " It is not indented, so it is at the top level."
        if deep:
            facts = _syntax_facts(raw, tokens.get(number, []), nodes)
            if facts:
                sentence += " " + " ".join(facts)
        parts.append(sentence)

    next_start = start + len(selected) if start + len(selected) < total else None
    if next_start is not None:
        parts.append("Say continue analysis for the next section.")
    return {
        "analysis": "\n\n".join(parts),
        "total": total,
        "start": start,
        "next_start": next_start,
        "deep": deep,
        "structural_source": "ast-tokenize",
    }
