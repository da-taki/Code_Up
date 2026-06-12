"""Screen Reader Bridge Mode — explain how to take the current code into
standard tools (NVDA, JAWS, VS Code). It tells the learner what to listen for,
which lines matter, keyboard-friendly steps, and common confusion points.

Positioning-critical: CodeUp is a bridge, NOT a replacement for screen readers
or editors. The wording always says so. Pure, deterministic, Flask-free.
"""

import ast
from typing import Any, Dict, List, Optional

import structure_tools

_POSITIONING = ("CodeUp is a bridge, not a replacement for NVDA, JAWS, or VS Code — "
                "it helps you build understanding before you move into them.")


def _target_label(target: str) -> str:
    t = str(target or "").lower()
    if "nvda" in t:
        return "NVDA"
    if "jaws" in t:
        return "JAWS"
    if "vs code" in t or "vscode" in t or "visual studio code" in t or t.strip() == "code":
        return "VS Code"
    return "your screen reader"


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _key_lines(code: str) -> Dict[str, Optional[int]]:
    """First loop / first indented print under it / first function / first if."""
    info: Dict[str, Optional[int]] = {"loop": None, "print_after_loop": None,
                                       "function": None, "condition": None}
    try:
        tree = ast.parse(code)
    except Exception:
        return info
    prints: List[int] = []
    for node in ast.walk(tree):
        if info["loop"] is None and isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            info["loop"] = node.lineno
        elif info["function"] is None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info["function"] = node.lineno
        elif info["condition"] is None and isinstance(node, ast.If):
            info["condition"] = node.lineno
        elif isinstance(node, ast.Call) and _call_name(node) == "print":
            prints.append(node.lineno)
    if info["loop"] is not None:
        info["print_after_loop"] = next((p for p in sorted(prints) if p > info["loop"]), None)
    return info


def _project_files(project_state: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(project_state, dict):
        return []
    files = project_state.get("files")
    if isinstance(files, dict):
        return [str(k) for k in files.keys()]
    if isinstance(files, list):
        return [str(f) for f in files]
    return []


def _structure_sentence(code: str, keys: Dict[str, Optional[int]]) -> str:
    if keys["loop"] is not None and keys["print_after_loop"] is not None:
        return (f"This program has a loop on line {keys['loop']} and an indented print line on "
                f"line {keys['print_after_loop']}.")
    if keys["loop"] is not None:
        return f"This program has a loop on line {keys['loop']} with an indented body beneath it."
    if keys["function"] is not None:
        return f"This program defines a function on line {keys['function']} with an indented body."
    if keys["condition"] is not None:
        return f"This program has an if statement on line {keys['condition']} with an indented body."
    try:
        return structure_tools.build_structure_snapshot(code).get("summary", "").strip()
    except Exception:
        return "This program has a few top-level statements."


def _indentation_point(keys: Dict[str, Optional[int]]) -> str:
    if keys["loop"] is not None and keys["print_after_loop"] is not None:
        return (f"The key structure is that line {keys['print_after_loop']} belongs inside line "
                f"{keys['loop']}.")
    if keys["loop"] is not None:
        return f"The key structure is that the lines under line {keys['loop']} belong inside the loop."
    if keys["function"] is not None:
        return f"The key structure is that the lines under line {keys['function']} belong inside the function."
    return "The key structure to listen for is which lines are indented inside a block."


def build_screen_reader_bridge(code: str, project_state: Optional[Dict[str, Any]] = None,
                               target: str = "screen reader", verbosity: str = "normal") -> Dict[str, Any]:
    """Return {message, speech, target} bridging the current code to NVDA / JAWS / VS Code."""
    code = code or ""
    verbosity = (verbosity or "normal").strip().lower()
    label = _target_label(target)
    project_files = _project_files(project_state)

    if not code.strip() and len(project_files) <= 1:
        msg = "There is no code or project to prepare yet. Try creating or opening a program first."
        return {"message": msg, "speech": msg, "target": label}

    keys = _key_lines(code)
    structure = _structure_sentence(code, keys)
    indentation = _indentation_point(keys)

    listen = (f"In {label}, listen carefully for indentation, or use line-by-line reading so you "
              f"hear where each block begins and ends.")
    keyboard = ("Keyboard steps: move line by line with the arrow keys; after a line that ends in a "
                "colon, the next line should be indented.")
    confusion = ("Common confusion: if you hear an indentation error, go back to the line after the "
                 "colon and check that it is indented.")
    vscode = ""
    if label == "VS Code":
        vscode = ("Moving to VS Code: open the file, turn on NVDA or JAWS, and use line-by-line "
                  "reading; the structure you learned here maps directly onto what you will hear there.")

    parts: List[str] = [f"Screen reader bridge for {label}: {structure}", listen, indentation, keyboard]

    if len(project_files) > 1:
        entry = ""
        if isinstance(project_state, dict):
            entry = str(project_state.get("entry") or project_state.get("active_file") or project_files[0])
        helpers = ", ".join(f for f in project_files if f != entry) or "the other files"
        reqs = ""
        if isinstance(project_state, dict) and project_state.get("requirements"):
            reqs = " Requirements: " + ", ".join(str(r) for r in project_state["requirements"]) + "."
        parts.append(f"This is a multi-file project. Start from the entry point {entry}; the helper "
                     f"files are {helpers}.{reqs}")

    if verbosity == "concise":
        parts = [parts[0], listen, indentation]
        if vscode:
            parts.append(vscode)
    else:
        parts.append(confusion)
        if vscode:
            parts.append(vscode)

    parts.append(_POSITIONING)
    message = " ".join(p for p in parts if p)
    return {"message": message, "speech": message, "target": label}
