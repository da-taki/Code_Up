"""Server-side AI policy enforcement.

An instructor sets one policy per assignment. Every AI-assisted capability
in the app is tagged with a coarse capability name, and a policy is just an
allow-list of capability names:

    FULL               -> generate, explain, hint, error_help, assessment
    EXPLANATIONS_ONLY   -> explain, error_help
    HINTS_ONLY          -> hint
    ERROR_HELP_ONLY     -> error_help
    ASSESSMENT          -> assessment
    OFF                 -> (nothing)

Capabilities:
    generate     full code generation / rewriting (e.g. "write this for me", auto-fix)
    explain      explaining what existing code does, walkthroughs, summaries
    hint         nudges/suggestions that don't hand over the answer
    error_help   explaining what an error means / what happened
    assessment   instructor-facing or self-check assessment tools (quizzes,
                 progress checks, practice-bug generation) - not "help"

This module never touches the network or a request object directly (it is
plain, unit-testable logic); the Flask layer resolves the request/cookie
context and calls into here.
"""

from __future__ import annotations

from typing import Optional

POLICIES = ("FULL", "EXPLANATIONS_ONLY", "HINTS_ONLY", "ERROR_HELP_ONLY", "ASSESSMENT", "OFF")

CAPABILITIES = ("generate", "explain", "hint", "error_help", "assessment")

_ALLOW = {
    "FULL": {"generate", "explain", "hint", "error_help", "assessment"},
    "EXPLANATIONS_ONLY": {"explain", "error_help"},
    "HINTS_ONLY": {"hint"},
    "ERROR_HELP_ONLY": {"error_help"},
    "ASSESSMENT": {"assessment"},
    "OFF": set(),
}

_LABELS = {
    "generate": "generating full code",
    "explain": "explaining code",
    "hint": "giving hints",
    "error_help": "explaining errors",
    "assessment": "assessment tools",
}


def normalize_policy(value: Optional[str]) -> str:
    value = str(value or "").strip().upper()
    return value if value in _ALLOW else "FULL"


def is_allowed(capability: str, policy: str) -> bool:
    policy = normalize_policy(policy)
    return capability in _ALLOW.get(policy, set())


def blocked_message(capability: str, policy: str) -> str:
    label = _LABELS.get(capability, capability)
    policy = normalize_policy(policy)
    if policy == "OFF":
        return "Your instructor has turned off AI assistance for this assignment. Core CodeUp features still work: the editor, running your code, and accessibility tools."
    if policy == "ASSESSMENT":
        return "This assignment is in assessment mode, so AI help is turned off while you work. You can still edit, run, save and submit your code."
    return (
        f"Your instructor has restricted AI help on this assignment to keep it fair. "
        f"{label.capitalize()} is not available right now, but you can still edit, run, save and submit your code."
    )


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


def resolve_policy_for_request(get_cookie, get_json_field, get_assignment_fn) -> str:
    """Resolve the effective AI policy for the current request.

    Parameters are small callables (rather than importing Flask's ``request``
    directly) so this stays testable without a request context:
      - get_cookie(name) -> Optional[str]
      - get_json_field(name) -> Optional[str]   (JSON body override)
      - get_assignment_fn(assignment_id: int) -> Optional[dict] with 'ai_policy'

    No assignment context at all (anonymous, non-cohort IDE usage) means
    FULL - the classroom layer must never restrict the existing single-user
    IDE experience.
    """
    assignment_id = get_json_field("assignment_id") or get_cookie(ASSIGNMENT_COOKIE)
    if not assignment_id:
        return "FULL"
    try:
        assignment = get_assignment_fn(int(assignment_id))
    except (TypeError, ValueError):
        return "FULL"
    if not assignment:
        return "FULL"
    return normalize_policy(assignment.get("ai_policy"))
