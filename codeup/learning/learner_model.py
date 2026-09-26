"""Session-local, evidence-based learner model.

Tracks only programming evidence (runs, errors, hints, guided checkpoints,
explanation requests) - never personal traits or ability labels. States are
deliberately cautious: "demonstrated" needs repeated independent success,
and running AI-generated code never counts as the learner's own success.

The adaptation queries at the bottom (``scaffolding``, ``explanation_style``,
``prompt_directive``, ``adapt_concept_answer``) are what real learning paths
consume: guided learning, concept/theory answers, tutor hints and the AI
explanation prompts.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional


STATES = ("not_introduced", "introduced", "practicing", "needs_reinforcement", "demonstrated")

_MAX_EVENTS = 400

_SUCCESS_KINDS = {"success", "quiz_correct", "independent_fix", "independent_success"}
_STRONG_SUCCESS_KINDS = {"independent_success", "independent_fix", "quiz_correct"}

# Beginner-facing aliases -> the detector's canonical concept labels
# (codeup/classroom/concepts.py CURRICULUM_CONCEPTS), so evidence recorded from
# code detection, guided learning and concept questions lands on one key.
_ALIASES = {
    "print": "print output", "printing": "print output", "output": "print output",
    "variable": "variables", "assignment": "variables",
    "input": "input", "user input": "input",
    "type conversion": "data types", "data type": "data types", "types": "data types",
    "if": "conditionals (if/else)", "if else": "conditionals (if/else)", "elif": "conditionals (if/else)",
    "else": "conditionals (if/else)", "condition": "conditionals (if/else)", "conditions": "conditionals (if/else)",
    "conditionals": "conditionals (if/else)", "comparison": "conditionals (if/else)",
    "comparisons": "conditionals (if/else)", "boolean logic": "conditionals (if/else)",
    "loop": "loops", "for": "loops", "for loop": "loops", "for loops": "loops", "while": "loops",
    "while loop": "loops", "while loops": "loops", "iteration": "loops", "counter": "loops",
    "counters": "loops", "accumulator": "loops", "accumulators": "loops",
    "list": "lists", "list iteration": "lists",
    "dictionary": "dictionaries", "dict": "dictionaries",
    "function": "functions", "parameters": "functions", "parameter": "functions",
    "return": "functions", "return values": "functions", "decomposition": "functions",
    "debug": "debugging", "errors": "debugging", "syntax errors": "debugging",
}


def canonical(concept: str) -> str:
    key = " ".join(str(concept or "").lower().replace("_", " ").split())
    return _ALIASES.get(key, key)


def _bucket(mem: Dict[str, Any]) -> Dict[str, Any]:
    model = mem.setdefault("learner_model", {})
    if not isinstance(model, dict):
        model = {}
        mem["learner_model"] = model
    model.setdefault("concepts", {})
    model.setdefault("events", [])
    return model


def record_evidence(mem: Dict[str, Any], concept: str, evidence: str, *, strength: int = 1) -> None:
    concept = canonical(concept)
    evidence = str(evidence or "").strip().lower()
    if not concept or not evidence:
        return
    model = _bucket(mem)
    events = model.setdefault("events", [])
    events.append({"concept": concept, "evidence": evidence, "strength": max(1, int(strength or 1)), "ts": time.time()})
    del events[:-_MAX_EVENTS]
    model["concepts"][concept] = state_for(events, concept)


def record_evidence_for_code(mem: Dict[str, Any], code: str, evidence: str) -> List[str]:
    """Record one evidence kind for every concept detected in ``code``."""
    concepts = detect(code)
    for concept in concepts:
        record_evidence(mem, concept, evidence)
    if not concepts and evidence == "simpler_requested":
        record_evidence(mem, "general", evidence)
    return concepts


def detect(code: str) -> List[str]:
    try:
        from codeup.classroom import concepts as classroom_concepts
        return list(classroom_concepts.detect_concepts(code or ""))
    except Exception:
        return []


def reset(mem: Dict[str, Any]) -> None:
    mem["learner_model"] = {"concepts": {}, "events": []}


def state_for(events: Iterable[Dict[str, Any]], concept: str) -> str:
    recent = [event for event in events if event.get("concept") == concept][-12:]
    if not recent:
        return "not_introduced"
    score = 0
    failures = 0
    for event in recent:
        kind = event.get("evidence")
        strength = max(1, int(event.get("strength") or 1))
        if kind in {"encountered", "asked"}:
            score += 1
        elif kind in {"practiced", "hint_used", "success_with_hint"}:
            score += 2 if kind in {"practiced", "success_with_hint"} else 0
            failures += 1 if kind == "hint_used" else 0
        elif kind in {"error", "checkpoint_failed"}:
            score -= strength
            failures += strength
        elif kind == "simpler_requested":
            failures += 0  # a preference signal, not a failure
        elif kind in _SUCCESS_KINDS:
            score += 3 * strength
    successes = [e for e in recent if e.get("evidence") in _SUCCESS_KINDS]
    if failures >= 3 and score < 6:
        return "needs_reinforcement"
    if score >= 6 and len(successes) >= 2:
        return "demonstrated"
    if score >= 3:
        return "practicing"
    return "introduced"


def concept_state(mem: Dict[str, Any], concept: str) -> str:
    return str((_bucket(mem).get("concepts") or {}).get(canonical(concept)) or "not_introduced")


def _events(mem: Dict[str, Any], concept: Optional[str] = None, kinds: Optional[set] = None,
            within: int = 40) -> List[Dict[str, Any]]:
    events = list(_bucket(mem).get("events") or [])[-within:]
    if concept is not None:
        key = canonical(concept)
        events = [e for e in events if e.get("concept") == key]
    if kinds is not None:
        events = [e for e in events if e.get("evidence") in kinds]
    return events


# ---------------------------------------------------------------------------
# Adaptation queries used by real learning paths
# ---------------------------------------------------------------------------

def scaffolding(mem: Dict[str, Any], concept: str) -> str:
    """"more" (smaller steps, extra hints), "less" (skip basics), or "normal"."""
    state = concept_state(mem, concept)
    if state == "needs_reinforcement":
        return "more"
    if state == "demonstrated":
        strong = _events(mem, concept, _STRONG_SUCCESS_KINDS, within=60)
        return "less" if len(strong) >= 2 else "normal"
    return "normal"


def solves_independently(mem: Dict[str, Any], concept: Optional[str] = None) -> bool:
    """Recent checkpoint history shows success without hints."""
    recent = _events(mem, concept, {"independent_success", "success_with_hint", "hint_used",
                                    "checkpoint_failed"}, within=30)[-4:]
    return len(recent) >= 2 and all(e.get("evidence") == "independent_success" for e in recent[-2:])


def explanation_style(mem: Dict[str, Any]) -> str:
    """"short" once the learner has asked for simpler explanations twice recently."""
    asks = _events(mem, None, {"simpler_requested"}, within=40)
    return "short" if len(asks) >= 2 else "normal"


def prompt_directive(mem: Dict[str, Any], code: str = "") -> str:
    """One or two sentences appended to AI explanation prompts."""
    concepts = detect(code) if code else []
    parts: List[str] = []
    struggling = [c for c in concepts if concept_state(mem, c) == "needs_reinforcement"]
    shown = [c for c in concepts if concept_state(mem, c) == "demonstrated"]
    if struggling:
        parts.append(
            "The learner has recently needed extra help with " + ", ".join(struggling[:2])
            + ": use one very small example, short steps, and connect back to the basic idea."
        )
    if shown:
        parts.append(
            "The learner has already shown they can use " + ", ".join(shown[:2])
            + " on their own: do not re-explain the basic definition of that concept."
        )
    if explanation_style(mem) == "short":
        parts.append("The learner prefers simpler, shorter explanations: at most three short sentences.")
    return (" " + " ".join(parts)) if parts else ""


def adapt_concept_answer(mem: Dict[str, Any], concept: str, answer: str) -> str:
    """Shape a deterministic theory answer (definition / Example / Beginner note)."""
    text = str(answer or "").strip()
    if not text:
        return text
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    level = scaffolding(mem, concept)
    style = explanation_style(mem)
    if level == "less" and len(paragraphs) >= 2:
        # Already demonstrated: skip the basic definition, keep the example.
        rest = [p for p in paragraphs[1:] if not p.lower().startswith("beginner note")]
        return "You already use this well, so here is just the example.\n\n" + "\n\n".join(rest or paragraphs[1:])
    if level == "more":
        first = paragraphs[0]
        example = next((p for p in paragraphs[1:] if p.lower().startswith("example")), "")
        return "Let's go slowly, one small idea at a time.\n\n" + "\n\n".join(p for p in (first, example) if p) + \
            "\n\nTry changing just one value in that example and run it."
    if style == "short":
        return "\n\n".join(paragraphs[:2])
    return text


def last_explained_concept(mem: Dict[str, Any]) -> str:
    """The concept of the most recent adapted explanation, for "why did you
    explain it that way"; empty when nothing has been explained yet."""
    asked = _events(mem, None, {"asked"}, within=20)
    return str(asked[-1].get("concept") or "") if asked else ""


def adaptation_note(mem: Dict[str, Any], concept: str) -> str:
    state = concept_state(mem, concept)
    if state == "needs_reinforcement":
        return "I will keep this shorter and use a smaller example because this topic has needed reinforcement recently."
    if state == "demonstrated":
        return "I will skip the very basic definition unless you ask, because you have demonstrated this topic more than once."
    if state == "practicing":
        return "I will give a normal beginner explanation with one concrete next step."
    return "I will start with the basic idea first."


def progress_summary(mem: Dict[str, Any]) -> str:
    concepts = {k: v for k, v in (_bucket(mem).get("concepts") or {}).items() if k != "general"}
    if not concepts:
        return "I do not have learning evidence yet. Run code, use tutorials, or ask for hints and I will track programming progress only."
    groups: Dict[str, List[str]] = {state: [] for state in STATES}
    for concept, state in concepts.items():
        groups.setdefault(state, []).append(concept)
    parts = []
    if groups.get("demonstrated"):
        parts.append("Demonstrated: " + ", ".join(sorted(groups["demonstrated"])[:5]) + ".")
    if groups.get("practicing"):
        parts.append("Practicing: " + ", ".join(sorted(groups["practicing"])[:5]) + ".")
    if groups.get("needs_reinforcement"):
        parts.append("Needs reinforcement: " + ", ".join(sorted(groups["needs_reinforcement"])[:5]) + ".")
    return " ".join(parts) or "You have started learning, but no concept has enough evidence yet."


def practice_recommendation(mem: Dict[str, Any]) -> str:
    concepts = _bucket(mem).get("concepts") or {}
    needs = sorted(k for k, v in concepts.items() if v == "needs_reinforcement" and k != "general")
    if needs:
        return f"Practice {needs[0]} next with a small example and one run at a time. Say start guided learning {needs[0]} for step by step practice."
    practicing = sorted(k for k, v in concepts.items() if v == "practicing" and k != "general")
    if practicing:
        return f"Keep practicing {practicing[0]}, then try using it in a tiny project."
    return "Start with printing, variables, and input. Say start guided learning when you are ready."
