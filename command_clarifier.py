"""Confidence-aware clarification for risky or ambiguous voice commands.

Speech recognition can mishear exact prompts, and some commands are destructive.
Before CodeUp blindly executes a risky action it should ask ONE short
clarification — and it always remembers a structured ``pending`` object so it can
understand the user's answer (see clarification_flow + the /voice-command route).

Scope is deliberately narrow so it never blocks clean commands:
  * a destructive file op with a vague referent ("delete that file") -> ask which
    file (or confirm the remembered one);
  * a sized symbol-pattern request that does not parse cleanly -> ask only for the
    missing slot(s) (symbol and/or the special row's count).

Everything is deterministic; Key 2 may only *rephrase* the question with bounded
context and never weaken/invent facts (enforced by grounded_ai).
"""

import re
from typing import Any, Callable, Dict, List, Optional

import clarification_flow
import grounded_ai

# A destructive file op whose target is a vague referent (no concrete filename).
_VAGUE_FILE_OP = re.compile(
    r"^(?:please\s+)?(delete|remove|trash|erase|rename)\s+"
    r"(?:the\s+|that\s+|this\s+|current\s+|it\b\s*)?"
    r"(?:file|one|thing|it)?\s*(?:again|please)?$",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def _need(message: str, reason: str, pending: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    decision = {"needs_clarification": True, "message": message, "reason": reason}
    if pending:
        decision["pending"] = pending
    return decision


def _ai_refine_question(deterministic_question: str, text: str,
                        ai_fn: Optional[Callable[[str, str], str]],
                        required_facts: List[str]) -> str:
    """Optionally let Key 2 rephrase the clarifying question. Returns "" unless
    the rephrase is grounded (keeps the size/line facts, invents nothing), so a
    weaker/generic question can never replace the specific deterministic one."""
    if not ai_fn:
        return ""
    system = (
        "You are CodeUp's command clarifier for a blind beginner. The user's voice "
        "command was ambiguous. Ask ONE short clarifying question (under 25 words) that "
        "KEEPS every concrete detail from the draft question (sizes, line numbers, "
        "symbol). Do NOT invent file names, code, numbers, or results, and do NOT make "
        "it more generic. Plain text, end with a question mark."
    )
    user = (
        f"User command: {text}\n"
        f"Draft question: {deterministic_question}\n"
        "Return one short clarifying question that keeps the same concrete details."
    )
    try:
        raw = str(ai_fn(system, user) or "").strip().strip('"').strip()
    except Exception:
        return ""
    if not raw or not raw.endswith("?"):
        return ""
    grounded = grounded_ai.ground(
        raw, deterministic_question,
        required_facts=required_facts,
        context=f"{deterministic_question} {text}",
        single_sentence=True, max_words=30, max_chars=200,
    )
    return grounded if grounded == raw else ""


def assess(text: str, *, intent: str = "", confidence: float = 0.0, code: str = "",
           mem: Optional[Dict[str, Any]] = None, exact_result: Optional[Dict[str, Any]] = None,
           ai_fn: Optional[Callable[[str, str], str]] = None) -> Optional[Dict[str, Any]]:
    """Decide whether a risky/ambiguous command needs clarification.

    Returns ``{"needs_clarification", "message", "reason", "pending"?}`` or None
    when the command is safe/clear and should run normally. ``pending`` is the
    structured state the route stores so the user's next utterance is understood.
    """
    mem = mem or {}
    t = _norm(text)
    if not t:
        return None

    # 1. Destructive file op with a vague referent.
    file_op = _VAGUE_FILE_OP.match(t)
    if file_op:
        verb = "rename" if file_op.group(1).lower() == "rename" else "delete"
        recent = (mem.get("last_opened_file") or mem.get("last_active_file") or "").strip()
        if not recent:
            return _need(f"Which file should I {verb}?", "missing_file_reference",
                         pending={"type": "file", "verb": verb, "destructive": True})
        return _need(
            f"Do you want me to {verb} {recent}? Say yes to confirm, or name another file.",
            "confirm_destructive",
            pending={"type": "file", "verb": verb, "file": recent, "destructive": True},
        )

    # 2. A sized symbol pattern that is missing a needed slot (no symbol, or a
    #    "third line is different" with no count). We ask even when the exact
    #    parser would otherwise succeed by ignoring the special-row request, so
    #    the learner's intent is honoured. A fully specified request has no
    #    missing slots and is never clarified.
    slots = clarification_flow.extract_pattern_slots(text)
    if (
        isinstance(exact_result, dict)
        and exact_result.get("success")
        and not (slots.get("special_row") is not None and slots.get("special_count") is None)
    ):
        return None
    if clarification_flow.is_pattern_clarifiable(slots) and (
        slots.get("special_row") or clarification_flow.has_pattern_intent(text)
    ):
        question = clarification_flow.pattern_question(slots)
        refined = _ai_refine_question(question, text, ai_fn, clarification_flow.question_facts(question))
        return _need(refined or question, "ambiguous_pattern",
                     pending={"type": "pattern", "slots": slots})

    return None
