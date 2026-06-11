"""
Optional Intel OpenVINO local-intent demo (Intel AI Global Impact Festival).

This is a SMALL, ISOLATED, DIAGNOSTIC prototype. It is deliberately NOT wired
into CodeUp's real voice-command router, does not edit the editor, does not run
code, and does not call the cloud AI ("Key 2") orchestrator. It exists only to
show how common classroom voice commands such as "insert print hello" or
"write a program for even numbers" *could* be classified locally with Intel
OpenVINO before falling back to cloud AI in a future edge-first deployment.

What it does today:
  * Classifies a text command into one coarse intent category using a tiny set
    of deterministic local rules (no model, no downloads, no binaries).
  * Reports whether the OpenVINO runtime happens to be importable, so the same
    function works on machines with or without OpenVINO installed.

What it explicitly does NOT do (no overclaiming):
  * It does NOT run a trained OpenVINO model. No model files ship with CodeUp.
  * The OpenVINO runtime, if present, is only *detected* here; the rule engine
    still produces the actual classification until a model path is configured.
  * It does NOT mean CodeUp runs on OpenVINO or is fully offline.

Public API:
    classify_local_intent(text: str) -> dict
        Returns a JSON-serialisable dict with keys:
        available (bool), source (str), intent (str), confidence (float),
        note (str).
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# Optional dependency. The whole point of this demo is that CodeUp must keep
# running normally whether or not OpenVINO is installed, so the import is fully
# guarded and any failure (missing package, broken install, import-time error)
# simply leaves ``ov`` as None.
try:  # pragma: no cover - exercised only on machines with OpenVINO installed
    import openvino as ov  # type: ignore
except Exception:  # noqa: BLE001 - any import failure means "not available"
    ov = None


# The coarse intent vocabulary this demo can emit. Mirrors the spirit of
# CodeUp's real intents but is intentionally a small, fixed set for the demo.
INTENT_LABELS: Tuple[str, ...] = (
    "insert_code",
    "generate_code",
    "stop_speaking",
    "concept_question",
    "run_code",
    "unknown",
)

UNKNOWN_CONFIDENCE = 0.25

# Ordered rule table. The first intent whose pattern matches wins, so more
# specific / higher-priority intents are listed first. Each rule carries a base
# confidence in roughly the range a small local classifier might report. These
# are hand-tuned demo rules, not a trained model.
#
# Ordering rationale:
#   1. stop_speaking     - short, unambiguous control phrases ("stop speaking").
#   2. concept_question  - question-shaped utterances ("what does X mean", "why
#                          is ...") win before edit/generate/run verbs so a
#                          question is never mistaken for a command.
#   3. run_code          - "run", "execute", "launch" the program.
#   4. generate_code     - generate/write/create/make/build a program/function.
#   5. insert_code       - insert/add/append/type a line into the editor.
_RULES: List[Tuple[str, float, List[str]]] = [
    ("stop_speaking", 0.90, [
        r"\bstop\s+(speaking|talking|reading|narrat\w*|the\s+voice|voice)\b",
        r"\b(be\s+quiet|shut\s+up|silence|hush|quiet\s+please)\b",
        r"^\s*(stop|quiet|silence)\s*[.!]?\s*$",
        r"\bstop\s+(it|now|please)\b",
        r"\b(mute|stop\s+the\s+sound|stop\s+sound)\b",
        r"\b(chup|bolna\s+band)\b",  # Hinglish: be quiet / stop talking
    ]),
    ("concept_question", 0.85, [
        r"^\s*(hey|ok|okay|so|um|hmm)?[,\s]*what(?:'?s| is| are| does| do)\b.*\b(mean|do|does|is|are|work|for)\b",
        r"^\s*(hey|ok|okay|so|um|hmm)?[,\s]*what(?:'?s| is| are)\b",
        r"^\s*(hey|ok|okay|so|um|hmm)?[,\s]*why\b",
        r"^\s*(hey|ok|okay|so|um|hmm)?[,\s]*how\s+(do|does|can)\b",
        r"^\s*(hey|ok|okay|so|um|hmm)?[,\s]*(explain|describe)\b",
        r"\b(ka\s+matlab|kya\s+karta\s+hai|kya\s+hota\s+hai|kyun)\b",  # Hinglish concept Qs
        r"\?\s*$",
    ]),
    ("run_code", 0.88, [
        r"^\s*run\b",
        r"\brun\s+(the\s+)?(code|program|it|this|that|file|script)\b",
        r"^\s*(execute|launch)\b",
        r"\bexecute\s+(the\s+)?(code|program|it|this|that)\b",
        r"^\s*(chalao|chalaiye)\b",  # Hinglish: run it
    ]),
    ("generate_code", 0.82, [
        r"\b(write|generate|create|make|build)\s+(me\s+)?(a\s+|an\s+|some\s+)?"
        r"(python\s+)?(program|code|script|function|app|application|game|tool)\b",
        r"\bi\s+want\s+(some\s+)?(python\s+)?code\b",
        r"\b(write|generate|create|make|build)\s+(a\s+)?program\b",
    ]),
    ("insert_code", 0.82, [
        r"^\s*insert\b",
        r"^\s*(add|append|type)\b",
        r"\binsert\s+(print|a\s+line|the\s+line|code|variable|function)\b",
        r"\bput\b.*\bin\s+(the\s+)?(editor|code)\b",
    ]),
]

# Pre-compile for clarity and a little speed. Structure: (intent, confidence,
# [compiled patterns]).
_COMPILED_RULES: List[Tuple[str, float, List["re.Pattern[str]"]]] = [
    (intent, conf, [re.compile(p, re.IGNORECASE) for p in patterns])
    for intent, conf, patterns in _RULES
]


def _rule_classify(text: str) -> Tuple[str, float]:
    """Classify ``text`` into (intent, confidence) using the local demo rules.

    Pure, deterministic, side-effect free. Returns ("unknown", low) when nothing
    matches. This is the stand-in for what a future on-device OpenVINO model
    would do.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return "unknown", UNKNOWN_CONFIDENCE

    for intent, base_conf, patterns in _COMPILED_RULES:
        hits = sum(1 for pat in patterns if pat.search(cleaned))
        if hits:
            # A small, bounded bump for multiple matching cues so the number
            # reads like a classifier score without ever overstating certainty.
            confidence = min(0.97, round(base_conf + 0.03 * (hits - 1), 2))
            return intent, confidence

    return "unknown", UNKNOWN_CONFIDENCE


def classify_local_intent(text: str) -> Dict[str, object]:
    """Classify a voice/text command into a coarse intent, the local-demo way.

    The classification itself always comes from the deterministic local rules in
    this module — no model is bundled or executed. The ``available``/``source``/
    ``note`` fields simply report whether the OpenVINO runtime is importable, to
    show the intended edge-first integration point.

    Returns a JSON-serialisable dict, e.g.::

        {
            "available": False,
            "source": "local_rules",
            "intent": "insert_code",
            "confidence": 0.82,
            "note": "OpenVINO runtime not installed; using local demo rules.",
        }
    """
    intent, confidence = _rule_classify(text)

    if ov is None:
        available = False
        source = "local_rules"
        note = "OpenVINO runtime not installed; using local demo rules."
    else:
        available = True
        source = "openvino_ready"
        note = "OpenVINO runtime detected; model path not configured."

    return {
        "available": available,
        "source": source,
        "intent": intent,
        "confidence": confidence,
        "note": note,
    }


def openvino_available() -> bool:
    """True if the OpenVINO runtime was importable at module load time."""
    return ov is not None
