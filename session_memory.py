"""Per-session short-term working memory + contextual follow-up resolution.

CodeUp is voice-first: a blind beginner should be able to say "explain it
again", "why did it fail", "run it again", or "do the same with 10" without
restating everything. This module keeps a small, bounded, per-session memory of
the immediate conversation context and resolves those follow-up commands to the
app's existing real actions.

Design notes:
  * Pure/deterministic and Flask-free, so it is easy to unit test. The app wires
    it into the per-session ``get_trace_storage()`` dict (no database).
  * Bounded: every stored string is clipped and lists are capped, so memory can
    never grow without limit. We store summaries/references, never giant code
    bodies, and never secrets/keys (the app only ever passes it task text).
  * Deterministic resolution is the source of truth. Key 2 is optional and only
    used by the caller to refine an ambiguous referent ("it"/"that"/"same"); it
    can never invent an error, output, or file that memory does not hold.
"""

import re
import time
from typing import Any, Dict, List, Optional

MEMORY_KEY = "session_memory"
_PENDING_TTL_SECONDS = 180

_MAX_TEXT = 400
_MAX_OUTPUT = 800
_MAX_ERROR = 600
_MAX_PROMPT = 600
_MAX_FILES = 50
_MAX_VALUES = 50


def _clip(value: Any, limit: int = _MAX_TEXT) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def new_memory() -> Dict[str, Any]:
    """The shape of per-session working memory (all bounded)."""
    return {
        "last_utterance": "",
        "last_intent": "",
        "last_action": "",
        "last_actions": [],          # last action-sequence (list of action names)
        "last_gen_prompt": "",       # last generation request (prompt text)
        "last_gen_summary": "",      # short summary of last generated code (not the body)
        "last_run_output": "",
        "last_run_error": "",
        "last_run_ok": None,
        "last_run_inputs": [],
        "last_explain_target": "",
        "last_active_file": "",
        "last_opened_file": "",
        "project_files": [],
        "tutorial_module": "",
        "input_values": [],
        "input_prompts": [],
        "code_map_summary": "",
        "features_used": [],          # human-friendly feature tags used this session
        "concepts_practiced": [],     # concepts the learner touched (for the recap)
        "pending_clarification": None,
    }


# Map a /voice-command action to a human-friendly "feature used" tag for the
# session recap. Actions not listed (navigation, confirmations, control) are not
# counted as a learning feature.
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
    """Record a learning feature used and/or a concept practised (bounded, unique)."""
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


def get_memory(storage: Dict[str, Any]) -> Dict[str, Any]:
    """Return (creating if needed) the memory dict inside a session storage."""
    mem = storage.get(MEMORY_KEY)
    if not isinstance(mem, dict):
        mem = new_memory()
        storage[MEMORY_KEY] = mem
    return mem


# ---------------------------------------------------------------------------
# Recording (called after key events)
# ---------------------------------------------------------------------------
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
        mem["last_gen_prompt"] = _clip(prompt, _MAX_PROMPT)
    if code:
        lines = str(code).strip().splitlines()
        first = lines[0].strip() if lines else ""
        mem["last_gen_summary"] = _clip(f"{len(lines)} lines, starts with: {first}", 200)


def record_run(mem: Dict[str, Any], *, output: str = "", error: str = "",
               inputs: Optional[List[str]] = None, ran_ok: Optional[bool] = None) -> None:
    mem["last_run_output"] = _clip(output, _MAX_OUTPUT)
    mem["last_run_error"] = _clip(error, _MAX_ERROR)
    if ran_ok is not None:
        mem["last_run_ok"] = bool(ran_ok)
    if inputs is not None:
        mem["last_run_inputs"] = [_clip(v, 200) for v in inputs][:_MAX_VALUES]


def record_input_values(mem: Dict[str, Any], values: List[str],
                        prompts: Optional[List[str]] = None) -> None:
    if values:
        mem["input_values"] = [_clip(v, 200) for v in values][:_MAX_VALUES]
    if prompts:
        mem["input_prompts"] = [_clip(p, 120) for p in prompts][:_MAX_VALUES]


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


def record_tutorial(mem: Dict[str, Any], module: str) -> None:
    module = _clip(module, 40)
    if module:
        mem["tutorial_module"] = module


def record_code_map(mem: Dict[str, Any], summary: str) -> None:
    mem["code_map_summary"] = _clip(summary, 400)


# ---------------------------------------------------------------------------
# Pending clarification (a real question CodeUp asked and must understand)
# ---------------------------------------------------------------------------
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


def snapshot(mem: Dict[str, Any], *, utterance: str = "", file_name: str = "") -> Dict[str, Any]:
    """A small, bounded context bundle for Key 2 referent resolution."""
    return {
        "utterance": _clip(utterance, 200),
        "last_intent": mem.get("last_intent", ""),
        "last_action": mem.get("last_action", ""),
        "last_run_ok": mem.get("last_run_ok"),
        "last_error": _clip(mem.get("last_run_error", ""), 200),
        "last_output": _clip(mem.get("last_run_output", ""), 200),
        "last_gen_prompt": _clip(mem.get("last_gen_prompt", ""), 200),
        "current_file": file_name or mem.get("last_active_file", ""),
        "tutorial_module": mem.get("tutorial_module", ""),
        "project_files": (mem.get("project_files") or [])[:20],
    }


# ---------------------------------------------------------------------------
# Follow-up classification
# ---------------------------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def _match(t: str, phrases: List[str]) -> bool:
    # Whole-utterance match (equal, or the phrase plus trailing detail). This
    # deliberately avoids substring matching so "ask mentor why did this fail"
    # is NOT mistaken for the bare "why did this fail" follow-up.
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
_MODIFY = ["do the same", "add comments", "add a comment", "add some comments",
           "make it shorter", "make it longer", "make it simpler", "make it cleaner",
           "make it use", "make it print", "make it return", "make it loop", "make it count",
           "make it a", "make it an", "change the name to", "change it to", "rename it to",
           "change the variable", "do that with", "do it with"]
_SUMMARIZE = ["summarize what i did", "summarise what i did", "summarize my work",
              "summarise my work", "what did i do", "what have i done", "recap my session"]

# Correction / "oh I meant ..." follow-ups: after generating or editing code the
# learner refines it in natural speech. We strip the correction lead-in and treat
# the remainder as a modification instruction (resolved against the last
# generation / current editor code). Kept tight so a plain command after a filler
# (e.g. "actually run it") is NOT hijacked: a leading "actually"/"no" only counts
# as a correction when the remainder is itself a modify-style instruction.
_CORRECTION_LEADIN_RE = re.compile(
    r"^(?:oh[,\s]+)?i\s+(?:meant|mean)\b[:,]?\s*"
    r"|^oh[,\s]+(?=make|use|change|print|turn|add|rename|it\b)"
    r"|^no[,]?\s+(?:i\s+meant\s+)?(?=make|use|change|print|turn|add|rename|it\b)"
    r"|^actually[,]?\s+"
    r"|^(?:wait|sorry)[,]?\s+(?:i\s+meant\s+)?"
    r"|^modify\s+it\s+so(?:\s+that)?\s+"
    r"|^change\s+it\s+(?:to|so)\s+",
    re.IGNORECASE,
)
_MODIFY_HINT_RE = re.compile(
    r"\b(make\s+it|use\b|change\b|rename\b|print\b|turn\s+it\s+into|add\s+(?:a\s+)?comment|"
    r"comments?\b|a\s+function|a\s+loop|a\s+while|odd\b|even\b|instead\b|variable\b|return\b|"
    r"shorter\b|longer\b|simpler\b|the\s+output)\b",
    re.IGNORECASE,
)
# An instruction with no actionable target ("change that", "make it", a bare
# pronoun) is ambiguous: the caller asks one clarifying question instead.
_VAGUE_INSTRUCTION_RE = re.compile(
    r"^(?:change|make|use|fix|update|modify|do|edit)\s+(?:it|that|this|the\s+code|something)?$"
    r"|^(?:that|this|it|the\s+other\s+one|that\s+one|the\s+same|something\s+else)$",
    re.IGNORECASE,
)


def detect_correction(text: str) -> Optional[str]:
    """If ``text`` is a natural correction of just-generated code, return the
    modification instruction (the part after the lead-in); otherwise None.

    Examples:
      "oh I meant make it first ten even numbers" -> "make it first ten even numbers"
      "actually use a while loop instead"         -> "actually use a while loop instead"
      "actually run it"                           -> None (plain command, not an edit)
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    low = raw.lower()
    # Trailing/embedded "instead" is a strong correction signal; keep the whole
    # utterance as the instruction so "use a while loop instead" stays intact.
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
    """True when a modification instruction has no actionable target."""
    return bool(_VAGUE_INSTRUCTION_RE.match(_norm(instruction)))


def classify_followup(text: str) -> Optional[str]:
    """Classify a whole utterance into a follow-up category, or None.

    Specific multi-file/run forms are checked before generic explain/run so a
    "run that file again" is not read as a bare "run again".
    """
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
    # "oh I meant ...", "actually use ... instead": a correction of the code we
    # just produced. Checked last so specific follow-ups above still win.
    if detect_correction(text):
        return "modify"
    return None


# ---------------------------------------------------------------------------
# Follow-up resolution (deterministic; Key 2 refinement is the caller's job)
# ---------------------------------------------------------------------------
def _clarify(message: str, referent: str = "unknown", confidence: float = 0.4) -> Dict[str, Any]:
    return {"handled": False, "resolved_action": "", "referent": referent,
            "confidence": confidence, "clarification": message, "params": {}}


def _act(action: str, referent: str, params: Optional[Dict[str, Any]] = None,
         confidence: float = 0.9) -> Dict[str, Any]:
    return {"handled": True, "resolved_action": action, "referent": referent,
            "confidence": confidence, "clarification": "", "params": params or {}}


def build_modify_prompt(text: str, referent_prompt: str, has_code: bool) -> str:
    """Ground a modification request in the previous generation / current code."""
    instruction = _norm(text)
    parts: List[str] = []
    if referent_prompt:
        parts.append(f"Earlier you generated code for: {referent_prompt}.")
    elif has_code:
        parts.append("Using the current program in the editor,")
    parts.append(f"now {instruction}.")
    parts.append("Keep it beginner-friendly, well-commented, and runnable without input().")
    return " ".join(parts).strip()


def resolve_followup(category: str, text: str, mem: Dict[str, Any], *,
                     code: str = "", error: str = "") -> Dict[str, Any]:
    """Resolve a classified follow-up against memory into an action decision.

    Returns a decision dict: {handled, resolved_action, referent, confidence,
    clarification, params}. ``handled`` is False when we must ask one short
    clarification (the referent context is missing).
    """
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
        # For a natural correction ("oh I meant ...") use the stripped instruction.
        instruction = detect_correction(text) or text
        if instruction_is_vague(instruction):
            return _clarify("What change would you like? For example, say: make it use ten, "
                            "use a while loop, or rename the variable to total.")
        referent_prompt = _clip(mem.get("last_gen_prompt", ""), _MAX_PROMPT)
        if not referent_prompt and not code:
            return _clarify("I'm not sure what to change. Generate some code first, "
                            "then tell me how to change it.")
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
    """A short, fact-grounded recap built only from stored memory."""
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
