"""Server-side AI/tool policy enforcement.

An instructor configures NINE independent toggles per assignment (this is a
granular model, not a single six-tier enum - see history for the earlier,
simpler version):

    generate         full code generation / rewriting ("write this for me")
    fix              the dedicated AI "Fix" debugger (separate from generate,
                      so an instructor can allow error fixes without allowing
                      free-form generation, or vice versa)
    explain          explaining what existing code does, walkthroughs, summaries
    hint             nudges/suggestions that don't hand over the answer
    error_help       explaining what an error means / what happened
    concept_qa       "what is a dictionary" style concept glossary answers
    audio_code_map   the Audio Code Map narration tool
    step_narration   step-by-step execution narration
    watch_variable   the variable/state watch tool

Three of these (audio_code_map, step_narration, watch_variable) are mostly
deterministic, not AI - they are still gated here because an instructor may
reasonably want to disable them during an assessment even though they are
not "AI help" in the generative sense.

A tenth capability, ``assessment`` (instructor/self-check tools like the
practice quiz generator), is intentionally NOT instructor-configurable - it
always mirrors the ``explain`` toggle, since it is a lower-stakes review aid
rather than "help" that could undermine an assessment.

Accessibility features (screen reader access, keyboard navigation, output
access, focus management, program input) are never gated by anything in
this module - no route that implements them calls into here.

This module never touches the network or a request object directly (it is
plain, unit-testable logic); the Flask layer resolves the request/cookie
context and calls into here.
"""

from __future__ import annotations

from typing import Dict, Optional

CAPABILITIES = (
    "generate", "fix", "explain", "hint", "error_help",
    "concept_qa", "audio_code_map", "step_narration", "watch_variable",
)

# Presets are a convenience starting point an instructor can apply and then
# fine-tune - the stored, enforced state is always the full settings dict,
# never the preset name alone.
PRESETS: Dict[str, Dict[str, bool]] = {
    "FULL": {cap: True for cap in CAPABILITIES},
    "EXPLANATIONS_ONLY": {
        "generate": False, "fix": False, "explain": True, "hint": False, "error_help": True,
        "concept_qa": True, "audio_code_map": True, "step_narration": True, "watch_variable": True,
    },
    "HINTS_ONLY": {
        "generate": False, "fix": False, "explain": False, "hint": True, "error_help": False,
        "concept_qa": False, "audio_code_map": False, "step_narration": False, "watch_variable": False,
    },
    "ERROR_HELP_ONLY": {
        "generate": False, "fix": False, "explain": False, "hint": False, "error_help": True,
        "concept_qa": False, "audio_code_map": False, "step_narration": False, "watch_variable": False,
    },
    "ASSESSMENT": {cap: False for cap in CAPABILITIES},
    "OFF": {cap: False for cap in CAPABILITIES},
}
POLICIES = tuple(PRESETS.keys())

CAPABILITY_LABELS = {
    "generate": "Code generation",
    "fix": "Automatic fixes",
    "explain": "Explanations",
    "hint": "Hints",
    "error_help": "Error explanations",
    "concept_qa": "Concept questions",
    "audio_code_map": "Audio code map",
    "step_narration": "Step narration",
    "watch_variable": "Variable watch",
}


def normalize_policy(value: Optional[str]) -> str:
    value = str(value or "").strip().upper()
    return value if value in PRESETS else "FULL"


def default_settings_for_preset(preset: Optional[str]) -> Dict[str, bool]:
    return dict(PRESETS[normalize_policy(preset)])


def normalize_settings(raw: Optional[Dict]) -> Dict[str, bool]:
    """Fill in any missing/malformed keys as True (fail open to the same
    behavior as "no assignment context" - never silently over-restrict due
    to a bad or partial settings blob)."""
    raw = raw if isinstance(raw, dict) else {}
    return {cap: bool(raw[cap]) if cap in raw else True for cap in CAPABILITIES}


def is_allowed(capability: str, settings: Dict[str, bool]) -> bool:
    settings = normalize_settings(settings)
    if capability == "assessment":
        return settings.get("explain", True)
    return settings.get(capability, True)


def blocked_message(capability: str, settings: Optional[Dict[str, bool]] = None) -> str:
    label = CAPABILITY_LABELS.get(capability, capability.replace("_", " "))
    return (
        f"Your instructor has turned off {label.lower()} for this assignment. "
        "You can still edit, run, save and submit your code, and accessibility "
        "features stay fully available."
    )


def _join_with_and(items):
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def summarize_settings(settings: Optional[Dict[str, bool]], *, is_assessment: bool = False) -> str:
    """A short, non-repetitive, accessible summary of what's restricted.

    Meant to be announced ONCE when a learner opens an assignment, not on
    every subsequent action - callers control when this is spoken.
    """
    settings = normalize_settings(settings)
    help_caps = ("generate", "fix", "hint", "explain", "error_help")
    disabled_help = [CAPABILITY_LABELS[c] for c in help_caps if not settings.get(c, True)]
    enabled_help = [CAPABILITY_LABELS[c] for c in ("explain", "error_help") if settings.get(c, True)]
    tool_caps = ("concept_qa", "audio_code_map", "step_narration", "watch_variable")
    disabled_tools = [CAPABILITY_LABELS[c] for c in tool_caps if not settings.get(c, True)]

    parts = []
    if is_assessment:
        parts.append("Assessment mode.")
    if disabled_help:
        verb = "is" if len(disabled_help) == 1 else "are"
        parts.append(f"{_join_with_and(disabled_help)} {verb} disabled.")
    if enabled_help and disabled_help:
        verb = "is" if len(enabled_help) == 1 else "are"
        parts.append(f"{_join_with_and(enabled_help)} {verb} allowed.")
    if disabled_tools:
        verb = "is" if len(disabled_tools) == 1 else "are"
        parts.append(f"{_join_with_and(disabled_tools)} {verb} also turned off.")
    if not parts:
        return "Full AI assistance is available for this assignment."
    return " ".join(parts)


# ---- chat capability classification ---------------------------------------

_ERROR_WORDS = (
    "error", "traceback", "exception", "broke", "broken", "crash", "crashed",
    "what happened", "why did it fail", "why did this fail", "failed", "bug",
    "doesn't work", "does not work", "won't run", "wont run", "not working",
)

_GENERATE_WORDS = (
    "write the code", "write me the code", "write my code", "give me the code",
    "give me the answer", "solve it", "solve this", "do it for me",
    "full solution", "complete this for me", "finish this for me",
    "just tell me the answer", "generate the code", "code it for me",
)

_HINT_WORDS = (
    "hint", "stuck", "nudge", "clue", "tip", "help me figure", "point me",
)


def classify_chat_capability(message: str) -> str:
    """Best-effort classification of a free-form chat message into a capability.

    Deliberately conservative: an explicit request for a finished answer is
    tagged 'generate'; error-shaped questions are 'error_help'; requests for
    a nudge are 'hint'; everything else defaults to 'explain' (the safest,
    least-generative default for "what does this mean" style questions).
    """
    text = str(message or "").lower()
    if any(w in text for w in _GENERATE_WORDS):
        return "generate"
    if any(w in text for w in _ERROR_WORDS):
        return "error_help"
    if any(w in text for w in _HINT_WORDS):
        return "hint"
    return "explain"


# ---- request-context resolution --------------------------------------------

ASSIGNMENT_COOKIE = "cu_assignment_id"


def resolve_settings_for_request(get_cookie, get_json_field, get_assignment_fn) -> Dict[str, bool]:
    """Resolve the effective capability settings for the current request.

    Parameters are small callables (rather than importing Flask's ``request``
    directly) so this stays testable without a request context:
      - get_cookie(name) -> Optional[str]
      - get_json_field(name) -> Optional[str]   (JSON body override)
      - get_assignment_fn(assignment_id: int) -> Optional[dict] with
        'capability_settings' (dict) and/or 'ai_policy' (preset name)

    No assignment context at all (anonymous, non-cohort IDE usage) means
    everything allowed - the classroom layer must never restrict the
    existing single-user IDE experience.
    """
    assignment_id = get_json_field("assignment_id") or get_cookie(ASSIGNMENT_COOKIE)
    if not assignment_id:
        return normalize_settings(None)
    try:
        assignment = get_assignment_fn(int(assignment_id))
    except (TypeError, ValueError):
        return normalize_settings(None)
    if not assignment:
        return normalize_settings(None)
    settings = assignment.get("capability_settings")
    if isinstance(settings, dict) and settings:
        return normalize_settings(settings)
    return default_settings_for_preset(assignment.get("ai_policy"))
