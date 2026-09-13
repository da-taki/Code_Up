"""Deterministic learning bridge features for blind Python beginners.

Tutor Mode, Codex Handoff Pack, and Understanding Checks are intentionally
small, local, and rule-based. They reuse the existing cockpit facts instead of
turning CodeUp into a general coding agent.
"""

from __future__ import annotations

import ast
import hashlib
import re
import time
from typing import Any, Dict, Optional

from codeup.accessibility import audio_diff
from codeup.learning import hint_engine
from codeup.runtime import error_trace
from codeup.projects import project_map
from codeup.reports import report_support

__all__ = [
    "command_kind",
    "handle_tutor_command",
    "build_handoff_pack",
    "build_help_request_pack",
    "build_understanding_check",
    "grade_understanding_answer",
    "lesson_question_spec",
    "progressive_hint",
    "redact",
    "route_pending_understanding",
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
    if t in {"give me another hint", "another hint", "one more hint", "give me one more hint",
             "give me the next hint", "next hint", "give me a bigger hint", "a bigger hint", "bigger hint",
             "give me a more specific hint"}:
        return "tutor_hint_more"
    if t in {"give me a small hint", "a small hint", "small hint"}:
        return "tutor_hint_small"
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
    if t in {
        "make help request",
        "prepare help request",
        "prepare a help request",
        "prepare a help request for my teacher",
    }:
        # NOTE: "I need help from my teacher" is deliberately NOT claimed here --
        # ide_commands.py's classroom "ask my teacher for help" flow already
        # substring-matches "i need help" for live/async teacher notification
        # inside a classroom, a different concept from this static copyable
        # pack. Adding a second, conflicting owner for overlapping phrasing
        # would violate "do not solve an existing problem twice."
        return "help_request"
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
    elif kind in {"tutor_hint", "tutor_hint_more", "tutor_hint_small"}:
        if kind != "tutor_hint_small" and _fresh_pending(mem, code, require_same_code=True) is not None:
            return understanding_hint(mem)
        request = {"tutor_hint": "hint", "tutor_hint_more": "more", "tutor_hint_small": "small"}[kind]
        return progressive_hint(mem, code, error_text, request)
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


def build_help_request_pack(
    mem: Dict[str, Any],
    code: str = "",
    project_state: Optional[Dict[str, Any]] = None,
    error_text: str = "",
    question: str = "",
) -> Dict[str, str]:
    """'make help request' / 'prepare help request' (see command_kind() for the
    exact trigger set -- "I need help from my teacher" is deliberately NOT one,
    since ide_commands.py's classroom flow already owns that phrasing): a
    concise, copyable package addressed to a HUMAN teacher. Reuses the exact
    same data-gathering helpers build_handoff_pack already
    uses (goal / code summary / project structure / error / recent changes /
    program state / what was tried, all through report_support, error_trace,
    project_map, and audio_diff -- no new report engine), swapping only the
    framing text. Unlike build_handoff_pack, empty sections are OMITTED
    entirely rather than padded with "not recorded yet" filler, since a
    teacher-facing note should be short.
    """
    mem = mem or {}
    state = _project_state(mem, project_state, code)
    analysis = _error_analysis(mem, code, error_text)
    goal = mem.get("latest_user_request") or mem.get("last_gen_prompt") or mem.get("last_edit_request")
    goal = _clip(redact(goal), 240) if goal else ""
    code_summary = _code_summary(code)
    if code_summary.startswith("No current code"):
        code_summary = ""
    structure = _clip(redact(project_map.narrate(state)), 900)
    if "not recorded" in structure.lower() or "no code" in structure.lower():
        structure = ""
    current_error = redact(error_trace.narrate(analysis)) if analysis.get("has_error") else ""
    changes = _changes(mem)
    if changes.startswith("No reviewed"):
        changes = ""
    state_text = _state(mem)
    if state_text.startswith("No program state"):
        state_text = ""
    tried = redact(_tried(mem))
    if tried.startswith("No attempts"):
        tried = ""

    sections = [
        ("What I am trying to do", goal),
        ("My code", code_summary),
        ("Project structure", structure),
        ("My error", current_error),
        ("What changed recently", changes),
        ("Program state", state_text),
        ("What I already tried", tried),
    ]
    lines = ["# Help Request for My Teacher", ""]
    for title, body in sections:
        if body:
            lines.extend([f"## {title}", body, ""])
    lines.append("## My question")
    lines.append(_clip(redact(question), 300) if str(question or "").strip()
                 else "I am not sure exactly what to ask -- please look at what I have so far.")
    speech = ("I made a help request for your teacher. It includes what you were doing, "
             "your code, any error, and what you already tried.")
    return {"message": "\n".join(lines).strip(), "speech": speech}


def _has_loop(code: str) -> bool:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return bool(re.search(r"\bfor\s+\w+\s+in\s+range\s*\(", code or ""))
    return any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(tree))


def build_understanding_check(kind: str, mem: Dict[str, Any], code: str = "", error_text: str = "") -> Dict[str, str]:
    mem = mem or {}
    analysis = _error_analysis(mem, code, error_text)
    if kind == "understanding_grade":
        last = mem.get("last_understanding_answer")
        if isinstance(last, dict) and last.get("message"):
            answer = f"Your answer was: {last['answer']}. " if last.get("answer") else ""
            msg = answer + str(last["message"])
            return {"message": msg, "speech": msg}
        if _fresh_pending(mem, code, require_same_code=False) is not None:
            question = mem["pending_understanding"]["spec"].get("question", "")
            msg = f"I do not have an answer to grade yet. {question} Say your answer, for example \"my answer is ...\"."
            return {"message": msg, "speech": msg}
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
    spec = _question_spec(code, analysis)
    ask_understanding_question(mem, spec, code)
    msg = f"Question: {spec['question']} Answer when ready, or say \"give me a hint.\""
    return {"message": msg, "speech": msg}


# ---------------------------------------------------------------------------
# Progressive tutor hints
# ---------------------------------------------------------------------------
# "give me a hint" starts at the smallest hint for a problem and each further
# request ("give me a hint" again, "give me another hint", "give me a bigger
# hint") reveals a little more, ending at a concrete answer-level hint. Hints
# never change code; "show fix" proposes a change that still needs "apply".

_HINT_LEVELS = ("small", "bigger", "answer")


def _hint_signature(analysis: Dict[str, Any], code: str) -> str:
    if analysis.get("has_error"):
        return "error|{}|{}|{}".format(
            analysis.get("exception_type") or "", analysis.get("line_number") or analysis.get("line") or "",
            analysis.get("code_line") or "",
        )
    return "code|" + _code_sig(code)


def _error_hint_text(level: str, analysis: Dict[str, Any], code: str, error_text: str) -> str:
    if analysis.get("exception_type") in {"IndentationError", "TabError"}:
        code_line = analysis.get("code_line") or "the line after the block header"
        return {
            "small": "Hint: Python expects an indented block after a line that ends with a colon, such as a for "
                     "loop, if statement, or function. Check which line should belong inside that block.",
            "bigger": f"Hint: {code_line} should be inside the block above it, so Python needs it indented.",
            "answer": f"Hint: Add four spaces before {code_line} so it sits inside the block above.",
        }[level]
    engine = hint_engine.build_hint({"code": code, "error": error_text or str(analysis.get("raw") or "")}, level)
    text = "Hint: " + engine["hint"]
    if level == "bigger" and analysis.get("likely_cause"):
        text += " " + str(analysis["likely_cause"])
    elif level == "answer" and analysis.get("next_steps"):
        text += " " + str(analysis["next_steps"])
    return text


def _code_hint_text(level: str, code: str) -> str:
    concepts = report_support.detect_python_concepts(code)
    if "loops" in concepts:
        return {
            "small": "Hint: Trace the loop one round at a time. Ask what value changes each time, then run again.",
            "bigger": "Hint: Say \"show program state\" or \"step through this\" to hear the loop variable on each round.",
            "answer": "Hint: Compare each round's value with what you expected; the first round where they differ "
                      "is the line to look at.",
        }[level]
    return {
        "small": "Hint: Read one line at a time and ask what value each name stores. Run the code when ready.",
        "bigger": "Hint: Say \"what variables exist\" or \"show program state\" to hear each value after it runs.",
        "answer": "Hint: Run the code and compare the output with what you expected; the first difference points "
                  "to the line to change.",
    }[level]


def progressive_hint(mem: Dict[str, Any], code: str = "", error_text: str = "", request: str = "hint") -> Dict[str, str]:
    """request: 'hint' (same problem escalates), 'more' (escalate), 'small' (restart)."""
    mem["tutor_mode"] = True
    mem["tutor_hints_requested"] = int(mem.get("tutor_hints_requested") or 0) + 1
    analysis = _error_analysis(mem, code, error_text)
    if not analysis.get("has_error") and not (code or "").strip():
        msg = "Tutor Mode is ready. Write or paste a small Python program, then say run or explain error."
        return {"message": msg, "speech": msg, "hint_level": "small"}
    sig = _hint_signature(analysis, code)
    previous = mem.get("tutor_hint_level") if mem.get("tutor_hint_sig") == sig else None
    if request == "small" or previous not in _HINT_LEVELS:
        level = "small"
    else:
        level = _HINT_LEVELS[min(_HINT_LEVELS.index(previous) + 1, len(_HINT_LEVELS) - 1)]
    mem["tutor_hint_sig"] = sig
    mem["tutor_hint_level"] = level
    mem["hint_level"] = level  # keeps session_memory's hint ladder in step
    text = _error_hint_text(level, analysis, code, error_text) if analysis.get("has_error") else _code_hint_text(level, code)
    step = _HINT_LEVELS.index(level) + 1
    if level != "answer":
        coda = (" Say \"give me another hint\" for a bigger hint, or \"show fix\" if you want a proposed change."
                if analysis.get("has_error") else " Say \"give me another hint\" for a bigger hint.")
    else:
        coda = (" That was the last hint. Say \"show fix\" to hear the exact proposed change; nothing changes unless "
                "you apply it." if analysis.get("has_error") else " That was the last hint for this code.")
    msg = f"Hint {step} of 3. {text[len('Hint: '):] if text.startswith('Hint: ') else text}{coda}"
    return {"message": msg, "speech": msg, "hint_level": level}


# ---------------------------------------------------------------------------
# Understanding checks: ask -> answer -> grade
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
_PENDING_MAX_AGE_SECONDS = 15 * 60
_MAX_WRONG_ATTEMPTS = 3

_ANSWER_PREFIX_RE = re.compile(
    r"^(?:my answer is|the answer is|answer is|answer|i think (?:it is|it's|that)|i think|i believe|i guess|"
    r"it is|it's|its)\b\s*[:,-]?\s*(.+)$"
)
_REVEAL_PHRASES = {
    "i don't know", "i dont know", "i do not know", "show me the answer", "tell me the answer",
    "give me the answer", "just give me the answer", "what's the answer", "whats the answer",
    "what is the answer", "reveal the answer", "skip this question", "skip question",
}
_QUESTION_HINT_PHRASES = {
    "give me a hint", "hint", "i need a hint", "hint only", "give me another hint", "another hint",
    "give me a bigger hint", "bigger hint", "give me the next hint", "next hint", "one more hint",
}
_COMMAND_START_RE = re.compile(
    r"^(?:run|read|go|open|close|insert|add|delete|remove|show|explain|give|what|where|why|how|when|which|who|"
    r"undo|redo|apply|reject|fix|rename|comment|uncomment|find|search|start|stop|list|check|make|create|save|"
    r"load|export|import|switch|turn|join|leave|submit|next|previous|repeat|help|cancel|clear|use|type|select|"
    r"copy|paste|analy[sz]e|step|walk|trace|debug|summari[sz]e|describe|move|jump|focus|set|change|replace|"
    r"write|generate|toggle|enable|disable|mute|unmute|pause|resume|quiz|grade|teach|define|call|print|test|"
    r"compare|tell|speak|say|hey|hi|hello|please|can|could|would|should|do|does|let|exit|quit|open|close|"
    r"yes|no|okay|ok)\b"
)


def _code_sig(code: str) -> str:
    return hashlib.sha1((code or "").strip().encode("utf-8", "replace")).hexdigest()[:16]


def _numbers_in(text: str) -> set:
    found = {int(n) for n in re.findall(r"-?\d+", text)}
    found.update(_NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", text) if w in _NUMBER_WORDS)
    return found


def _mentions(text: str, keyword: str) -> bool:
    keyword = keyword.lower()
    if re.fullmatch(r"[a-z0-9_ ]+", keyword):
        return re.search(r"(?<![a-z0-9_])" + re.escape(keyword) + r"(?![a-z0-9_])", text) is not None
    return keyword in text


def _range_loop(code: str) -> Optional[Dict[str, Any]]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        m = re.search(r"\bfor\s+([A-Za-z_]\w*)\s+in\s+range\s*\(\s*(\d+)\s*\)", code or "")
        return {"target": m.group(1), "start": 0, "stop": int(m.group(2))} if m else None
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Call)
                and isinstance(node.iter.func, ast.Name) and node.iter.func.id == "range"
                and 1 <= len(node.iter.args) <= 2
                and all(isinstance(a, ast.Constant) and isinstance(a.value, int) for a in node.iter.args)):
            args = [a.value for a in node.iter.args]
            start, stop = (0, args[0]) if len(args) == 1 else (args[0], args[1])
            return {"target": node.target.id, "start": start, "stop": stop}
    return None


def _loop_target(code: str) -> str:
    m = re.search(r"\bfor\s+([A-Za-z_]\w*)\s+in\b", code or "")
    return m.group(1) if m else ""


def _first_value(code: str) -> Optional[str]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None
    for stmt in tree.body:
        value = None
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
            value = stmt.value.value
        elif (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
              and isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "print"
              and stmt.value.args and isinstance(stmt.value.args[0], ast.Constant)):
            value = stmt.value.args[0].value
        if value is not None and not isinstance(value, bytes):
            return str(value)
    return None


def _assigned_string(code: str, name: str) -> Optional[str]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            return node.value.value
    return None


def _first_list_item(code: str) -> Optional[str]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List) and node.value.elts:
            first = node.value.elts[0]
            if isinstance(first, ast.Constant):
                return str(first.value)
    return None


def _function_call_line(code: str) -> Optional[int]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for stmt in tree.body:
        if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name) and stmt.value.func.id in defined):
            return stmt.lineno
    return None


def _spec(kind: str, question: str, explain: str, hints, **extra) -> Dict[str, Any]:
    return {"type": kind, "question": question, "explain": explain, "hints": list(hints), **extra}


def _loop_spec(code: str) -> Dict[str, Any]:
    loop = _range_loop(code)
    if loop and loop["stop"] > loop["start"]:
        name, last = loop["target"], loop["stop"] - 1
        values = list(range(loop["start"], loop["stop"]))
        shown = ", ".join(str(v) for v in values) if len(values) <= 6 else f"{values[0]} up to {last}"
        rng = f"range({loop['stop']})" if loop["start"] == 0 else f"range({loop['start']}, {loop['stop']})"
        return _spec(
            "number", f"What value does {name} have on the last loop run?",
            f"{rng} gives {shown}, so {name} is {last} on the last run.",
            [f"{rng} stops before {loop['stop']}.",
             f"List the values {rng} gives, then pick the last one."],
            value=last,
        )
    target = _loop_target(code) or "the loop variable"
    return _spec(
        "keywords", "What changes each time this loop runs?",
        f"Each time the loop runs, {target} takes the next value.",
        ["Look at the name right after the word for.", f"Think about what {target} holds on each round."],
        any=[k for k in (_loop_target(code), "value", "item", "number", "element", "counter", "variable") if k],
    )


def _question_spec(code: str, analysis: Dict[str, Any]) -> Dict[str, Any]:
    if analysis.get("exception_type") in {"IndentationError", "TabError"}:
        code_line = analysis.get("code_line") or "that line"
        return _spec(
            "keywords", f"Why does Python need spaces before {code_line} in this program?",
            "Python uses indentation to know which lines belong inside a block, so the indented line runs as "
            "part of the loop, if statement, or function above it.",
            ["Think about which line should run as part of the block above it.",
             "The spaces tell Python the line is inside the block."],
            any=["inside", "block", "belong", "part of", "body", "indent", "under", "within"],
        )
    if _has_loop(code):
        return _loop_spec(code)
    if (code or "").strip():
        value = _first_value(code)
        explain = (f"The first value this program stores or prints is {value}." if value is not None
                   else "Run the program and compare your answer with the output.")
        if value is not None and re.fullmatch(r"-?\d+", value.strip()):
            return _spec("number", "What is the first value this program stores or prints?", explain,
                         ["Look at line 1.", "Read the value after the equals sign or inside print."],
                         value=int(value.strip()))
        if value is not None:
            return _spec("keywords", "What is the first value this program stores or prints?", explain,
                         ["Look at line 1.", "Read the value after the equals sign or inside print."],
                         any=[value.strip().lower()])
        return _spec("open", "What is the first value this program stores or prints?", explain,
                     ["Look at line 1.", "Run the program and listen to the first output line."])
    return _spec(
        "choice", "What small Python idea do you want to practice next: print, variables, or loops?",
        "Pick print, variables, or loops.", ["Say print, variables, or loops."],
        choices={"print": "1", "variable": "2", "variables": "2", "loop": "4", "loops": "4"},
    )


def lesson_question_spec(lesson_id: str, question: str, code: str) -> Dict[str, Any]:
    """Gradable spec for a Programming Literacy lesson's check question."""
    if lesson_id == "loops":
        return _loop_spec(code)
    if lesson_id == "print":
        return _spec("keywords", question, "print shows a value in the program output when the program runs.",
                     ["Run the program and listen to the output.", "print sends text to the output."],
                     any=["show", "shows", "display", "displays", "output", "screen", "print", "prints", "text",
                          "message", "say", "says", "write", "writes"])
    if lesson_id == "variables":
        value = _assigned_string(code, "name") or "CodeUp"
        return _spec("keywords", question, f"The variable name stores the text {value}.",
                     ["Find the line that starts with name =.", "Read the text inside the quotes."],
                     any=[value.lower()])
    if lesson_id == "if":
        return _spec("keywords", question, "score is 7, and 7 >= 5 is true, so Python runs the if branch and prints pass.",
                     ["Compare the value of score with 5.", "Is 7 greater than or equal to 5?"],
                     any=["7", "seven", ">=", "greater", "at least", "more than", "bigger", "larger", "true", "higher"])
    if lesson_id == "lists":
        item = _first_list_item(code) or "apple"
        return _spec("keywords", question, f"Positions start at zero, so position 0 holds {item}.",
                     ["List positions start at zero.", "Position 0 is the first item in the list."],
                     any=[item.lower()])
    if lesson_id == "functions":
        line = _function_call_line(code) or 4
        return _spec("number", question, f"Line {line} calls the function by writing its name with parentheses.",
                     ["The def line only defines the function.", "Look for the function name followed by parentheses, not after def."],
                     value=line)
    return _spec("open", question, "Run the lesson code and compare your answer with the output.", ["Run the code first."])


def ask_understanding_question(mem: Dict[str, Any], spec: Dict[str, Any], code: str, source: str = "tutor") -> None:
    mem["pending_understanding"] = {
        "spec": spec, "code_sig": _code_sig(code), "asked_at": time.time(),
        "attempts": 0, "hints": 0, "source": source,
    }


def _fresh_pending(mem: Dict[str, Any], code: str, *, require_same_code: bool) -> Optional[Dict[str, Any]]:
    pending = mem.get("pending_understanding")
    if not isinstance(pending, dict) or not isinstance(pending.get("spec"), dict):
        return None
    if time.time() - float(pending.get("asked_at") or 0) > _PENDING_MAX_AGE_SECONDS:
        mem.pop("pending_understanding", None)
        return None
    if require_same_code and pending.get("code_sig") != _code_sig(code):
        return None
    return pending


def _grade(spec: Dict[str, Any], answer: str) -> Optional[bool]:
    text = _norm(answer)
    kind = spec.get("type")
    if kind == "number":
        nums = _numbers_in(text)
        return nums == {int(spec["value"])}
    if kind == "keywords":
        return any(_mentions(text, k) for k in spec.get("any") or [])
    if kind == "choice":
        return any(_mentions(text, k) for k in (spec.get("choices") or {}))
    return None


def _result(message: str, kind: str, correct: Optional[bool] = None) -> Dict[str, Any]:
    return {"message": message, "speech": message, "understanding_result": kind, "understanding_correct": correct}


def _record_answer(mem: Dict[str, Any], answer: str, spec: Dict[str, Any], correct: Optional[bool], message: str) -> None:
    mem["last_understanding_answer"] = {"answer": answer, "question": spec.get("question"), "correct": correct,
                                        "message": message}
    key = "understanding_correct" if correct else "understanding_incorrect"
    if correct is not None:
        mem[key] = int(mem.get(key) or 0) + 1


def grade_understanding_answer(mem: Dict[str, Any], answer: str) -> Dict[str, Any]:
    pending = mem.get("pending_understanding") or {}
    spec = pending.get("spec") or {}
    correct = _grade(spec, answer)
    if spec.get("type") == "choice" and correct:
        choice = next(k for k in spec["choices"] if _mentions(_norm(answer), k))
        lesson = spec["choices"][choice]
        msg = f"Good choice. Say \"start lesson {lesson}\" to practice {choice}."
        mem.pop("pending_understanding", None)
        _record_answer(mem, answer, spec, True, msg)
        return _result(msg, "correct", True)
    if correct is None:
        msg = f"Thanks. I cannot check that answer automatically. {spec.get('explain', '')}".strip()
        mem.pop("pending_understanding", None)
        _record_answer(mem, answer, spec, None, msg)
        return _result(msg, "ungraded")
    if correct:
        msg = f"Correct. {spec.get('explain', '')} Say \"check my understanding\" for another question.".strip()
        mem.pop("pending_understanding", None)
        _record_answer(mem, answer, spec, True, msg)
        return _result(msg, "correct", True)
    pending["attempts"] = int(pending.get("attempts") or 0) + 1
    if pending["attempts"] >= _MAX_WRONG_ATTEMPTS:
        msg = (f"Not quite. Here is the answer: {spec.get('explain', '')} "
               "Say \"check my understanding\" to try another question.")
        mem.pop("pending_understanding", None)
        _record_answer(mem, answer, spec, False, msg)
        return _result(msg, "revealed", False)
    msg = f"Not quite. {spec.get('question', '')} Try again, or say \"give me a hint\"."
    _record_answer(mem, answer, spec, False, msg)
    return _result(msg, "incorrect", False)


def understanding_hint(mem: Dict[str, Any]) -> Dict[str, Any]:
    pending = mem.get("pending_understanding") or {}
    spec = pending.get("spec") or {}
    hints = spec.get("hints") or ["Read the code one line at a time."]
    idx = int(pending.get("hints") or 0)
    pending["hints"] = idx + 1
    mem["tutor_hints_requested"] = int(mem.get("tutor_hints_requested") or 0) + 1
    if idx < len(hints):
        msg = f"Question hint {idx + 1} of {len(hints)}: {hints[idx]} {spec.get('question', '')} Answer when ready."
    else:
        msg = f"No more hints for this question. {spec.get('question', '')} Answer, or say \"I don't know\" to hear the answer."
    return _result(msg, "hint")


def reveal_understanding_answer(mem: Dict[str, Any]) -> Dict[str, Any]:
    pending = mem.pop("pending_understanding", None) or {}
    spec = pending.get("spec") or {}
    msg = f"Here is the answer: {spec.get('explain', '')} Say \"check my understanding\" to try another question."
    _record_answer(mem, "", spec, False, msg)
    return _result(msg, "revealed", False)


def route_pending_understanding(text: str, mem: Dict[str, Any], code: str = "") -> Optional[Dict[str, Any]]:
    """Claim a learner's reply to the open understanding question, or None.

    Explicit answers ("my answer is 2") are always accepted while a question is
    open. A bare reply ("2", "i is 2 on the last run") is only treated as an
    answer while the code is unchanged since the question was asked and the
    reply does not look like a CodeUp command."""
    t = _norm(text)
    if not t or not isinstance(mem, dict):
        return None
    if _fresh_pending(mem, code, require_same_code=False) is None:
        return None
    if t in _REVEAL_PHRASES:
        return reveal_understanding_answer(mem)
    same_code = _fresh_pending(mem, code, require_same_code=True) is not None
    if t in _QUESTION_HINT_PHRASES:
        return understanding_hint(mem) if same_code else None
    m = _ANSWER_PREFIX_RE.match(t)
    if m and m.group(1).strip():
        return grade_understanding_answer(mem, m.group(1).strip())
    if not same_code or len(t.split()) > 10 or _COMMAND_START_RE.match(t):
        return None
    if command_kind(t) is not None:
        return None
    return grade_understanding_answer(mem, t)
