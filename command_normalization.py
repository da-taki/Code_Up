"""Command transcript normalization for CodeUp voice routing.

This layer normalizes common ASR mistakes before intent parsing. It only handles
the command transcript; editor code is never passed through this module.
"""

from __future__ import annotations

import re
from typing import Dict


_REPLACEMENTS = (
    # Misheard stop commands. ASR often drops the leading "s" of "stop" or hears
    # "listing"/"listen" for "listening". These are safety-critical (they silence
    # runaway speech / mic), so we repair them before intent parsing. The \btop
    # boundary never matches the "top" inside "stop", so "stop listening" is left
    # untouched.
    (re.compile(r"\btop\s+everything\b", re.IGNORECASE), "stop everything"),
    (re.compile(r"\btop\s+listening\b", re.IGNORECASE), "stop listening"),
    (re.compile(r"\bstop\s+listing\b", re.IGNORECASE), "stop listening"),
    (re.compile(r"\bstop\s+listen\b", re.IGNORECASE), "stop listening"),
    (re.compile(r"\bleast\s+bookmarks?\b", re.IGNORECASE), "list bookmarks"),
    (re.compile(r"\bmakeup\s+project\s+report\b", re.IGNORECASE), "make a project report"),
    (re.compile(r"\brequriements\b", re.IGNORECASE), "requirements"),
    (re.compile(r"\brequirement\b", re.IGNORECASE), "requirements"),
    (re.compile(r"\bteach\s+me\s+this\s+scored\b", re.IGNORECASE), "teach me this code"),
    (re.compile(r"\bwhat\s+line\s+control\s+the\s+loop\b", re.IGNORECASE), "what line controls the loop"),
    (re.compile(r"\badd\s+dis\b", re.IGNORECASE), "add this"),
    (re.compile(r"\breplace\s+dis\b", re.IGNORECASE), "replace this"),
    (re.compile(r"\bdo\s+dis\b", re.IGNORECASE), "do this"),
    (re.compile(r"\btrends\b", re.IGNORECASE), "prints"),
    (re.compile(r"\bprince\b", re.IGNORECASE), "prints"),
    (re.compile(r"\bslope\b", re.IGNORECASE), "loop"),
    (re.compile(r"\b(bookmark|mark|go to|jump to|find|read|open)\s+((?:this|the)\s+)?loops\b", re.IGNORECASE), r"\1 \2loop"),
    (re.compile(r"^\s*remove\s+(?:the\s+)?error\s*$", re.IGNORECASE), "fix this code"),
)


def normalize_command_transcript(text: str) -> Dict[str, object]:
    """Return raw + normalized transcript data for command routing."""
    raw = "" if text is None else str(text)
    normalized = " ".join(raw.strip().split())
    for pattern, replacement in _REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)
    normalized = " ".join(normalized.strip().split())
    return {
        "raw_transcript": raw,
        "normalized_text": normalized,
        "changed": normalized != raw.strip(),
    }
