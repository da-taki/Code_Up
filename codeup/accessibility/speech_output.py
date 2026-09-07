
from __future__ import annotations

import re


def sanitize_speech_text(text: str) -> str:
    value = "" if text is None else str(text)
    value = re.sub(r"```[a-zA-Z0-9_-]*\s*", " ", value)
    value = value.replace("```", " ")
    value = value.replace("`", "")
    value = re.sub(r"(\*\*|__)(.*?)\1", r"\2", value)
    value = re.sub(r"(?<!\w)([*_])([^*_]+)\1(?!\w)", r"\2", value)
    value = re.sub(r"(?m)^\s*[-*+]\s+", "", value)
    value = re.sub(r"(?m)^\s*\d+\.\s+", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    # Strip markdown blockquote/heading markers only at the start of a line, not a
    # bare '>' anywhere -- a bare '>' is also Python's greater-than operator, and
    # spoken condition text (e.g. "the condition n > 5") needs to keep it readable.
    value = re.sub(r"(?m)^\s*>+\s?", "", value)
    value = re.sub(r"(?m)^\s*#{1,6}\s+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def speech_response(display_text: str, *, speech_text: str = "", speak: bool = True) -> dict:
    speech = sanitize_speech_text(speech_text or display_text)
    return {
        "message": display_text,
        "speech": speech if speak else "",
        "speak": bool(speak),
    }

