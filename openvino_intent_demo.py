
from __future__ import annotations

import re
from typing import Dict, List, Tuple

try:  # pragma: no cover - exercised only on machines with OpenVINO installed
    import openvino as ov  # type: ignore
except Exception:  # noqa: BLE001 - any import failure means "not available"
    ov = None


INTENT_LABELS: Tuple[str, ...] = (
    "insert_code",
    "generate_code",
    "stop_speaking",
    "concept_question",
    "run_code",
    "unknown",
)

UNKNOWN_CONFIDENCE = 0.25

_RULES: List[Tuple[str, float, List[str]]] = [
    ("stop_speaking", 0.90, [
        r"\bstop\s+(speaking|talking|reading|narrat\w*|the\s+voice|voice)\b",
        r"\b(be\s+quiet|shut\s+up|silence|hush|quiet\s+please)\b",
        r"^\s*(stop|quiet|silence)\s*[.!]?\s*$",
        r"\bstop\s+(it|now|please)\b",
        r"\b(mute|stop\s+the\s+sound|stop\s+sound)\b",
        r"\b(chup|bolna\s+band)\b",
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

_COMPILED_RULES: List[Tuple[str, float, List["re.Pattern[str]"]]] = [
    (intent, conf, [re.compile(p, re.IGNORECASE) for p in patterns])
    for intent, conf, patterns in _RULES
]


def _rule_classify(text: str) -> Tuple[str, float]:
    cleaned = (text or "").strip()
    if not cleaned:
        return "unknown", UNKNOWN_CONFIDENCE

    for intent, base_conf, patterns in _COMPILED_RULES:
        hits = sum(1 for pat in patterns if pat.search(cleaned))
        if hits:
            confidence = min(0.97, round(base_conf + 0.03 * (hits - 1), 2))
            return intent, confidence

    return "unknown", UNKNOWN_CONFIDENCE


def classify_local_intent(text: str) -> Dict[str, object]:
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
    return ov is not None
