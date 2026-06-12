"""Trainer Review Mode — a concise trainer-facing review of what the learner
*did* this session and how they learned, built from deterministic session
memory and the current project.

This is different from the project report (which describes what the project
contains). It never invents student performance or institutional details — it
only restates facts already recorded in session memory.

Pure and Flask-free.
"""

from typing import Any, Dict, List, Optional

import learning_recap
import structure_tools

# Feature tags (from session_memory._FEATURE_BY_ACTION) that indicate the
# learner used CodeUp's handoff / review features.
_HANDOFF_FEATURES = {"exported the project", "made a project report", "reviewed the session"}
_DEBUG_FEATURES = {"debugged errors"}


def _list(mem: Dict[str, Any], key: str) -> List[str]:
    value = mem.get(key)
    return [str(v) for v in value] if isinstance(value, list) else []


def _project_files(project_state: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(project_state, dict):
        return []
    files = project_state.get("files")
    if isinstance(files, dict):
        return [str(k) for k in files.keys()]
    if isinstance(files, list):
        return [str(f) for f in files]
    return []


def _has_debugging(mem: Dict[str, Any]) -> bool:
    return bool(
        mem.get("last_run_ok") is False
        or str(mem.get("last_run_error") or "").strip()
        or _DEBUG_FEATURES.intersection(_list(mem, "features_used"))
        or mem.get("hint_level") in ("bigger", "answer")
    )


def _worked_on(mem: Dict[str, Any], code: str) -> List[str]:
    concepts = _list(mem, "concepts_practiced")
    if not concepts and code.strip():
        try:
            concepts = list(structure_tools.build_structure_snapshot(code).get("concepts") or [])
        except Exception:
            concepts = []
    worked = list(concepts)
    if _has_debugging(mem) and "debugging" not in worked:
        worked.append("debugging")
    return worked


def _errors_clause(mem: Dict[str, Any]) -> str:
    err = str(mem.get("last_run_error") or "").strip()
    ok = mem.get("last_run_ok")
    if err or ok is False:
        if ok is True:
            return "They hit an error while running, and the latest run now succeeds."
        return "They hit an error while running that is still open in the latest run."
    if ok is True:
        return "Their code ran without errors."
    return "No runs were recorded yet."


def _hints_clause(mem: Dict[str, Any]) -> str:
    level = mem.get("hint_level")
    if level == "answer":
        return "They worked up the staged hints to the full answer."
    if level == "bigger":
        return "They used the staged hints, asking for a bigger hint."
    return ""


def _project_clause(code: str, project_files: List[str], entry: str) -> str:
    """A description of the project (no leading label)."""
    if len(project_files) > 1:
        helpers = ", ".join(project_files[:6])
        tail = f" The entry point is {entry}." if entry else ""
        return f"a multi-file project with {len(project_files)} files ({helpers})." + tail
    if code.strip():
        try:
            snap = structure_tools.build_structure_snapshot(code)
            return f"a single-file program. {snap.get('summary', '').strip()}"
        except Exception:
            return "a single-file program."
    return "nothing in the editor yet."


def _join(items: List[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def build_trainer_review(code: str, project_state: Optional[Dict[str, Any]] = None,
                         session_memory: Optional[Dict[str, Any]] = None,
                         verbosity: str = "normal") -> Dict[str, Any]:
    """Return {message (markdown), speech (short summary)} for a trainer."""
    code = code or ""
    mem = session_memory or {}
    verbosity = (verbosity or "normal").strip().lower()
    project_files = _project_files(project_state)
    entry = ""
    if isinstance(project_state, dict):
        entry = str(project_state.get("entry") or project_state.get("active_file") or "")

    features = _list(mem, "features_used")
    worked = _worked_on(mem, code)
    next_activity = learning_recap.suggest_next_step(mem)

    has_activity = bool(features or worked or mem.get("last_run_ok") is not None
                        or mem.get("last_gen_prompt") or project_files)

    if not has_activity:
        speech = ("Trainer notes: the learner has not done much yet this session. "
                  f"Suggested next activity: {next_activity}")
        message = ("**Trainer notes**\n\n"
                   "- **Activity:** The learner has not done much yet this session.\n"
                   f"- **Suggested next activity:** {next_activity}")
        return {"message": message, "speech": speech, "next_activity": next_activity, "limited": True}

    debugging = _has_debugging(mem)
    handoff = bool(_HANDOFF_FEATURES.intersection(features))

    # ----- spoken summary -----
    bits: List[str] = []
    if worked:
        bits.append(f"The learner practised {_join(worked)}")
    elif debugging:
        bits.append("The learner practised debugging")
    else:
        bits.append("The learner worked in CodeUp")
    if features:
        bits.append(f"using {_join(features)}")
    speech = ". ".join([", ".join(bits)])
    speech = "Trainer notes: " + speech + "."
    errors = _errors_clause(mem)
    if errors:
        speech += " " + errors
    hints = _hints_clause(mem)
    if hints:
        speech += " " + hints
    if handoff:
        speech += " They also used CodeUp's handoff features (export, report, or recap)."
    speech += f" Suggested next activity: {next_activity}"

    # ----- markdown for the trainer -----
    lines = ["**Trainer notes**", ""]
    if worked:
        lines.append(f"- **Worked on:** {_join(worked)}")
    if _list(mem, "concepts_practiced"):
        lines.append(f"- **Concepts practised:** {_join(_list(mem, 'concepts_practiced'))}")
    lines.append(f"- **Project:** {_project_clause(code, project_files, entry)}")
    if features:
        lines.append(f"- **Features used:** {_join(features)}")
    lines.append(f"- **Errors:** {errors}")
    if hints:
        lines.append(f"- **Hints:** {hints}")
    if handoff:
        lines.append("- **Handoff features:** export, report, or recap were used this session.")
    if debugging:
        lines.append("- **Debugging:** The learner spent time debugging during the session.")
    lines.append(f"- **Suggested next activity:** {next_activity}")
    message = "\n".join(lines)

    if verbosity == "concise":
        message = "\n".join(lines[:2] + [lines[-1]])

    return {"message": message, "speech": speech, "next_activity": next_activity,
            "debugging": debugging, "handoff": handoff}
