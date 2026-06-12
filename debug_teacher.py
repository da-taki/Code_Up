"""Blind Debugger Mode — a guided, teacher-style debugging response.

Built entirely from deterministic facts (current code, last run result, last
error, detected problem, structure snapshot) plus the existing staged hint
engine. Pure and Flask-free. It NEVER edits or runs code — it only explains.

This module orchestrates existing Sprint-2 systems (hint_engine,
structure_tools) rather than re-implementing them, per the sprint brief.
"""

import ast
import re
from typing import Any, Dict, List, Optional

import hint_engine
import structure_tools

# Offered as spoken follow-ups whenever there is a problem to work on.
NEXT_COMMANDS = ["give me a bigger hint", "show me the answer", "replay the mistake"]

# Map hint_engine's problem types to (spec problem_type, beginner concept word).
_PROBLEM_META = {
    "indentation": ("indentation", "indentation"),
    "missing_colon": ("syntax", "syntax"),
    "syntaxerror": ("syntax", "syntax"),
    "nameerror": ("name_error", "variable"),
    "typeerror": ("logic", "types"),
    "zerodivision": ("logic", "arithmetic"),
}

_STRUCT_TO_WORD = {"loops": "loop", "conditions": "if statement",
                   "functions": "function", "variables": "variable"}


def _problem_line(code: str, error: str) -> Optional[int]:
    """The most relevant 1-based line: from the error text, else from parsing."""
    matches = re.findall(r"line (\d+)", str(error or ""))
    if matches:
        try:
            return int(matches[-1])
        except ValueError:
            pass
    try:
        ast.parse(code or "")
    except SyntaxError as exc:
        return exc.lineno
    except Exception:
        return None
    return None


def _structure_concepts(code: str) -> List[str]:
    try:
        return list(structure_tools.build_structure_snapshot(code).get("concepts") or [])
    except Exception:
        return []


def _constructs(code: str) -> List[str]:
    """Constructs detected by regex — works even when the code does not parse
    (e.g. an IndentationError), where the AST-based snapshot cannot help."""
    found = []
    if re.search(r"(?m)^\s*(?:for|while)\b", code or ""):
        found.append("loop")
    if re.search(r"(?m)^\s*(?:if|elif)\b", code or ""):
        found.append("if statement")
    if re.search(r"(?m)^\s*def\b", code or ""):
        found.append("function")
    return found


def _block_word(code: str, structure_concepts: List[str]) -> str:
    """A beginner word for the construct a misplaced line most likely belongs to."""
    for key in ("loops", "conditions", "functions"):
        if key in structure_concepts:
            return _STRUCT_TO_WORD[key]
    constructs = _constructs(code)
    return constructs[0] if constructs else "block"


def _on_line(line: Optional[int]) -> str:
    return f" on line {line}" if line else ""


def _concepts_for(concept: str, code: str, structure_concepts: List[str]) -> List[str]:
    out = [concept]
    for sc in structure_concepts:
        word = _STRUCT_TO_WORD.get(sc)
        if word and word not in out:
            out.append(word)
            return out
    # Fall back to regex constructs (so an unparseable indentation error still
    # names the loop / if / function the line belongs to).
    for word in _constructs(code):
        norm = "loop" if word == "loop" else word
        if norm not in out:
            out.append(norm)
            return out
    return out


def _summarize_does(snap: Dict[str, Any]) -> str:
    counts = snap.get("counts", {})
    loops, prints = counts.get("loops", 0), counts.get("prints", 0)
    funcs, conds = counts.get("functions", 0), counts.get("conditions", 0)
    if loops and prints:
        return f"It uses a loop to print {'a value' if prints == 1 else 'values'}."
    if loops:
        return "It uses a loop to repeat an action."
    if funcs:
        return "It defines and uses a function."
    if conds:
        return "It makes a decision with an if statement."
    if prints:
        return "It prints output to the screen."
    return (snap.get("summary") or "It runs a few simple statements.").strip()


def _debugging_habit(snap: Dict[str, Any]) -> str:
    counts = snap.get("counts", {})
    if counts.get("functions"):
        return "A good next debugging habit is to test the function with one small example input."
    return "A good next debugging habit is to predict the output before running it."


def _explain(spec_type: str, problem: Dict[str, Any], line: Optional[int], block: str) -> str:
    if spec_type == "indentation":
        return (f"Your program has an indentation error{_on_line(line)}. A line that should be "
                f"inside the {block} above it is not indented, so Python does not know it belongs there.")
    if spec_type == "name_error":
        name = problem.get("name") or "a value"
        return (f"Your program has a name error{_on_line(line)}. The name {name} is used "
                f"before it is given a value.")
    if spec_type == "syntax":
        if problem.get("type") == "missing_colon":
            return (f"Your program has a syntax error{_on_line(line)}. A line that starts a block "
                    f"is missing its colon at the end.")
        return f"Your program has a syntax error{_on_line(line)}. Python could not read that line."
    # logic / runtime
    if problem.get("type") == "zerodivision":
        return (f"Your program ran but hit a division-by-zero error{_on_line(line)}. "
                f"Something is being divided by zero.")
    if problem.get("type") == "typeerror":
        return (f"Your program ran but hit a type error{_on_line(line)}. "
                f"Two values of different kinds are being combined.")
    return (f"Your program ran but hit an error{_on_line(line)}. "
            f"Read the error message to see what it expected.")


def _classify_sentence(spec_type: str) -> str:
    return {
        "indentation": "This is an indentation problem, not a logic problem.",
        "syntax": "This is a syntax problem, not a logic problem.",
        "name_error": "This is a variable problem — a name is missing its value.",
        "logic": "This is a runtime problem that happens while the code is running.",
    }.get(spec_type, "")


def _success_response(code: str, verbosity: str) -> Dict[str, Any]:
    snap = structure_tools.build_structure_snapshot(code)
    does = _summarize_does(snap)
    habit = _debugging_habit(snap)
    if verbosity == "concise":
        msg = f"Your code runs successfully. {does}"
    elif verbosity == "expert":
        msg = f"Code runs. {does} {habit}"
    else:
        msg = f"Your code runs successfully. {does} {habit}"
    return {"message": msg, "speech": msg, "problem_type": "none", "line": None,
            "concepts": list(snap.get("concepts") or []),
            "next_commands": ["teach me this code", "ask my code"]}


def _problem_response(code: str, error: str, problem: Dict[str, Any],
                      mem: Dict[str, Any], verbosity: str) -> Dict[str, Any]:
    ptype = problem.get("type", "")
    spec_type, concept = _PROBLEM_META.get(ptype, ("logic", "logic"))
    structure_concepts = _structure_concepts(code)
    line = _problem_line(code, error)
    block = _block_word(code, structure_concepts)

    hint = hint_engine.build_hint(
        {"code": code, "error": error, "tutorial_module": mem.get("tutorial_module", "")},
        "small")["hint"]

    explain = _explain(spec_type, problem, line, block)
    classify = _classify_sentence(spec_type)
    small = f"Small hint: {hint}"
    offer = 'You can say "give me a bigger hint" if you want more help.'

    if verbosity == "concise":
        sentences = [explain, small]
    elif verbosity == "expert":
        sentences = [explain, small]
    else:  # normal / beginner / detailed
        sentences = [explain, classify, small, offer]

    msg = " ".join(s for s in sentences if s)
    return {"message": msg, "speech": msg, "problem_type": spec_type, "line": line,
            "concepts": _concepts_for(concept, code, structure_concepts),
            "next_commands": list(NEXT_COMMANDS)}


def build_blind_debugger_response(code: str, session_memory: Optional[Dict[str, Any]] = None,
                                  last_run: Optional[Dict[str, Any]] = None,
                                  verbosity: str = "normal") -> Dict[str, Any]:
    """Return a teacher-style debugging response. Never edits or runs code.

    Returns: {message, speech, problem_type, line, concepts, next_commands}.
    problem_type is one of: indentation | name_error | syntax | logic | none | empty.
    """
    code = code or ""
    mem = session_memory or {}
    last_run = last_run or {}
    verbosity = (verbosity or "normal").strip().lower()

    if not code.strip():
        msg = "There is no code to debug yet. Try creating or opening a program first."
        return {"message": msg, "speech": msg, "problem_type": "empty", "line": None,
                "concepts": [], "next_commands": []}

    error = str(last_run.get("error") or mem.get("last_run_error") or "")
    problem = hint_engine._detect_problem(code, error)
    ptype = problem.get("type", "")

    if not ptype:
        return _success_response(code, verbosity)
    return _problem_response(code, error, problem, mem, verbosity)
