"""Concept-Aware Code Tutor — turn the current program into a short, spoken
lesson built from deterministic AST facts (not a long lecture).

A lesson covers: what the program does, the concepts it uses and where each
appears, one prediction question, and one practice suggestion. Length respects
the verbosity preference. Pure and Flask-free; never edits or runs code.
"""

import ast
from typing import Any, Dict, List, Optional

import structure_tools

# Map structure concept tokens to lesson-friendly words.
_CONCEPT_WORD = {
    "imports": "imports", "variables": "variables", "loops": "loops",
    "conditions": "if statements", "functions": "functions", "classes": "classes",
    "print output": "output", "input": "input", "error handling": "error handling",
}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _assign_name(node: ast.AST) -> str:
    target = None
    if isinstance(node, ast.Assign) and node.targets:
        target = node.targets[0]
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        target = node.target
    return target.id if isinstance(target, ast.Name) else ""


def _facts(code: str) -> Dict[str, Any]:
    """Deterministic facts used to build the lesson."""
    facts: Dict[str, Any] = {"loops": [], "prints": [], "assigns": [],
                             "functions": [], "conditions": [], "ranges": []}
    try:
        tree = ast.parse(code)
    except Exception:
        return facts
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            facts["loops"].append(("for", node.lineno))
        elif isinstance(node, ast.While):
            facts["loops"].append(("while", node.lineno))
        elif isinstance(node, ast.If):
            facts["conditions"].append(node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts["functions"].append((node.name, node.lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            name = _assign_name(node)
            if name:
                facts["assigns"].append((name, node.lineno))
        elif isinstance(node, ast.Call):
            fn = _call_name(node)
            if fn == "print":
                facts["prints"].append(node.lineno)
            elif fn == "range" and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, int):
                facts["ranges"].append(node.args[0].value)
    facts["loops"].sort(key=lambda x: x[1])
    facts["prints"].sort()
    facts["conditions"].sort()
    return facts


def _concepts_phrase(concepts: List[str]) -> str:
    words = [_CONCEPT_WORD.get(c, c) for c in concepts]
    # de-dupe preserving order
    seen, ordered = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            ordered.append(w)
    if not ordered:
        return "simple statements"
    if len(ordered) == 1:
        return ordered[0]
    if len(ordered) == 2:
        return f"{ordered[0]} and {ordered[1]}"
    return ", ".join(ordered[:-1]) + ", and " + ordered[-1]


def _intro(concepts: List[str]) -> str:
    if not concepts:
        return "This program runs a few simple statements."
    return f"This program is a lesson about {_concepts_phrase(concepts)}."


def _where_sentences(facts: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if facts["loops"]:
        kind, line = facts["loops"][0]
        out.append(f"It uses a {kind} loop on line {line} to repeat an action.")
        later_print = next((p for p in facts["prints"] if p > line), None)
        if later_print:
            out.append(f"The indented print statement on line {later_print} is the action that repeats.")
    if facts["functions"]:
        name, line = facts["functions"][0]
        out.append(f"The function {name} on line {line} groups steps you can reuse.")
    if facts["assigns"]:
        name, line = facts["assigns"][0]
        out.append(f"The variable {name} is given a value on line {line}, then used later in the program.")
    if facts["conditions"] and not facts["loops"]:
        out.append(f"The if statement on line {facts['conditions'][0]} chooses what runs.")
    return out


def _prediction(facts: Dict[str, Any]) -> str:
    if facts["loops"] and facts["prints"]:
        return "Prediction question: what numbers will print before you run it?"
    if facts["prints"]:
        return "Prediction question: what will this program print before you run it?"
    if facts["functions"]:
        name = facts["functions"][0][0]
        return f"Prediction question: what would {name} return for a simple input?"
    return "Prediction question: what will this program output before you run it?"


def _practice(facts: Dict[str, Any]) -> str:
    if facts["ranges"]:
        r = facts["ranges"][0]
        return f"Practice idea: change range {r} to range {r + 2} and predict the new output."
    if facts["assigns"]:
        name = facts["assigns"][0][0]
        return f"Practice idea: change the value of {name} and predict what prints."
    return "Practice idea: change one value and predict the new output before running."


def _primary_line(facts: Dict[str, Any]) -> Optional[int]:
    if facts["loops"]:
        return facts["loops"][0][1]
    if facts["functions"]:
        return facts["functions"][0][1]
    if facts["assigns"]:
        return facts["assigns"][0][1]
    if facts["conditions"]:
        return facts["conditions"][0]
    if facts["prints"]:
        return facts["prints"][0]
    return None


def build_concept_lesson(code: str, session_memory: Optional[Dict[str, Any]] = None,
                         verbosity: str = "normal") -> Dict[str, Any]:
    """Turn ``code`` into a short lesson. Returns a deterministic_message action."""
    code = code or ""
    verbosity = (verbosity or "normal").strip().lower()

    if not code.strip():
        msg = "There is no code to turn into a lesson yet. Try creating or opening a program first."
        return {"action": "deterministic_message", "message": msg, "speech": msg,
                "concepts": [], "line": None}

    snap = structure_tools.build_structure_snapshot(code)
    if snap.get("has_error"):
        msg = ("This code has a syntax error, so I cannot turn it into a lesson yet. "
               "Try fixing the error, then ask again.")
        return {"action": "deterministic_message", "message": msg, "speech": msg,
                "concepts": list(snap.get("concepts") or []), "line": None}

    facts = _facts(code)
    concepts = list(snap.get("concepts") or [])
    intro = _intro(concepts)
    where = _where_sentences(facts)
    prediction = _prediction(facts)
    practice = _practice(facts)

    if verbosity == "concise":
        parts = [intro] + where[:1] + [prediction]
    elif verbosity in ("beginner", "detailed"):
        parts = [intro] + where + [prediction, practice]
    elif verbosity == "expert":
        parts = [intro] + where[:1] + [prediction]
    else:  # normal
        parts = [intro] + where[:2] + [prediction, practice]

    msg = " ".join(p for p in parts if p)
    return {"action": "deterministic_message", "message": msg, "speech": msg,
            "concepts": concepts, "line": _primary_line(facts)}
