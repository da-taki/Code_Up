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
            target = node.target.id if isinstance(node.target, ast.Name) else "the loop variable"
            range_stop = None
            if (isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name)
                    and node.iter.func.id == "range" and node.iter.args
                    and isinstance(node.iter.args[0], ast.Constant)
                    and isinstance(node.iter.args[0].value, int)):
                range_stop = node.iter.args[0].value
            facts["loops"].append({"kind": "for", "line": node.lineno,
                                   "target": target, "range_stop": range_stop})
        elif isinstance(node, ast.While):
            facts["loops"].append({"kind": "while", "line": node.lineno,
                                   "target": "", "range_stop": None})
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
    facts["loops"].sort(key=lambda x: x["line"])
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
        loop = facts["loops"][0]
        kind, line = loop["kind"], loop["line"]
        out.append(f"It uses a {kind} loop on line {line} to repeat an action.")
        later_print = next((p for p in facts["prints"] if p > line), None)
        if later_print:
            out.append(f"The indented print statement on line {later_print} is the action that repeats.")
        target = loop.get("target") or "the loop variable"
        stop = loop.get("range_stop")
        if kind == "for" and stop is not None:
            values = list(range(stop))
            values_phrase = ", ".join(str(v) for v in values)
            out.append(f"range({stop}) gives {values_phrase}.")
            out.append(f"{target} changes each time through the loop.")
            if later_print:
                out.append(f"print({target}) displays the current value.")
                out.append(f"Expected output is {values_phrase} on separate lines.")
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
        loop = facts["loops"][0]
        if loop.get("range_stop") is not None:
            values = ", ".join(str(v) for v in range(loop["range_stop"]))
            return f"Prediction question: before you run it, can you say why the output will be {values}?"
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


def _flatten_output(out: str, limit: int = 160) -> str:
    lines = [ln.strip() for ln in str(out or "").splitlines() if ln.strip()]
    joined = ", ".join(lines)
    return (joined[:limit] + "...") if len(joined) > limit else joined


def _last_output_sentence(mem: Optional[Dict[str, Any]]) -> str:
    """Make the program's most recent result accessible before any practice idea."""
    mem = mem or {}
    ok = mem.get("last_run_ok")
    out = (mem.get("last_run_output") or "").strip()
    err = (mem.get("last_run_error") or "").strip()
    if out and ok is not False:
        flat = _flatten_output(out)
        end = "" if flat[-1:] in ".!?:" else "."
        return f"The last output was {flat}{end}"
    if err and ok is False:
        first = err.splitlines()[0][:100]
        end = "" if first[-1:] in ".!?:" else "."
        return f"Last time it ran it hit an error: {first}{end}"
    return ""


def _readback_suggestion(code: str) -> str:
    """Suggest line-by-line readback when there is more than one line to hear."""
    if len([ln for ln in (code or "").splitlines() if ln.strip()]) > 1:
        return 'You can say "read my code" to hear it line by line.'
    return ""


def _primary_line(facts: Dict[str, Any]) -> Optional[int]:
    if facts["loops"]:
        return facts["loops"][0]["line"]
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
    last_output = _last_output_sentence(session_memory)
    readback = _readback_suggestion(code)
    prediction = _prediction(facts)
    practice = _practice(facts)

    # Always teach the code first (what it is -> important lines -> last output),
    # then offer a way to explore it, and only then a prediction + practice idea.
    if verbosity == "concise":
        parts = [intro] + where[:1] + [last_output, prediction]
    elif verbosity in ("beginner", "detailed"):
        parts = [intro] + where + [last_output, readback, prediction, practice]
    elif verbosity == "expert":
        parts = [intro] + where[:1] + [prediction]
    else:  # normal
        parts = [intro] + where[:6] + [last_output, readback, prediction, practice]

    msg = " ".join(p for p in parts if p)
    return {"action": "deterministic_message", "message": msg, "speech": msg,
            "concepts": concepts, "line": _primary_line(facts)}
