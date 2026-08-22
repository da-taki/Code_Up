"""Guided projects with real, AST-based checkpoints.

Deliberately not LLM-judged: each checkpoint is a small deterministic check
over the learner's actual code, mirroring the pattern already used by
``codeup.learning.tutorial_engine``'s ``validate_*`` functions. A checkpoint
is "complete" the moment the code satisfies its check - nothing is inferred
or guessed.
"""

from __future__ import annotations

import ast
from typing import Callable, Dict, List, Optional

Check = Callable[[str], bool]


def _parse(code: str) -> Optional[ast.AST]:
    try:
        return ast.parse(code or "")
    except SyntaxError:
        return None


def _check_dictionary(code: str) -> bool:
    tree = _parse(code)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and node.keys:
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
            return True
    return False


def _check_total(code: str) -> bool:
    if not _check_dictionary(code):
        return False
    tree = _parse(code)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sum":
            return True
        if isinstance(node, ast.For):
            for sub in ast.walk(node):
                if isinstance(sub, ast.AugAssign) and isinstance(sub.op, ast.Add):
                    return True
    return False


def _check_average(code: str) -> bool:
    if not _check_total(code):
        return False
    tree = _parse(code)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return True
    return False


def _check_output(code: str) -> bool:
    if not _check_average(code):
        return False
    tree = _parse(code)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print" and node.args:
            return True
    return False


_STUDENT_MARKS_STARTER = '''# Student Marks Program
# Step 1: store each student's mark in a dictionary, for example:
# marks = {"Amir": 78, "Priya": 91}
marks = {}

# Step 2: add up all the marks (total = ...)

# Step 3: divide the total by how many students there are (average = ...)

# Step 4: print the result
'''

PROJECTS: Dict[str, Dict] = {
    "student_marks": {
        "id": "student_marks",
        "title": "Student Marks Program",
        "description": (
            "Build a small program that stores a class's marks in a dictionary, "
            "works out the total and the average, and prints the result."
        ),
        "starter_code": _STUDENT_MARKS_STARTER,
        "expected_concepts": ["dictionaries", "variables", "print output"],
        "checkpoints": [
            {"id": "dictionary", "label": "Create a dictionary of student marks", "check": _check_dictionary},
            {"id": "total", "label": "Compute the total of the marks", "check": _check_total},
            {"id": "average", "label": "Compute the average of the marks", "check": _check_average},
            {"id": "output", "label": "Print the result", "check": _check_output},
        ],
    },
}


def list_projects() -> List[Dict]:
    return [_public_project(p) for p in PROJECTS.values()]


def _public_project(project: Dict) -> Dict:
    return {
        "id": project["id"],
        "title": project["title"],
        "description": project["description"],
        "starter_code": project["starter_code"],
        "expected_concepts": project["expected_concepts"],
        "checkpoints": [{"id": c["id"], "label": c["label"]} for c in project["checkpoints"]],
    }


def get_project(project_id: str) -> Optional[Dict]:
    project = PROJECTS.get(project_id)
    return _public_project(project) if project else None


def evaluate(project_id: str, code: str) -> Dict[str, bool]:
    project = PROJECTS.get(project_id)
    if not project:
        return {}
    return {c["id"]: bool(c["check"](code)) for c in project["checkpoints"]}


def newly_completed(project_id: str, code: str, previously_completed: List[str]) -> List[str]:
    results = evaluate(project_id, code)
    previous = set(previously_completed or [])
    return [cid for cid, passed in results.items() if passed and cid not in previous]


def all_checkpoint_ids(project_id: str) -> List[str]:
    project = PROJECTS.get(project_id)
    if not project:
        return []
    return [c["id"] for c in project["checkpoints"]]


# ---- instructor-authored (custom) project checkpoints ---------------------------------
#
# A small, deliberately limited deterministic DSL so instructors can define
# checkpoints without writing Python: each checkpoint has a `check_type` and
# a `check_config` dict. This reuses the same AST-over-real-code philosophy
# as the built-in projects above and tutorial_engine's validators - never an
# LLM judging completion.

def _check_contains_print(code: str, config: Dict) -> bool:
    tree = _parse(code)
    if tree is None:
        return False
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print" and n.args
        for n in ast.walk(tree)
    )


def _check_contains_call(code: str, config: Dict) -> bool:
    tree = _parse(code)
    if tree is None:
        return False
    name = str(config.get("name") or "")
    if not name:
        return False
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
        for n in ast.walk(tree)
    )


def _check_contains_assignment_named(code: str, config: Dict) -> bool:
    tree = _parse(code)
    if tree is None:
        return False
    names = config.get("names")
    names = [names] if isinstance(names, str) else list(names or [])
    if not names:
        return True  # any assignment at all
    assigned = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            assigned |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            assigned.add(n.target.id)
    return all(name in assigned for name in names)


def _check_contains_keyword(code: str, config: Dict) -> bool:
    tree = _parse(code)
    if tree is None:
        return False
    keyword = str(config.get("keyword") or "").lower()
    node_types = {
        "for": ast.For, "while": ast.While, "if": ast.If, "def": ast.FunctionDef,
        "try": ast.Try, "class": ast.ClassDef,
    }
    node_type = node_types.get(keyword)
    if node_type is None:
        return False
    return any(isinstance(n, node_type) for n in ast.walk(tree))


def _check_contains_operator(code: str, config: Dict) -> bool:
    tree = _parse(code)
    if tree is None:
        return False
    op_map = {"+": ast.Add, "-": ast.Sub, "*": ast.Mult, "/": ast.Div, "//": ast.FloorDiv, "%": ast.Mod}
    op_type = op_map.get(str(config.get("op") or ""))
    if op_type is None:
        return False
    return any(isinstance(n, ast.BinOp) and isinstance(n.op, op_type) for n in ast.walk(tree))


_CUSTOM_CHECKS: Dict[str, Callable[[str, Dict], bool]] = {
    "contains_print": _check_contains_print,
    "contains_call": _check_contains_call,
    "contains_assignment_named": _check_contains_assignment_named,
    "contains_keyword": _check_contains_keyword,
    "contains_operator": _check_contains_operator,
}

CUSTOM_CHECK_TYPES = tuple(_CUSTOM_CHECKS.keys())


def evaluate_custom(checkpoints: List[Dict], code: str) -> Dict[int, bool]:
    """checkpoints: rows from db.get_custom_project(...)['checkpoints'], each
    with 'id', 'check_type', 'check_config'."""
    results = {}
    for cp in checkpoints or []:
        check = _CUSTOM_CHECKS.get(cp.get("check_type"))
        results[cp["id"]] = bool(check(code, cp.get("check_config") or {})) if check else False
    return results


def newly_completed_custom(checkpoints: List[Dict], code: str, previously_completed: List[int]) -> List[int]:
    results = evaluate_custom(checkpoints, code)
    previous = set(previously_completed or [])
    return [cid for cid, passed in results.items() if passed and cid not in previous]
