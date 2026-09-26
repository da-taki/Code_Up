
from __future__ import annotations

import re

_PYTHON_TOKENS = [
    ("**", " power "),
    ("//", " floor divide "),
    (">=", " greater than or equal to "),
    ("<=", " less than or equal to "),
    ("==", " equals equals "),
    ("!=", " not equals "),
    ("(", " open parenthesis "),
    (")", " close parenthesis "),
    ("[", " open bracket "),
    ("]", " close bracket "),
    ("{", " open brace "),
    ("}", " close brace "),
    (":", " colon "),
    (",", " comma "),
    ("=", " equals "),
    (">", " greater than "),
    ("<", " less than "),
    ("+", " plus "),
    ("-", " minus "),
    ("*", " times "),
    ("/", " divide "),
    ("%", " modulo "),
]


def python_code_to_speech(code: str, *, precision: bool = False) -> str:
    """Speak Python code as code, not prose."""
    lines = str(code or "").splitlines() or [str(code or "")]
    spoken_lines = []
    for line in lines:
        raw = line.rstrip("\n")
        if not raw.strip():
            spoken_lines.append("blank line")
            continue
        spaces = len(raw) - len(raw.lstrip(" "))
        prefix = ""
        if spaces:
            prefix = f"indent {spaces} spaces, " if precision else "indented, "
        body = raw.strip()
        out = []
        quote = ""
        i = 0
        while i < len(body):
            ch = body[i]
            if ch in {"'", '"'}:
                name = "single quote" if ch == "'" else "double quote"
                out.append(f" {name} ")
                quote = "" if quote == ch else ch
                i += 1
                continue
            matched = False
            if not quote and ch == ".":
                numeric = i > 0 and body[i - 1].isdigit() and i + 1 < len(body) and body[i + 1].isdigit()
                out.append(" point " if numeric else " dot ")
                i += 1
                continue
            if not quote:
                for token, word in _PYTHON_TOKENS:
                    if body.startswith(token, i):
                        out.append(f" {word.strip()} ")
                        i += len(token)
                        matched = True
                        break
            if matched:
                continue
            out.append(ch)
            i += 1
        spoken = " ".join("".join(out).split())
        spoken_lines.append(prefix + spoken)
    return ". ".join(part for part in spoken_lines if part).strip()


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
