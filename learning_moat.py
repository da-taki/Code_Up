"""Deterministic learning bridge features for blind Python beginners.

Tutor Mode, Codex Handoff Pack, and Understanding Checks are intentionally
small, local, and rule-based. They reuse the existing cockpit facts instead of
turning CodeUp into a general coding agent.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, Optional

import audio_diff
import error_trace
import project_map
import report_support

__all__ = [
    "command_kind",
    "handle_tutor_command",
    "build_handoff_pack",
    "build_understanding_check",
    "redact",
]

_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential)\b\s*[:=]\s*['\"]?[^'\"\s]+"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[a-z0-9]{8,}|xox[baprs]-[a-z0-9-]+|akia[0-9a-z]{8,}|bearer\s+[a-z0-9._-]+|-----begin)"
)
_MAX_CODE_DUMP = 900


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def redact(text: str) -> str:
    """Hide obvious secret-looking values in copyable learner reports."""
    if not text:
        return ""
    redacted = []
    for line in str(text).splitlines():
        line = _SECRET_ASSIGN_RE.sub(r"\1 = [redacted]", line)
        line = _SECRET_VALUE_RE.sub("[redacted possible secret]", line)
        redacted.append(line)
    return "\n".join(redacted)


def _clip(text: str, limit: int = 260) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def command_kind(text: str) -> Optional[str]:
    t = _norm(text)
    if t in {"start tutor mode", "turn on tutor mode"}:
        return "tutor_on"
    if t in {"stop tutor mode", "turn off tutor mode"}:
        return "tutor_off"
    if t == "tutor mode status":
        return "tutor_status"
    if t in {"hint only", "give me a hint", "explain first"}:
        return "tutor_hint"
    if t == "let me try again":
        return "tutor_try_again"
    if t in {"show fix", "fix with teaching"}:
        return "tutor_show_fix"
    if t in {
        "make codex handoff",
        "create codex handoff",
        "prepare codex handoff",
        "make handoff pack",
        "copy handoff for codex",
    }:
        return "handoff"
    if t in {"check my understanding", "quiz me on this code", "ask me a question"}:
        return "understanding_question"
    if t == "what mistake did i make":
        return "understanding_mistake"
    if t in {"give me a similar exercise", "make practice question"}:
        return "understanding_practice"
    if t == "grade my attempt":
        return "understanding_grade"
    return None


def _project_state(mem: Dict[str, Any], project_state: Optional[Dict[str, Any]], code: str) -> Dict[str, Any]:
    if project_state:
        return project_state
    files = mem.get("project_files")
    if isinstance(files, dict) and files:
        return {"is_project": True, "files": files}
    return {"is_project": False, "code": code or ""}


def _error_analysis(mem: Dict[str, Any], code: str, error_text: str = "") -> Dict[str, Any]:
    return error_trace.analyze(
        error_text or str(mem.get("last_run_error") or ""),
        traceback_text=str(mem.get("last_run_traceback") or ""),
        code=code or "",
    )


def _indentation_hint(analysis: Dict[str, Any]) -> str:
    if analysis.get("exception_type") not in {"IndentationError", "TabError"}:
        return ""
    code_line = analysis.get("code_line") or "the line after the block header"
    return (
        "Hint: Python needs an indented block after a for loop, if statement, or function. "
        f"Try adding four spaces before {code_line}. "
        "Say \"show fix\" if you want CodeUp to propose the exact change."
    )


def _generic_hint(mem: Dict[str, Any], code: str, error_text: str = "") -> str:
    analysis = _error_analysis(mem, code, error_text)
    if analysis.get("has_error"):
        indent = _indentation_hint(analysis)
        if indent:
            return indent
        cause = analysis.get("likely_cause") or "Read the error type and the line it names."
        next_step = analysis.get("next_steps") or "Try one small change, then run again."
        return f"Hint: {cause} {next_step} Say \"show fix\" if you want a proposed change."
    if code.strip():
        concepts = report_support.detect_python_concepts(code)
        if "loops" in concepts:
            return "Hint: Trace the loop one round at a time. Ask what value changes each time, then run again."
        return "Hint: Read one line at a time and ask what value each name stores. Run the code when ready."
    return "Tutor Mode is ready. Write or paste a small Python program, then say run or explain error."


def handle_tutor_command(kind: str, mem: Dict[str, Any], code: str = "", error_text: str = "") -> Dict[str, str]:
    if kind == "tutor_on":
        mem["tutor_mode"] = True
        msg = "Tutor Mode on. I will give hints and explanations before fixes."
    elif kind == "tutor_off":
        mem["tutor_mode"] = False
        msg = "Tutor Mode off. I will still explain clearly when you ask."
    elif kind == "tutor_status":
        msg = "Tutor Mode is on." if mem.get("tutor_mode") else "Tutor Mode is off."
    elif kind == "tutor_try_again":
        mem["tutor_waiting_for_try"] = True
        msg = "Okay. Try editing the code yourself, then run it again. I will not force a fix."
    elif kind == "tutor_hint":
        mem["tutor_mode"] = True
        mem["tutor_hints_requested"] = int(mem.get("tutor_hints_requested") or 0) + 1
        msg = _generic_hint(mem, code, error_text)
    else:
        msg = "Tutor Mode is ready. Ask for a hint, show fix, or let me try again."
    return {"message": msg, "speech": msg}


def _code_summary(code: str) -> str:
    code = redact(code or "")
    if not code.strip():
        return "No current code was recorded yet."
    lines = code.splitlines()
    concepts = report_support.detect_python_concepts(code)
    concept_text = ", ".join(concepts[:5]) if concepts else "basic Python"
    if len(code) <= _MAX_CODE_DUMP and len(lines) <= 30:
        count = len(lines)
        return f"Small current code ({count} line{'s' if count != 1 else ''}, {concept_text}):\n\n```python\n{code}\n```"
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    return (
        f"Large code file recorded ({len(lines)} lines, {concept_text}). "
        f"It starts with: {first or 'blank line'}. Paste only the specific section you want help with."
    )


def _changes(mem: Dict[str, Any]) -> str:
    history = mem.get("change_history") if isinstance(mem.get("change_history"), list) else []
    if not history:
        return "No reviewed code changes are recorded yet."
    lines = []
    for record in history[-3:]:
        change = audio_diff.summarize_change(
            record.get("before", ""), record.get("after", ""), file_name=record.get("file", "")
        )
        summary = redact(change.get("summary") or "A code change was reviewed.")
        lines.append(f"- {summary} Risk: {change.get('risk', 'unknown')}.")
    return "\n".join(lines)


def _state(mem: Dict[str, Any]) -> str:
    bundle = mem.get("last_state_trace") if isinstance(mem.get("last_state_trace"), dict) else None
    watched = mem.get("watched_variables") if isinstance(mem.get("watched_variables"), list) else []
    parts = []
    if watched:
        parts.append("Watched variables: " + ", ".join(str(v) for v in watched[:8]) + ".")
    if bundle and not bundle.get("error"):
        vars_state = bundle.get("vars") if isinstance(bundle.get("vars"), dict) else {}
        shown = []
        for name, info in list(vars_state.items())[:6]:
            value = str((info or {}).get("value", ""))
            if _SECRET_ASSIGN_RE.search(name) or _SECRET_VALUE_RE.search(value):
                shown.append(f"{name} = [redacted]")
            else:
                shown.append(f"{name} = {_clip(value, 60)}")
        if shown:
            parts.append("Final traced values: " + ", ".join(shown) + ".")
        if bundle.get("loop"):
            parts.append(_clip(str(bundle.get("loop")), 180))
    return "\n".join(parts) if parts else "No program state trace is recorded yet."


def _tried(mem: Dict[str, Any]) -> str:
    bits = []
    total = int(mem.get("command_count") or 0)
    if total:
        bits.append(f"Commands used: {total}.")
    if mem.get("run_count"):
        bits.append(f"Runs: {int(mem.get('run_count') or 0)}.")
    if mem.get("last_action"):
        bits.append(f"Last action: {mem.get('last_action')}.")
    if mem.get("fixes_applied") or mem.get("fixes_rejected"):
        bits.append(
            f"Fixes applied: {int(mem.get('fixes_applied') or 0)}; rejected: {int(mem.get('fixes_rejected') or 0)}."
        )
    return " ".join(bits) if bits else "No attempts are recorded yet."


def build_handoff_pack(
    mem: Dict[str, Any],
    code: str = "",
    project_state: Optional[Dict[str, Any]] = None,
    error_text: str = "",
) -> Dict[str, str]:
    mem = mem or {}
    state = _project_state(mem, project_state, code)
    analysis = _error_analysis(mem, code, error_text)
    current_error = error_trace.narrate(analysis) if analysis.get("has_error") else "No recent Python error is recorded."
    goal = mem.get("latest_user_request") or mem.get("last_gen_prompt") or mem.get("last_edit_request")
    goal = _clip(redact(goal), 240) if goal else "not recorded yet."
    structure = _clip(redact(project_map.narrate(state)), 900) or "Project structure is not recorded yet."
    questions = [
        "Can you explain this error without rewriting the whole program?",
        "Can you suggest the smallest fix?",
        "Can you explain the main loop or condition in beginner words?",
        "Can you help me test whether my solution still teaches the concept?",
    ]
    lines = [
        "# CodeUp Handoff Pack",
        "",
        "## What I am trying to do",
        goal,
        "",
        "## Current code / project",
        _code_summary(code),
        "",
        "## Project structure",
        structure,
        "",
        "## Current error",
        redact(current_error),
        "",
        "## What changed recently",
        _changes(mem),
        "",
        "## Current program state",
        _state(mem),
        "",
        "## What I already tried",
        redact(_tried(mem)),
        "",
        "## Questions to ask Codex",
    ]
    lines.extend(f"- {q}" for q in questions)
    speech = (
        "I made a Codex handoff pack. It summarizes your goal, code, error, "
        "recent changes, program state, and questions to ask next."
    )
    return {"message": "\n".join(lines).strip(), "speech": speech}


def _has_loop(code: str) -> bool:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return bool(re.search(r"\bfor\s+\w+\s+in\s+range\s*\(", code or ""))
    return any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(tree))


def _loop_question(code: str) -> str:
    m = re.search(r"\bfor\s+([A-Za-z_]\w*)\s+in\s+range\s*\(\s*(\d+)\s*\)", code or "")
    if m:
        name = m.group(1)
        stop = int(m.group(2))
        if stop > 0:
            return f"Question: What value does {name} have on the last loop run?"
    return "Question: What changes each time this loop runs?"


def build_understanding_check(kind: str, mem: Dict[str, Any], code: str = "", error_text: str = "") -> Dict[str, str]:
    mem = mem or {}
    analysis = _error_analysis(mem, code, error_text)
    if kind == "understanding_grade":
        msg = "I do not have an answer to grade yet. Say your answer first, then ask me to grade it."
        return {"message": msg, "speech": msg}
    if kind == "understanding_practice":
        if _has_loop(code):
            msg = "Practice: Write a loop that prints the numbers 1, 2, and 3. Then run it and ask CodeUp to show program state."
        else:
            msg = "Practice: Write two lines: store your name in a variable, then print it. Run it when ready."
        return {"message": msg, "speech": msg}
    if kind == "understanding_mistake":
        if analysis.get("has_error"):
            msg = f"Mistake check: {analysis.get('likely_cause') or 'The program has an error.'}"
        else:
            msg = "I do not see a recent error. Run your code first if you want me to identify a mistake."
        return {"message": msg, "speech": msg}
    if analysis.get("exception_type") in {"IndentationError", "TabError"}:
        msg = "Question: Why does Python need spaces before print(i) in this program?"
    elif _has_loop(code):
        msg = _loop_question(code)
    elif code.strip():
        msg = "Question: What is the first value this program stores or prints?"
    else:
        msg = "Question: What small Python idea do you want to practice next: print, variables, or loops?"
    msg += " Answer when ready, or say \"give me a hint.\""
    return {"message": msg, "speech": msg}
