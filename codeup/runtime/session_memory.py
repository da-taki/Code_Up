import hashlib
import re
import time
from typing import Any, Dict, List, Optional

MEMORY_KEY = "session_memory"
_PENDING_TTL_SECONDS = 180

_MAX_TEXT = 400
_MAX_OUTPUT = 800
_MAX_ERROR = 600
_MAX_PROMPT = 600
_MAX_CODE = 5000
_MAX_FILES = 50
_MAX_VALUES = 50


def _clip(value: Any, limit: int = _MAX_TEXT) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def code_hash(code: Any) -> str:
    value = str(code or "").replace("\x00", "")
    if not value.strip():
        return ""
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def new_memory() -> Dict[str, Any]:
    return {
        "last_utterance": "",
        "latest_user_request": "",
        "last_intent": "",
        "last_action": "",
        "last_actions": [],
        "command_count": 0,
        "command_type_counts": {},
        "last_gen_prompt": "",       # last generation request (prompt text)
        "last_gen_summary": "",
        "last_generated_code": "",   # bounded latest generated single-file code
        "last_generated_goal": "",
        "last_generated_code_hash": "",
        "last_code_generation_command": "",
        "last_code_edit_command": "",
        "last_code_generation_time": 0.0,
        "current_editor_code_hash": "",
        "last_project_manifest": {},  # bounded latest generated/open project facts
        "last_edit_request": "",
        "last_edit_summary": "",
        "last_edit_old_code": "",
        "last_edit_new_code": "",
        "last_fix_explanation": "",
        "change_history": [],
        "undo_stack": [],
        "last_change": None,
        "change_cursor": 0,
        "pending_change_proposal": None,
        "watched_variables": [],
        "last_state_trace": None,
        "student_satisfied": None,
        "last_run_output": "",
        "last_run_error": "",
        "last_run_traceback": "",
        "last_run_ok": None,
        "last_run_inputs": [],
        "run_count": 0,
        "last_explain_target": "",
        "last_active_file": "",
        "last_opened_file": "",
        "project_files": [],
        "tutorial_module": "",
        "input_values": [],
        "input_prompts": [],
        "pending_stdin_values": [],
        "awaiting_program_input": False,
        "awaiting_input_code_hash": "",
        "awaiting_input_prompts": [],
        "awaiting_input_values": [],
        "awaiting_input_index": 0,
        "last_input_run_summary": {},
        "code_map_summary": "",
        "features_used": [],
        "concepts_practiced": [],
        "pending_clarification": None,
        "hint_level": "small",
        "error_type_counts": {},
        "landmarks": {},
        "last_navigated": None,       # last navigate-by-meaning target block
    }


_FEATURE_BY_ACTION = {
    "run": "ran code",
    "generate_code": "generated code",
    "walk_through": "asked for explanations",
    "mentor_chat": "asked for explanations",
    "explain_simply": "debugged errors",
    "fix": "debugged errors",
    "code_map": "used code mapping",
    "mentor_code_map": "used code mapping",
    "show_structure": "used code mapping",
    "read_outline": "used code mapping",
    "sonify_block": "used sonification",
    "sonify_file": "used sonification",
    "step_narration": "traced execution",
    "story_mode": "traced execution",
    "conversational_edit": "edited code by voice",
    "insert_variable": "edited code by voice",
    "insert_function": "edited code by voice",
    "append_line": "edited code by voice",
    "export_project": "exported the project",
    "project_report": "made a project report",
    "learning_recap": "reviewed the session",
    "open_project_file": "worked in project mode",
    "run_project_file": "worked in project mode",
    "read_project_files": "worked in project mode",
}


def record_activity(mem: Dict[str, Any], action: str, *, concept: str = "") -> None:
    feature = _FEATURE_BY_ACTION.get(str(action or ""))
    if feature:
        feats = mem.setdefault("features_used", [])
        if feature not in feats:
            feats.append(feature)
            del feats[20:]
    concept = _clip(concept, 40)
    if concept:
        concepts = mem.setdefault("concepts_practiced", [])
        if concept not in concepts:
            concepts.append(concept)
            del concepts[20:]


_HINT_LEVELS = ("small", "bigger", "answer")


def get_hint_level(mem: Dict[str, Any]) -> str:
    level = mem.get("hint_level", "small")
    return level if level in _HINT_LEVELS else "small"


def set_hint_level(mem: Dict[str, Any], level: str) -> str:
    mem["hint_level"] = level if level in _HINT_LEVELS else "small"
    return mem["hint_level"]


def escalate_hint(mem: Dict[str, Any]) -> str:
    idx = _HINT_LEVELS.index(get_hint_level(mem))
    mem["hint_level"] = _HINT_LEVELS[min(idx + 1, len(_HINT_LEVELS) - 1)]
    return mem["hint_level"]


def get_landmarks(mem: Dict[str, Any]) -> Dict[str, Any]:
    landmarks = mem.get("landmarks")
    if not isinstance(landmarks, dict):
        landmarks = {}
        mem["landmarks"] = landmarks
    return landmarks


def record_navigation(mem: Dict[str, Any], nav: Optional[Dict[str, Any]]) -> None:
    if not nav or not nav.get("line"):
        return
    mem["last_navigated"] = {
        "line": int(nav.get("line") or 0),
        "end_line": int(nav.get("end_line") or nav.get("line") or 0),
        "block_type": _clip(nav.get("block_type", ""), 40),
        "preview": _clip(nav.get("preview") or nav.get("code", ""), 80),
    }


def get_last_navigated(mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    nav = mem.get("last_navigated")
    return nav if isinstance(nav, dict) else None


def get_memory(storage: Dict[str, Any]) -> Dict[str, Any]:
    mem = storage.get(MEMORY_KEY)
    if not isinstance(mem, dict):
        mem = new_memory()
        storage[MEMORY_KEY] = mem
    return mem


def record_utterance(mem: Dict[str, Any], text: str, intent: str = "", action: str = "") -> None:
    mem["last_utterance"] = _clip(text)
    if intent:
        mem["last_intent"] = _clip(intent, 60)
    if action:
        mem["last_action"] = _clip(action, 60)


def record_actions(mem: Dict[str, Any], action_names: List[str]) -> None:
    mem["last_actions"] = [_clip(a, 60) for a in (action_names or [])][:10]


def record_generation(mem: Dict[str, Any], prompt: str, code: Optional[str] = None) -> None:
    if prompt:
        prompt_text = _clip(prompt, _MAX_PROMPT)
        lower_prompt = prompt_text.lower()
        if lower_prompt.startswith("earlier you generated code for:") or lower_prompt.startswith("using the current program"):
            mem["last_edit_request"] = prompt_text
            mem["student_satisfied"] = False
        else:
            mem["last_gen_prompt"] = prompt_text
            mem["last_generated_goal"] = prompt_text
            mem["last_code_generation_command"] = prompt_text
            mem["last_code_generation_time"] = time.time()
            mem["latest_user_request"] = prompt_text
    if code:
        bounded_code = _clip(code, _MAX_CODE)
        mem["last_generated_code"] = bounded_code
        mem["last_generated_code_hash"] = code_hash(bounded_code)
        mem["current_editor_code_hash"] = mem["last_generated_code_hash"]
        lines = bounded_code.strip().splitlines()
        first = lines[0].strip() if lines else ""
        mem["last_gen_summary"] = _clip(f"{len(lines)} lines, starts with: {first}", 200)


def record_editor_code(mem: Dict[str, Any], code: str) -> None:
    mem["current_editor_code_hash"] = code_hash(code)


def record_run(mem: Dict[str, Any], *, output: str = "", error: str = "",
               traceback_text: str = "",
               inputs: Optional[List[str]] = None, ran_ok: Optional[bool] = None,
               input_source: str = "") -> None:
    mem["run_count"] = max(0, int(mem.get("run_count") or 0)) + 1
    mem["last_run_traceback"] = _clip(traceback_text, _MAX_ERROR * 2)
    mem["last_run_output"] = _clip(output, _MAX_OUTPUT)
    mem["last_run_error"] = _clip(error, _MAX_ERROR)
    error_text = str(error or "")
    if error_text:
        counts = mem.setdefault("error_type_counts", {})
        kind = next((name for name in (
            "IndentationError", "SyntaxError", "NameError", "TypeError", "ImportError"
        ) if name in error_text), "")
        if not kind and "blocked" in error_text.lower() and "import" in error_text.lower():
            kind = "BlockedImport"
        if kind:
            counts[kind] = max(0, int(counts.get(kind) or 0)) + 1
    if ran_ok is not None:
        mem["last_run_ok"] = bool(ran_ok)
    if inputs is not None:
        mem["last_run_inputs"] = [_clip(v, 200) for v in inputs][:_MAX_VALUES]
        if inputs:
            mem["last_input_run_summary"] = {
                "used_input": True,
                "count": len(inputs[:_MAX_VALUES]),
                "source": _clip(input_source, 40),
                "values": [_clip(v, 200) for v in inputs][:_MAX_VALUES],
            }
        elif input_source:
            mem["last_input_run_summary"] = {
                "used_input": False,
                "count": 0,
                "source": _clip(input_source, 40),
                "values": [],
            }


def clear_run_state(mem: Dict[str, Any]) -> None:
    mem["last_run_output"] = ""
    mem["last_run_error"] = ""
    mem["last_run_traceback"] = ""
    mem["last_run_ok"] = None
    mem["last_run_inputs"] = []
    mem["run_count"] = 0


def record_input_values(mem: Dict[str, Any], values: List[str],
                        prompts: Optional[List[str]] = None) -> None:
    if values:
        mem["input_values"] = [_clip(v, 200) for v in values][:_MAX_VALUES]
    if prompts:
        mem["input_prompts"] = [_clip(p, 120) for p in prompts][:_MAX_VALUES]


def set_pending_stdin_values(mem: Dict[str, Any], values: List[str]) -> List[str]:
    cleaned = [_clip(v, 200) for v in (values or []) if str(v).strip()][:_MAX_VALUES]
    mem["pending_stdin_values"] = cleaned
    if cleaned:
        mem["input_values"] = list(cleaned)
    return cleaned


def get_pending_stdin_values(mem: Dict[str, Any]) -> List[str]:
    values = mem.get("pending_stdin_values")
    return [_clip(v, 200) for v in values][:_MAX_VALUES] if isinstance(values, list) else []


def clear_pending_stdin_values(mem: Dict[str, Any]) -> None:
    mem["pending_stdin_values"] = []
    mem["input_values"] = []


def set_awaiting_program_input(
    mem: Dict[str, Any], *, code_hash: str, prompts: List[Dict[str, Any]],
    values: Optional[List[str]] = None, index: int = 0
) -> None:
    mem["awaiting_program_input"] = True
    mem["awaiting_input_code_hash"] = _clip(code_hash, 80)
    mem["awaiting_input_prompts"] = list(prompts or [])[:_MAX_VALUES]
    mem["awaiting_input_values"] = [_clip(v, 200) for v in (values or [])][:_MAX_VALUES]
    mem["awaiting_input_index"] = max(0, int(index or 0))


def get_awaiting_program_input(mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not mem.get("awaiting_program_input"):
        return None
    prompts = mem.get("awaiting_input_prompts")
    values = mem.get("awaiting_input_values")
    return {
        "code_hash": _clip(mem.get("awaiting_input_code_hash", ""), 80),
        "prompts": prompts if isinstance(prompts, list) else [],
        "values": values if isinstance(values, list) else [],
        "index": max(0, int(mem.get("awaiting_input_index") or 0)),
    }


def clear_awaiting_program_input(mem: Dict[str, Any]) -> None:
    mem["awaiting_program_input"] = False
    mem["awaiting_input_code_hash"] = ""
    mem["awaiting_input_prompts"] = []
    mem["awaiting_input_values"] = []
    mem["awaiting_input_index"] = 0


def record_file_open(mem: Dict[str, Any], path: str) -> None:
    path = _clip(path, 200)
    if path:
        mem["last_opened_file"] = path
        mem["last_active_file"] = path


def record_active_file(mem: Dict[str, Any], path: str) -> None:
    path = _clip(path, 200)
    if path:
        mem["last_active_file"] = path


def record_project_files(mem: Dict[str, Any], files: List[str]) -> None:
    if files:
        mem["project_files"] = [_clip(f, 200) for f in files][:_MAX_FILES]


def record_project_manifest(mem: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        return
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    if files:
        record_project_files(mem, [str(f) for f in files])
    active = _clip(manifest.get("active_file") or manifest.get("entry") or "", 200)
    if active:
        mem["last_active_file"] = active
    mem["last_project_manifest"] = {
        "name": _clip(manifest.get("name"), 120),
        "entry": _clip(manifest.get("entry"), 200),
        "active_file": active,
        "requirements": [_clip(v, 80) for v in (manifest.get("requirements") or [])[:20]]
        if isinstance(manifest.get("requirements"), list) else [],
        "file_count": len(files),
    }


def record_edit_request(mem: Dict[str, Any], instruction: str) -> None:
    mem["last_edit_request"] = _clip(instruction, _MAX_PROMPT)
    mem["student_satisfied"] = False


def record_code_edit(mem: Dict[str, Any], *, instruction: str = "", old_code: str = "",
                     new_code: str = "", summary: str = "") -> None:
    if instruction:
        mem["last_edit_request"] = _clip(instruction, _MAX_PROMPT)
        mem["last_code_edit_command"] = _clip(instruction, _MAX_PROMPT)
    old_bounded = _clip(old_code, _MAX_CODE)
    new_bounded = _clip(new_code, _MAX_CODE)
    mem["last_edit_old_code"] = old_bounded
    mem["last_edit_new_code"] = new_bounded
    old_hash = code_hash(old_bounded)
    if new_bounded and old_hash and old_hash == mem.get("last_generated_code_hash"):
        mem["last_generated_code"] = new_bounded
        mem["last_generated_code_hash"] = code_hash(new_bounded)
        mem["current_editor_code_hash"] = mem["last_generated_code_hash"]
    elif new_bounded:
        mem["current_editor_code_hash"] = code_hash(new_bounded)
    mem["last_edit_summary"] = _clip(summary, 300)
    mem["student_satisfied"] = False


def clear_edit_memory(mem: Dict[str, Any]) -> None:
    for key in (
        "last_gen_prompt", "last_gen_summary", "last_generated_code",
        "last_generated_goal", "last_generated_code_hash",
        "last_code_generation_command", "last_code_edit_command",
        "current_editor_code_hash",
        "last_project_manifest", "last_edit_request", "last_edit_summary",
        "last_edit_old_code", "last_edit_new_code", "last_fix_explanation",
        "project_files", "last_active_file", "last_opened_file",
    ):
        mem[key] = [] if key == "project_files" else ({} if key == "last_project_manifest" else "")
    mem["student_satisfied"] = None
    mem["last_code_generation_time"] = 0.0


def record_tutorial(mem: Dict[str, Any], module: str) -> None:
    module = _clip(module, 40)
    if module:
        mem["tutorial_module"] = module


def set_pending(mem: Dict[str, Any], pending: Optional[Dict[str, Any]]) -> None:
    if not pending:
        mem["pending_clarification"] = None
        return
    record = dict(pending)
    record["timestamp"] = time.time()
    mem["pending_clarification"] = record


def get_pending(mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pending = mem.get("pending_clarification")
    if not isinstance(pending, dict):
        return None
    if time.time() - float(pending.get("timestamp", 0) or 0) > _PENDING_TTL_SECONDS:
        mem["pending_clarification"] = None
        return None
    return pending


def clear_pending(mem: Dict[str, Any]) -> None:
    mem["pending_clarification"] = None


_MAX_CHANGE_HISTORY = 10


def record_change(mem: Dict[str, Any], *, before: str = "", after: str = "",
                  file_name: str = "", reason: str = "") -> Dict[str, Any]:
    """Record an applied before/after code change for Audio Diff Review."""
    record = {
        "before": _clip(before, _MAX_CODE),
        "after": _clip(after, _MAX_CODE),
        "file": _clip(file_name, 200),
        "reason": _clip(reason, 200),
        "timestamp": time.time(),
    }
    history = mem.setdefault("change_history", [])
    history.append(record)
    del history[:-_MAX_CHANGE_HISTORY]
    undo = mem.setdefault("undo_stack", [])
    undo.append(record)
    del undo[:-_MAX_CHANGE_HISTORY]
    mem["last_change"] = record
    mem["change_cursor"] = len(history) - 1
    return record


def get_last_change(mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return mem.get("last_change")


def get_change_history(mem: Dict[str, Any]) -> List[Dict[str, Any]]:
    history = mem.get("change_history")
    return history if isinstance(history, list) else []


def get_change_at_cursor(mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    history = get_change_history(mem)
    if not history:
        return None
    cursor = int(mem.get("change_cursor", len(history) - 1) or 0)
    cursor = max(0, min(cursor, len(history) - 1))
    mem["change_cursor"] = cursor
    return history[cursor]


def move_change_cursor(mem: Dict[str, Any], delta: int) -> Optional[Dict[str, Any]]:
    history = get_change_history(mem)
    if not history:
        return None
    cursor = int(mem.get("change_cursor", len(history) - 1) or 0) + delta
    cursor = max(0, min(cursor, len(history) - 1))
    mem["change_cursor"] = cursor
    return history[cursor]


def undo_last_change(mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pop the most recent change. Caller restores the returned record's 'before'."""
    undo = mem.get("undo_stack")
    if not isinstance(undo, list) or not undo:
        return None
    record = undo.pop()
    history = mem.get("change_history")
    if isinstance(history, list) and history:
        history.pop()
    remaining = mem.get("change_history") or []
    mem["last_change"] = remaining[-1] if remaining else None
    mem["change_cursor"] = (len(remaining) - 1) if remaining else 0
    return record


def set_change_proposal(mem: Dict[str, Any], proposal: Optional[Dict[str, Any]]) -> None:
    if not proposal:
        mem["pending_change_proposal"] = None
        return
    record = dict(proposal)
    record["timestamp"] = time.time()
    mem["pending_change_proposal"] = record


def get_change_proposal(mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    proposal = mem.get("pending_change_proposal")
    if not isinstance(proposal, dict):
        return None
    if time.time() - float(proposal.get("timestamp", 0) or 0) > _PENDING_TTL_SECONDS:
        mem["pending_change_proposal"] = None
        return None
    return proposal


def clear_change_proposal(mem: Dict[str, Any]) -> None:
    mem["pending_change_proposal"] = None


_MAX_WATCHED = 20


def add_watched_variable(mem: Dict[str, Any], name: str) -> List[str]:
    name = _clip(name, 60).strip()
    watched = mem.setdefault("watched_variables", [])
    if name and name not in watched:
        watched.append(name)
        del watched[:-_MAX_WATCHED]
    return list(watched)


def remove_watched_variable(mem: Dict[str, Any], name: str) -> List[str]:
    name = (name or "").strip()
    watched = mem.setdefault("watched_variables", [])
    if name in watched:
        watched.remove(name)
    return list(watched)


def get_watched_variables(mem: Dict[str, Any]) -> List[str]:
    watched = mem.get("watched_variables")
    return list(watched) if isinstance(watched, list) else []


def set_state_trace(mem: Dict[str, Any], bundle: Optional[Dict[str, Any]]) -> None:
    """Store a compact state-trace bundle (steps/vars/conditions, not raw trace)."""
    if not bundle:
        mem["last_state_trace"] = None
        return
    record = dict(bundle)
    record.setdefault("cursor", 0)
    record["timestamp"] = time.time()
    mem["last_state_trace"] = record


def get_state_trace(mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    bundle = mem.get("last_state_trace")
    return bundle if isinstance(bundle, dict) else None


def state_trace_is_stale(mem: Dict[str, Any], current_code: str) -> bool:
    bundle = get_state_trace(mem)
    if not bundle:
        return False
    return (current_code or "") != (bundle.get("code") or "")


def snapshot(mem: Dict[str, Any], *, utterance: str = "", file_name: str = "") -> Dict[str, Any]:
    return {
        "utterance": _clip(utterance, 200),
        "last_intent": mem.get("last_intent", ""),
        "last_action": mem.get("last_action", ""),
        "last_run_ok": mem.get("last_run_ok"),
        "last_error": _clip(mem.get("last_run_error", ""), 200),
        "last_output": _clip(mem.get("last_run_output", ""), 200),
        "last_gen_prompt": _clip(mem.get("last_gen_prompt", ""), 200),
        "last_gen_summary": _clip(mem.get("last_gen_summary", ""), 200),
        "last_edit_request": _clip(mem.get("last_edit_request", ""), 200),
        "last_edit_summary": _clip(mem.get("last_edit_summary", ""), 200),
        "current_file": file_name or mem.get("last_active_file", ""),
        "tutorial_module": mem.get("tutorial_module", ""),
        "project_files": (mem.get("project_files") or [])[:20],
    }


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def _match(t: str, phrases: List[str]) -> bool:
    for p in phrases:
        if t == p or t.startswith(p + " "):
            return True
    return False


_OPEN_FILE_AGAIN = ["open that file again", "open this file again", "open that again",
                    "open it again", "open the file again", "reopen that file",
                    "reopen the file", "read that file", "read this file", "read that file again"]
_RUN_FILE_AGAIN = ["run that file again", "run this file again", "run that file",
                   "run the file again", "run that one again"]
_EXPLAIN_PROJECT = ["explain this project again", "explain the project again",
                    "explain this project", "explain the project", "explain the whole project"]
_RUN_SAME_INPUTS = ["use the same inputs", "use the same values", "run with the same values",
                    "run with the same inputs", "same inputs", "same values",
                    "with the same inputs", "with the same values", "reuse the inputs",
                    "reuse the same inputs"]
_RUN_AGAIN = ["run it again", "run that again", "run again", "run this again",
              "rerun it", "rerun that", "rerun", "execute it again", "run the code again"]
_WHY_FAILED = ["why did it fail", "why did that fail", "why did this fail", "what went wrong",
               "why didn't it work", "why did it not work", "why did that not work",
               "what failed"]
_FIX_ERROR = ["fix that", "fix the error", "fix it", "try fixing it", "try to fix it",
              "fix this error", "fix that error", "can you fix it", "please fix it"]
_WHAT_HAPPENED = ["what just happened", "what did that do", "what happened", "what did it do",
                  "what happened just now"]
_EXPLAIN_AGAIN = ["explain it again", "explain that again", "explain again",
                  "explain this again", "explain the code again"]
_EXPLAIN_SIMPLER = ["explain simpler", "say that simpler", "explain it simpler",
                    "explain that simpler", "say it simpler", "in simpler terms",
                    "simpler explanation"]
_MODIFY = ["do the same", "now make it", "now change it", "now edit it",
           "add comments", "add a comment", "add some comments",
           "add scoring", "add score", "add scores", "add input", "add levels",
           "add average", "add this", "add that", "add it", "replace this",
           "replace that", "replace it", "do this also", "do this", "do it also",
           "do that also", "make it easier", "make it shorter", "make it longer",
           "make it simpler", "make it cleaner", "make it speak", "make it ask",
           "make it with",
           "make it use", "make it print", "make it return", "make it loop",
           "make it count", "make it a", "make it an", "remove the hard part",
           "change the name to", "change it to", "change it into", "rename it to",
           "change the variable", "do that with", "do it with"]
_SUMMARIZE = ["summarize what i did", "summarise what i did", "summarize my work",
              "summarise my work", "what did i do", "what have i done", "recap my session"]

_CORRECTION_LEADIN_RE = re.compile(
    r"^(?:oh[,\s]+)?i\s+(?:meant|mean)\b[:,]?\s*"
    r"|^oh[,\s]+(?=make|use|change|print|turn|add|rename|it\b)"
    r"|^no[,]?\s+(?:i\s+meant\s+)?(?=make|use|change|print|turn|add|rename|it\b)"
    r"|^actually[,]?\s+"
    r"|^arey[,]?\s+(?=add|make|change|replace|do|remove|use|turn|edit)\b"
    r"|^(?:wait|sorry)[,]?\s+(?:i\s+meant\s+)?"
    r"|^modify\s+it\s+so(?:\s+that)?\s+"
    r"|^change\s+it\s+(?:to|so)\s+",
    re.IGNORECASE,
)
_MODIFY_HINT_RE = re.compile(
    r"\b(make\s+it|use\b|change\b|rename\b|print\b|turn\s+it\s+into|add\s+(?:a\s+)?comment|"
    r"comments?\b|scoring\b|scores?\b|input\b|levels?\b|average\b|pandas\b|a\s+function|"
    r"a\s+loop|a\s+while|while\s+loop|odd\b|even\b|instead\b|variable\b|return\b|"
    r"shorter\b|longer\b|simpler\b|easier\b|the\s+output)\b",
    re.IGNORECASE,
)
_VAGUE_INSTRUCTION_RE = re.compile(
    r"^(?:change|make|use|fix|update|modify|do|edit|replace)\s+(?:it|that|this|the\s+code|something)?(?:\s+also)?$"
    r"|^add\s+(?:it|that|this|something)(?:\s+also)?$"
    r"|^(?:that|this|it|the\s+other\s+one|that\s+one|the\s+same|something\s+else)$",
    re.IGNORECASE,
)


def detect_correction(text: str) -> Optional[str]:
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    low = raw.lower()
    if re.search(r"\binstead\b", low):
        return raw
    match = _CORRECTION_LEADIN_RE.match(raw)
    if not match:
        return None
    remainder = raw[match.end():].strip(" ,.")
    if not remainder:
        return None
    leadin = match.group(0).lower()
    if leadin.startswith(("actually", "no")) and not _MODIFY_HINT_RE.search(remainder):
        return None
    return remainder


def instruction_is_vague(instruction: str) -> bool:
    return bool(_VAGUE_INSTRUCTION_RE.match(_norm(instruction)))


def classify_followup(text: str) -> Optional[str]:
    t = _norm(text)
    if not t:
        return None
    if _match(t, _OPEN_FILE_AGAIN):
        return "open_file_again"
    if _match(t, _RUN_FILE_AGAIN):
        return "run_file_again"
    if _match(t, _EXPLAIN_PROJECT):
        return "explain_project"
    if _match(t, _RUN_SAME_INPUTS):
        return "run_same_inputs"
    if _match(t, _RUN_AGAIN):
        return "run_again"
    if _match(t, _WHY_FAILED):
        return "why_failed"
    if _match(t, _FIX_ERROR):
        return "fix_error"
    if _match(t, _WHAT_HAPPENED):
        return "what_happened"
    if _match(t, _EXPLAIN_AGAIN):
        return "explain_again"
    if _match(t, _EXPLAIN_SIMPLER):
        return "explain_simpler"
    if _match(t, _MODIFY):
        return "modify"
    if _match(t, _SUMMARIZE):
        return "summarize_session"
    if detect_correction(text):
        return "modify"
    return None


def _clarify(message: str, referent: str = "unknown", confidence: float = 0.4) -> Dict[str, Any]:
    return {"handled": False, "resolved_action": "", "referent": referent,
            "confidence": confidence, "clarification": message, "params": {}}


def _act(action: str, referent: str, params: Optional[Dict[str, Any]] = None,
         confidence: float = 0.9) -> Dict[str, Any]:
    return {"handled": True, "resolved_action": action, "referent": referent,
            "confidence": confidence, "clarification": "", "params": params or {}}


def build_modify_prompt(text: str, referent_prompt: str, has_code: bool) -> str:
    instruction = _norm(text)
    parts: List[str] = []
    if referent_prompt:
        parts.append(f"Earlier you generated code for: {referent_prompt}.")
    elif has_code:
        parts.append("Using the current program in the editor,")
    parts.append(f"now {instruction}.")
    parts.append("Keep it beginner-friendly, well-commented, and runnable without input().")
    if re.search(r"\b(?:pandas|numpy|data\s*frame)\b", instruction, re.IGNORECASE):
        parts.append("Use pandas or numpy only if the learner explicitly asked and the example stays small and teachable.")
    return " ".join(parts).strip()


def resolve_followup(category: str, text: str, mem: Dict[str, Any], *,
                     code: str = "", error: str = "") -> Dict[str, Any]:
    code = (code or "").strip()
    recent_error = (error or "").strip() or _clip(mem.get("last_run_error", ""), _MAX_ERROR)
    has_gen = bool(mem.get("last_gen_prompt"))
    stored_inputs = mem.get("input_values") or mem.get("last_run_inputs") or []

    if category == "explain_again":
        if code or has_gen:
            return _act("explain_code", "last_code")
        if mem.get("last_run_output") or recent_error:
            return _act("explain_run", "last_run", confidence=0.85)
        return _clarify("I'm not sure what to explain again. Generate or run something first, "
                        "or tell me what to explain.")

    if category == "explain_simpler":
        if code or has_gen:
            return _act("explain_simpler", "last_code", confidence=0.85)
        return _clarify("What would you like me to explain more simply?")

    if category == "what_happened":
        if recent_error:
            return _act("explain_error", "last_error", {"error": recent_error}, confidence=0.85)
        if mem.get("last_run_output"):
            return _act("describe_run", "last_run", {"output": mem["last_run_output"]}, confidence=0.85)
        if code:
            return _act("explain_code", "last_code", confidence=0.7)
        return _clarify("I don't have a recent action to describe yet.")

    if category == "why_failed":
        if recent_error:
            return _act("explain_error", "last_error", {"error": recent_error})
        return _clarify("There is no recent error to explain. Run your code, "
                        "or tell me what to inspect.", referent="no_error")

    if category == "fix_error":
        if recent_error:
            return _act("fix_error", "last_error", {"error": recent_error})
        return _clarify("There is no recent error to fix. What would you like me to look at?",
                        referent="no_error")

    if category == "run_again":
        if stored_inputs:
            return _act("run_same_inputs", "last_run", {"inputs": list(stored_inputs)})
        if code or mem.get("last_run_ok") is not None or has_gen:
            return _act("run_again", "last_run")
        return _clarify("There is no code to run yet. Generate or insert some code first.",
                        referent="no_code")

    if category == "run_same_inputs":
        if stored_inputs:
            return _act("run_same_inputs", "last_inputs", {"inputs": list(stored_inputs)})
        return _clarify("I don't have saved input values yet. Say, for example: "
                        "run with name Taknoor and age 16.", referent="no_inputs")

    if category == "modify":
        instruction = detect_correction(text) or text
        if instruction_is_vague(instruction):
            return _clarify("What change would you like? For example, say: make it use ten, "
                            "use a while loop, or rename the variable to total.")
        referent_prompt = _clip(mem.get("last_gen_prompt", ""), _MAX_PROMPT)
        if not referent_prompt and not code:
            return _clarify("I'm not sure what to change. Generate some code first, "
                            "then tell me how to change it.")
        record_edit_request(mem, instruction)
        prompt = build_modify_prompt(instruction, referent_prompt, has_code=bool(code))
        return _act("modify_code", "last_code" if code else "last_prompt",
                    {"prompt": prompt, "instruction": _norm(instruction), "referent_prompt": referent_prompt},
                    confidence=0.8)

    if category == "open_file_again":
        path = mem.get("last_opened_file") or mem.get("last_active_file") or ""
        if path:
            return _act("open_file", "last_file", {"path": path})
        return _clarify("I don't have a recent file to open. Say the file name, for example: open main.",
                        referent="no_file")

    if category == "run_file_again":
        path = mem.get("last_active_file") or mem.get("last_opened_file") or ""
        if path:
            return _act("run_file", "last_file", {"path": path})
        return _clarify("I don't have a recent file to run. Say the file name, for example: run main.",
                        referent="no_file")

    if category == "explain_project":
        if mem.get("project_files"):
            return _act("explain_project", "project", confidence=0.85)
        return _clarify("There is no project open yet. Say: read project files.", referent="no_project")

    if category == "summarize_session":
        return _act("summarize_session", "session", {"summary": session_summary(mem)})

    return _clarify("Could you say that another way?")


def session_summary(mem: Dict[str, Any]) -> str:
    bits: List[str] = []
    if mem.get("last_gen_prompt"):
        bits.append(f"generated code for {mem['last_gen_prompt']}")
    if mem.get("last_run_ok") is True:
        bits.append("ran it successfully")
    elif mem.get("last_run_ok") is False:
        bits.append("hit an error when running it")
    if mem.get("last_opened_file"):
        bits.append(f"opened {mem['last_opened_file']}")
    if mem.get("tutorial_module"):
        bits.append(f"practised the {mem['tutorial_module']} tutorial")
    if not bits:
        return "We have not done much yet this session. Try generating or running some code."
    return "So far you " + ", then ".join(bits) + "."
