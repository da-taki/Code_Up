"""Confidence-aware clarification for risky or ambiguous voice commands.

Speech recognition can mishear exact prompts, and some commands are destructive.
Before CodeUp blindly executes a risky action it should ask ONE short
confirmation/clarification. This module makes that decision.

Scope is deliberately narrow so it never blocks clean commands:
  * a destructive file op with a vague referent ("delete that file") with no
    clear file in memory -> ask which file;
  * a sized symbol-pattern request that does not parse cleanly and is vaguely
    worded ("make a five by five thing with the third line different") -> ask a
    short, specific pattern question.

Everything is deterministic; Key 2 is optional and only ever *rephrases* the
deterministic question with bounded context. If Key 2 is unavailable the
deterministic question is used, so clarification always works. Key 2 can never
invent a file name, count, output, or error here.
"""

import re
from typing import Any, Callable, Dict, Optional, Tuple

import grounded_ai
from intent_parser import IntentParser

_PARSER = IntentParser()

# A destructive file op whose target is a vague referent (no concrete filename).
_VAGUE_FILE_OP = re.compile(
    r"^(?:please\s+)?(delete|remove|trash|erase|rename)\s+"
    r"(?:the\s+|that\s+|this\s+|current\s+|it\b\s*)?"
    r"(?:file|one|thing|it)?\s*(?:again|please)?$",
    re.IGNORECASE,
)

_SIZE_RE = re.compile(r"\b([a-z0-9]+)\s+(?:by|x|times)\s+([a-z0-9]+)\b", re.IGNORECASE)
_VAGUE_NOUN = re.compile(r"\b(thing|things|shape|something|whatever|design|figure|stuff|pattern thing)\b", re.IGNORECASE)
_ORDINAL_LINE = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+(line|row)\b",
    re.IGNORECASE,
)
_LINE_DIFF = re.compile(r"\b(different|differ|special|changed|other|unique|odd|extra|more|fewer)\b", re.IGNORECASE)
_EXPLICIT_SYMBOL = re.compile(r"(\*|#|\bstars?\b|\basterisks?\b|\bhash(?:es)?\b|\bhashtags?\b|\bdots?\b|\bplus(?:es)?\b)", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def _to_int(word: str) -> Optional[int]:
    word = (word or "").strip().lower()
    if word.isdigit():
        return int(word)
    return _PARSER._word_to_number(word)


def _find_size(text: str) -> Optional[Tuple[int, int]]:
    match = _SIZE_RE.search(text or "")
    if not match:
        return None
    a, b = _to_int(match.group(1)), _to_int(match.group(2))
    if a is None or b is None:
        return None
    return (a, b)


def _need(message: str, reason: str) -> Dict[str, Any]:
    return {"needs_clarification": True, "message": message, "reason": reason}


def _ambiguous_pattern(text: str) -> Optional[Dict[str, Any]]:
    """A sized pattern request that is too vague to parse exactly."""
    t = _norm(text)
    size = _find_size(text)
    if not size:
        return None
    vague = bool(_VAGUE_NOUN.search(t))
    line_match = _ORDINAL_LINE.search(t)
    line_diff = bool(line_match) and bool(_LINE_DIFF.search(t))
    # Only flag when there is a genuine ambiguity signal (a vague noun, or a
    # "the third line is different" with no concrete count). A clear request
    # like "5 by 5 star pattern" never reaches here (its exact parse succeeds).
    if not (vague or line_diff):
        return None
    return {
        "size": size,
        "ordinal": line_match.group(1) if line_match else "",
        "line_word": line_match.group(2) if line_match else "line",
    }


def _pattern_question(pat: Dict[str, Any]) -> str:
    n, m = pat["size"]
    question = f"Do you want a {n} by {m} pattern"
    if pat.get("ordinal"):
        question += f", and should the {pat['ordinal']} {pat['line_word']} have a different number of symbols?"
    else:
        question += ", and which symbol should it use, stars or hashes?"
    return question


def _question_facts(question: str) -> list:
    """The concrete facts a clarifying question must keep (size, line ref)."""
    facts = []
    size = re.search(r"\b(\d+)\s+by\s+(\d+)\b", question)
    if size:
        facts.append(f"{size.group(1)} by {size.group(2)}")
    line = _ORDINAL_LINE.search(question)
    if line:
        facts.append(f"{line.group(1)} {line.group(2)}")
    return facts


def _ai_refine_question(deterministic_question: str, text: str,
                        ai_fn: Optional[Callable[[str, str], str]]) -> str:
    """Optionally let Key 2 rephrase the clarifying question. Returns "" unless
    the rephrase is grounded (keeps the size/line facts, invents nothing), so a
    weaker/generic question can never replace the specific deterministic one."""
    if not ai_fn:
        return ""
    system = (
        "You are CodeUp's command clarifier for a blind beginner. The user's voice "
        "command was ambiguous. Ask ONE short clarifying question (under 25 words) that "
        "KEEPS every concrete detail from the draft question (sizes, line numbers). Do "
        "NOT invent file names, code, numbers, or results, and do NOT make it more "
        "generic. Plain text, end with a question mark."
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
        required_facts=_question_facts(deterministic_question),
        context=f"{deterministic_question} {text}",
        single_sentence=True, max_words=30, max_chars=200,
    )
    return grounded if grounded == raw else ""


def assess(text: str, *, intent: str = "", confidence: float = 0.0, code: str = "",
           mem: Optional[Dict[str, Any]] = None, exact_result: Optional[Dict[str, Any]] = None,
           ai_fn: Optional[Callable[[str, str], str]] = None) -> Optional[Dict[str, Any]]:
    """Decide whether a risky/ambiguous command needs clarification.

    Returns ``{"needs_clarification": True, "message", "reason"}`` or None when
    the command is safe/clear and should run normally.
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
            return _need(f"Which file should I {verb}?", "missing_file_reference")
        return _need(
            f"Do you want me to {verb} {recent}? Say yes to confirm, or name another file.",
            "confirm_destructive",
        )

    # 2. A sized symbol pattern that does not parse cleanly and is vaguely worded.
    if not (exact_result and exact_result.get("success")):
        pat = _ambiguous_pattern(text)
        if pat:
            question = _pattern_question(pat)
            refined = _ai_refine_question(question, text, ai_fn)
            return _need(refined or question, "ambiguous_pattern")

    return None
