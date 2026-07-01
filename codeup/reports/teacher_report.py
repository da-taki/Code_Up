"""Deterministic Teacher Report + Session Recap, upgraded with cockpit data.

Turns a CodeUp session into a clear report for a teacher, trainer, accessibility
reviewer, or pilot evaluator. It aggregates facts that already live in session
memory and the cockpit modules built in earlier slices:

  - Project Map       (project_map)        -> files, roles, imports, entry point
  - Error Trace       (error_trace)        -> error type, line, cause, fix
  - Audio Diff Review (session change_history + audio_diff) -> changes + risk
  - State / Var Watch (session last_state_trace) -> watched vars, values, loops
  - run history / command counts            -> activity

Everything here is deterministic and tolerant of missing data: any feature with
no data is simply skipped, never invented. No AI. Code values are redacted so the
report does not leak secrets. The Live Assistant transcript lives only in the
browser, so it is described, not fabricated (see Accessibility Workflow).
"""

import re
from typing import Any, Dict, List, Optional

from codeup.accessibility import audio_diff
from codeup.runtime import error_trace
from codeup.learning import learning_recap
from codeup.projects import project_map
from codeup.reports import report_support

__all__ = ["build_report", "learner_recap", "what_changed", "what_errors_fixed",
           "next_practice"]

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[:=]")
# A variable name or value that should never be shown in a report.
_SECRET_NAME_RE = re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|credential)")
_SECRET_VALUE_RE = re.compile(r"(?i)(sk-[a-z0-9]|aws_|akia[0-9a-z]|bearer\s|-----begin)")


def _redact(text: str) -> str:
    if not text:
        return ""
    out = []
    for line in str(text).splitlines():
        out.append("[redacted possible secret]" if _SECRET_RE.search(line) else line)
    return "\n".join(out)


def _clip(text: str, limit: int = 240) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _project_state(mem: Dict[str, Any], project_state: Optional[Dict[str, Any]],
                   current_code: str) -> Dict[str, Any]:
    if project_state and (project_state.get("files") or project_state.get("code")):
        return project_state
    files = mem.get("project_files") if isinstance(mem.get("project_files"), dict) else None
    if files:
        return {"is_project": True, "files": files}
    return {"is_project": False, "code": current_code or ""}


# ---- individual section facts ------------------------------------------

def _commands_section(mem: Dict[str, Any]) -> List[str]:
    counts = mem.get("command_type_counts") if isinstance(mem.get("command_type_counts"), dict) else {}
    total = max(0, int(mem.get("command_count") or 0))
    if not total and not counts:
        return ["No commands recorded yet."]
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
    detail = ", ".join(f"{name} ({count})" for name, count in top) if top else "various commands"
    return [f"The learner used {total} command{'s' if total != 1 else ''}: {detail}."]


def _errors_section(mem: Dict[str, Any], current_code: str) -> List[str]:
    last_error = str(mem.get("last_run_error") or "").strip()
    counts = mem.get("error_type_counts") if isinstance(mem.get("error_type_counts"), dict) else {}
    total_errors = sum(int(v) for v in counts.values()) if counts else 0
    lines: List[str] = []
    if last_error:
        analysis = error_trace.analyze(last_error, traceback_text=str(mem.get("last_run_traceback") or ""),
                                       code=current_code)
        where = f", line {analysis['line']}" if analysis.get("line") else ""
        lines.append(f"Most recent error: {analysis.get('exception_type') or 'an error'}{where}. "
                     f"{analysis.get('likely_cause') or ''}".strip())
    if counts:
        seen = ", ".join(f"{name} ({count})" for name, count in sorted(counts.items()) if count)
        if seen:
            lines.append(f"Errors seen this session: {seen}.")
    if mem.get("fixes_applied") or mem.get("fixes_rejected"):
        lines.append(f"Fixes applied: {int(mem.get('fixes_applied') or 0)}; "
                     f"proposals rejected: {int(mem.get('fixes_rejected') or 0)}.")
    if not lines:
        lines.append("No errors were recorded this session." if not total_errors
                     else "Some errors occurred but no details were stored.")
    return lines


def _changes_section(mem: Dict[str, Any]) -> List[str]:
    history = mem.get("change_history") if isinstance(mem.get("change_history"), list) else []
    if not history:
        return ["No code changes were reviewed this session."]
    risks = {"low": 0, "medium": 0, "high": 0}
    last_meaning = ""
    for record in history:
        change = audio_diff.summarize_change(record.get("before", ""), record.get("after", ""),
                                             file_name=record.get("file", ""))
        risks[change["risk"]] = risks.get(change["risk"], 0) + 1
        if change["changes"]:
            last_meaning = audio_diff.change_meaning(change["changes"][0])
    risk_bits = ", ".join(f"{n} {level}" for level, n in risks.items() if n)
    lines = [f"{len(history)} change{'s' if len(history) != 1 else ''} reviewed"
             + (f" ({risk_bits} risk)." if risk_bits else ".")]
    if last_meaning:
        lines.append(f"Most recent change: {_clip(last_meaning, 160)}")
    return lines


def _state_section(mem: Dict[str, Any]) -> List[str]:
    watched = mem.get("watched_variables") if isinstance(mem.get("watched_variables"), list) else []
    bundle = mem.get("last_state_trace") if isinstance(mem.get("last_state_trace"), dict) else None
    lines: List[str] = []
    if watched:
        lines.append("Watched variables: " + ", ".join(str(v) for v in watched[:8]) + ".")
    if bundle and not bundle.get("error"):
        vars_state = bundle.get("vars") if isinstance(bundle.get("vars"), dict) else {}
        shown = []
        for name, info in list(vars_state.items())[:6]:
            value = str(info.get("value", ""))
            if _SECRET_NAME_RE.search(name) or _SECRET_VALUE_RE.search(value):
                shown.append("[a sensitive value was hidden]")
            else:
                shown.append(f"{name} ended as {_clip(value, 40)}")
        if shown:
            lines.append("Final traced values: " + _redact(", ".join(shown)) + ".")
        if bundle.get("loop"):
            lines.append(_clip(str(bundle["loop"]), 160))
        conditions = bundle.get("conditions") if isinstance(bundle.get("conditions"), list) else []
        if conditions:
            trues = sum(1 for c in conditions if c.get("result"))
            lines.append(f"The learner inspected {len(conditions)} condition result(s); "
                         f"{trues} were true.")
    if not lines:
        lines.append("The learner did not explore program state this session.")
    return lines


def _input_section(mem: Dict[str, Any]) -> List[str]:
    summary = mem.get("last_input_run_summary") if isinstance(mem.get("last_input_run_summary"), dict) else {}
    pending = mem.get("pending_stdin_values") if isinstance(mem.get("pending_stdin_values"), list) else []
    prompts = mem.get("input_prompts") if isinstance(mem.get("input_prompts"), list) else []
    lines: List[str] = []
    if summary.get("used_input"):
        count = int(summary.get("count") or 0)
        source = str(summary.get("source") or "input").replace("_", " ")
        lines.append(f"Program used input: {count} value{'s' if count != 1 else ''} supplied by {source}.")
    if prompts:
        lines.append(f"Input variables/prompts detected: {', '.join(str(p) for p in prompts[:6])}.")
    if pending:
        lines.append(f"Pending input values are set for the next run: {len(pending)}.")
    if not lines:
        lines.append("No program input was recorded this session.")
    return lines


def _next_practice(mem: Dict[str, Any], concepts: List[str]) -> str:
    if mem.get("last_run_ok") is False:
        return "Practice reading an error message and fixing it, then run the code again."
    if "loops" in concepts and "conditionals (if/else)" not in concepts:
        return "Next, practice writing one loop with an if statement inside it."
    if "functions" not in concepts and concepts:
        return "Next, practice putting your code inside a small function and calling it."
    return learning_recap.suggest_next_step(mem)


# ---- public builders ---------------------------------------------------

def build_report(mem: Optional[Dict[str, Any]], project_state: Optional[Dict[str, Any]] = None,
                 current_code: str = "", *, storage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Full, accessible teacher report. Tolerant of missing data."""
    mem = mem or {}
    storage = storage or {}
    state = _project_state(mem, project_state, current_code)
    base = report_support.build_project_report(state, mem) or {}
    code_for_concepts = current_code or state.get("code") or ""
    concepts = base.get("concepts") or report_support.detect_python_concepts(code_for_concepts)

    has_content = bool(
        base.get("has_content") or mem.get("command_count") or mem.get("change_history")
        or mem.get("last_run_error") or mem.get("last_run_output") or mem.get("watched_variables")
        or mem.get("last_input_run_summary") or mem.get("pending_stdin_values"))

    if not has_content:
        msg = ("# CodeUp Teacher Report\n\nThere is not much session activity yet. "
               "Ask the learner to write or run some code, then make the report again.")
        return {"report_md": msg, "speech": "There is not much activity yet. "
                "Ask the learner to write or run code first, then make the report again.",
                "has_content": False, "sections": {}}

    project_summary = _clip(project_map.narrate(state).replace("\n", " "), 320)
    files = base.get("files") or []
    file_lines = [f"- {f['path']}: {f.get('role', 'module')}" for f in files[:12]] or ["- No files yet."]
    sections: Dict[str, List[str]] = {
        "Project Summary": [project_summary],
        "Files and Structure": file_lines,
        "Commands Used": _commands_section(mem),
        "Concepts Practiced": [", ".join(concepts) + "." if concepts else "No concepts detected yet."],
        "Errors and Debugging": _errors_section(mem, code_for_concepts),
        "Code Changes Reviewed": _changes_section(mem),
        "State and Variable Understanding": _state_section(mem),
        "Program Input": _input_section(mem),
        "Accessibility Workflow": [
            f"Screen reader mode: {'on' if storage.get('screen_reader_mode') else 'off'}. "
            "Spoken output, project map, error narration and state narration were available. "
            "Live Assistant turns are kept in the browser and are not included in this backend report."],
        "Final Output / Final State": [
            "Last output: " + (_clip(str(mem.get("last_run_output") or "none"), 200) or "none")],
        "Suggested Next Practice": [_next_practice(mem, concepts)],
    }

    lines = ["# CodeUp Teacher Report", ""]
    for i, (title, body) in enumerate(sections.items(), 1):
        lines.append(f"## {i}. {title}")
        lines.extend(_redact("\n".join(body)).splitlines())
        lines.append("")
    lines += ["## Privacy",
              "This report is session-based and local. Possible secrets are redacted and full code is not dumped."]
    report_md = "\n".join(lines).strip()

    speech = (f"Teacher report ready. {project_summary} "
              + sections["Commands Used"][0] + " "
              + sections["Errors and Debugging"][0] + " "
              "Say read the full report, or ask about a section like errors or changes.")
    return {"report_md": report_md, "speech": _clip(speech, 600), "has_content": True,
            "sections": sections}


def learner_recap(mem: Optional[Dict[str, Any]], current_code: str = "") -> Dict[str, Any]:
    """Short, encouraging learner-facing recap (builds on learning_recap)."""
    mem = mem or {}
    recap = learning_recap.build_recap(mem)
    concepts = report_support.detect_python_concepts(current_code or "")
    text = recap.get("recap", "")
    if concepts and recap.get("has_history"):
        text = "You worked with " + ", ".join(concepts[:4]) + ". " + text
    return {"recap": text, "speech": text, "next_step": recap.get("next_step", ""),
            "has_history": recap.get("has_history", False)}


def what_changed(mem: Optional[Dict[str, Any]], project_state: Optional[Dict[str, Any]] = None,
                 current_code: str = "") -> str:
    """'what changed in this project' — uses change history, else a code summary."""
    mem = mem or {}
    history = mem.get("change_history") if isinstance(mem.get("change_history"), list) else []
    if history:
        return " ".join(_changes_section(mem))
    state = _project_state(mem, project_state, current_code)
    return ("No tracked code changes yet. " + _clip(project_map.narrate(state).replace("\n", " "), 240))


def what_errors_fixed(mem: Optional[Dict[str, Any]], current_code: str = "") -> str:
    """'what errors did I fix' — uses run/error history and fix counters."""
    mem = mem or {}
    if not (mem.get("last_run_error") or mem.get("error_type_counts") or mem.get("fixes_applied")):
        return "No errors were recorded this session. Run some code to see error help if anything breaks."
    return " ".join(_errors_section(mem, current_code))


def next_practice(mem: Optional[Dict[str, Any]], current_code: str = "") -> str:
    return _next_practice(mem or {}, report_support.detect_python_concepts(current_code or ""))
