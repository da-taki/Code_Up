
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Optional, Tuple

from codeup.integrations import groq_key_manager


ALLOWED_INTENTS = {
    "help_short",
    "help_more",
    "start_tutorial",
    "insert_print_statement",
    "insert_variable_example",
    "insert_input_example",
    "insert_if_statement",
    "insert_for_loop",
    "insert_while_loop",
    "insert_list_example",
    "insert_function_example",
    "add_comments",
    "simplify_current_code",
    "convert_loop_type",
    "insert_beginner_loop",
    "run_code",
    "read_output",
    "analyze_code",
    "explain_current_code",
    "fix_code",
    "debug_like_teacher",
    "replay_mistake",
    "summarize_structure",
    "generate_beginner_program",
    "edit_current_code",
    "edit_previous_program",
    "export_project",
    "stop_everything",
    "unknown_clarify",
}

HIGH_CONFIDENCE_THRESHOLD = 0.78
MEDIUM_CONFIDENCE_THRESHOLD = 0.55

MAPPER_FALLBACK_CLARIFICATION = (
    "I heard a programming command, but not clearly. Try saying: insert a loop, "
    "run, explain this code, or start tutorial."
)

_TOP_LEVEL_KEYS = {"intent", "confidence", "slots", "reason"}
_BLOCKED_KEYS = {
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "code",
    "python",
    "script",
    "shell",
    "command",
    "command_to_run",
    "executable",
}
_LEGACY_LOOP_SLOT_KEYS = {"start", "stop", "output"}
_INTENT_SLOT_KEYS = {
    "insert_beginner_loop": _LEGACY_LOOP_SLOT_KEYS,
    "insert_print_statement": {"text", "value"},
    "insert_variable_example": {"kind", "name", "value"},
    "insert_input_example": {"kind", "name"},
    "insert_if_statement": {"kind", "variable", "threshold"},
    "insert_for_loop": {
        "start", "stop", "step", "kind", "output", "variable", "collection",
        "count", "direction",
    },
    "insert_while_loop": {
        "start", "stop", "step", "kind", "output", "variable", "count",
        "direction",
    },
    "insert_list_example": {"kind", "collection", "loop"},
    "insert_function_example": {"kind", "name"},
    "add_comments": {"target"},
    "simplify_current_code": {"target"},
    "convert_loop_type": {"target", "to", "from"},
    "generate_beginner_program": {"kind", "topic", "prompt"},
    "edit_current_code": {
        "edit_type", "target", "to", "from", "new_value", "old_value",
        "row", "line", "condition", "variable", "value", "kind", "prompt",
    },
    "edit_previous_program": {
        "edit_type", "target", "to", "from", "new_value", "old_value",
        "row", "line", "condition", "variable", "value", "kind", "prompt",
    },
}
_NUMERIC_SLOT_KEYS = {"start", "stop", "step", "count", "threshold", "new_value", "old_value", "row", "line"}
_BOOL_SLOT_KEYS = {"loop"}
_SAFE_SLOT_TEXT_RE = re.compile(r"^[A-Za-z0-9 _.,:-]{0,80}$")


def mapper_messages(
    command_text: str,
    *,
    has_code: bool = False,
    normalized_text: str = "",
    has_recent_generated: bool = False,
    current_mode: str = "",
    memory_summary: str = "",
) -> Tuple[str, str]:

    allowed = ", ".join(sorted(ALLOWED_INTENTS))
    system = (
        "You are not writing code. You are mapping a student's natural-language "
        "CodeUp command to one known intent. Return JSON only. Use only these "
        f"allowed intents: {allowed}. Prefer unknown_clarify if unsure. Never "
        "invent new commands. Never include API keys or internal details. Never "
        "put code in the response. For natural follow-up edits that refer to the "
        "current program with words like it, this, the loop, the output, the row, "
        "or instead, prefer edit_current_code when editor code is available and "
        "edit_previous_program when the user is referring to a recent generated "
        "program. Slots may describe only small template choices or edit facts "
        "such as text, kind, target, start, stop, step, row, old_value, new_value, "
        "or to. Schema: "
        "{\"intent\":\"insert_for_loop\",\"confidence\":0.0,"
        "\"slots\":{\"start\":1,\"stop\":5},\"reason\":\"short log reason\"}."
    )
    user = (
        f"Editor has code: {'yes' if has_code else 'no'}\n"
        f"Recent generated program: {'yes' if has_recent_generated else 'no'}\n"
        f"Current mode or tutorial state: {str(current_mode or '(none)')[:120]}\n"
        f"Short memory summary: {str(memory_summary or '(none)')[:400]}\n"
        f"Normalized command: {str(normalized_text or command_text or '')[:500]}\n"
        f"Student command: {str(command_text or '')[:500]}\n"
        "Return one JSON object only."
    )
    return system, user


_DETERMINISTIC_EXACT_RE = re.compile(
    r"^(?:run|run code|execute|stop|stop everything|help|what can i do(?: here)?|"
    r"start tutorial|begin tutorial|more|print hello(?: world)?|print numbers 1 to 5|"
    r"print even numbers up to 10|make a while loop|while true loop|make an if statement|"
    r"check if marks are passing|make a list of fruits|function that adds two numbers)$",
    re.IGNORECASE,
)
_EDIT_LIKE_RE = re.compile(
    r"\b(?:make\s+it|make\s+this|make\s+that|change\s+it|change\s+this|change\s+that|"
    r"edit\s+this|edit\s+that|replace|add|remove|use\s+(?:while|for|numbers|stars)|"
    r"now\s+make|do\s+this|arey|instead|rather\s+than|row|line\s+\d+|"
    r"condition|loop|output|passing\s+marks|ask\s+the\s+user|input|"
    r"bigger|smaller|shorter|clearer|triangle|square|wider)\b",
    re.IGNORECASE,
)
_INDIRECT_CODE_REF_RE = re.compile(
    r"\b(?:it|this|that|the\s+loop|the\s+output|the\s+row|the\s+condition|"
    r"the\s+code|same\s+thing|current\s+program)\b",
    re.IGNORECASE,
)
_CONVERSATIONAL_RE = re.compile(
    r"\b(?:please|can you|could you|would you|a little|arey|now|also|same thing|"
    r"make it|do this|do the same)\b",
    re.IGNORECASE,
)


def should_consult_ai_for_command(raw_text: str, normalized_text: str = "", context: Optional[Dict[str, Any]] = None) -> bool:

    context = context or {}
    text = " ".join(str(normalized_text or raw_text or "").lower().strip().rstrip(".!?").split())
    if not text:
        return False
    if _DETERMINISTIC_EXACT_RE.match(text):
        return False
    if bool(context.get("security_sensitive")):
        return False
    deterministic_confidence = float(context.get("deterministic_confidence") or 0.0)
    has_code = bool(context.get("has_code"))
    has_recent_generated = bool(context.get("has_recent_generated"))
    if deterministic_confidence and deterministic_confidence >= 0.9 and not _EDIT_LIKE_RE.search(text):
        return False
    if _EDIT_LIKE_RE.search(text):
        return True
    if has_code and _INDIRECT_CODE_REF_RE.search(text):
        return True
    if has_recent_generated and (_INDIRECT_CODE_REF_RE.search(text) or _CONVERSATIONAL_RE.search(text)):
        return True
    if deterministic_confidence < 0.55 and (_CONVERSATIONAL_RE.search(text) or len(text.split()) >= 3):
        return True
    return False


def _extract_json_object(raw: Any) -> Optional[dict]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", str(raw or "")).strip()
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except (TypeError, ValueError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _contains_blocked_payload(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9_]+", "_", str(key).strip().lower())
            if normalized_key in _BLOCKED_KEYS:
                return True
            if _contains_blocked_payload(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_blocked_payload(child) for child in value)
    return False


def validate_mapping(mapping: Any) -> Tuple[bool, str]:

    if not isinstance(mapping, dict):
        return False, "mapping_not_object"
    if set(mapping) - _TOP_LEVEL_KEYS:
        return False, "unknown_top_level_key"
    if _contains_blocked_payload(mapping):
        return False, "blocked_payload"

    intent = str(mapping.get("intent") or "").strip()
    if intent not in ALLOWED_INTENTS:
        return False, "intent_not_allowed"

    try:
        confidence = float(mapping.get("confidence"))
    except (TypeError, ValueError):
        return False, "invalid_confidence"
    if not 0.0 <= confidence <= 1.0:
        return False, "invalid_confidence"

    slots = mapping.get("slots", {})
    if slots is None:
        slots = {}
    if not isinstance(slots, dict):
        return False, "invalid_slots"

    allowed_slot_keys = _INTENT_SLOT_KEYS.get(intent, set())
    if slots and intent not in _INTENT_SLOT_KEYS:
        return False, "unexpected_slots"
    if set(slots) - allowed_slot_keys:
        return False, "invalid_slots"

    if intent == "insert_beginner_loop":
        if set(slots) - _LEGACY_LOOP_SLOT_KEYS:
            return False, "invalid_loop_slots"
        try:
            start = int(slots.get("start", 0))
            stop = int(slots.get("stop", 3))
        except (TypeError, ValueError):
            return False, "invalid_loop_bounds"
        output = str(slots.get("output", "print_numbers") or "")
        if start != 0 or stop != 3 or output != "print_numbers":
            return False, "unsupported_loop_slots"
    else:
        for key, value in slots.items():
            if key in _NUMERIC_SLOT_KEYS:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    return False, "invalid_numeric_slot"
                if abs(number) > 100:
                    return False, "slot_out_of_range"
                if key == "step" and number == 0:
                    return False, "invalid_step"
                continue
            if key in _BOOL_SLOT_KEYS:
                if isinstance(value, bool):
                    continue
                if str(value).strip().lower() in {"true", "false", "yes", "no", "1", "0"}:
                    continue
                return False, "invalid_bool_slot"
            text_value = str(value or "")
            if "\n" in text_value or "\r" in text_value:
                return False, "unsafe_slot_text"
            if not _SAFE_SLOT_TEXT_RE.match(text_value):
                return False, "unsafe_slot_text"

    reason = str(mapping.get("reason", "") or "")
    if len(reason) > 180:
        return False, "reason_too_long"
    return True, ""


def confidence_band(confidence: float) -> str:
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        value = 0.0
    if value >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    if value >= MEDIUM_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"


def map_command(
    command_text: str,
    *,
    ai_fn: Callable[[str, str], str],
    has_code: bool = False,
    normalized_text: str = "",
    has_recent_generated: bool = False,
    current_mode: str = "",
    memory_summary: str = "",
) -> Dict[str, Any]:

    system, user = mapper_messages(
        command_text,
        has_code=has_code,
        normalized_text=normalized_text,
        has_recent_generated=has_recent_generated,
        current_mode=current_mode,
        memory_summary=memory_summary,
    )
    try:
        raw = ai_fn(system, user)
    except Exception as exc:
        safe_reason = groq_key_manager.redact_known_keys(str(exc))[:160]
        return {"status": "failed", "reason": safe_reason}

    parsed = _extract_json_object(raw)
    ok, reason = validate_mapping(parsed)
    if not ok:
        safe_reason = groq_key_manager.redact_known_keys(reason or "invalid_json")[:160]
        return {"status": "invalid", "reason": safe_reason}

    mapping = {
        "intent": str(parsed.get("intent") or ""),
        "confidence": float(parsed.get("confidence", 0.0) or 0.0),
        "slots": parsed.get("slots") or {},
        "reason": str(parsed.get("reason", "") or "")[:180],
    }
    return {
        "status": "mapped",
        "mapping": mapping,
        "band": confidence_band(mapping["confidence"]),
    }
