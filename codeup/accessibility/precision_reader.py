"""Deterministic, on-demand PRECISION reading of exact source text.

CodeUp's normal narration (structure_tools.explain_line, state_watch, etc.)
deliberately paraphrases code for beginner comprehension and does not speak
every punctuation mark -- listening to every symbol on every line would be
exhausting. Precision Mode is the opposite: on explicit request, it reads
EXACTLY what is on a line -- every character or token, case preserved,
spaces vs tabs distinguished, punctuation named -- for proofreading a syntax
error or checking an exact value/diff.

Everything here is deterministic (a fixed regex tokenizer + a fixed
punctuation-name table) and reuses NOTHING an existing module already owns in
the wrong direction: symbolic_specs.py and static/app.js's spoken-code
normalizers map SPOKEN WORDS -> CODE CHARACTERS (for voice dictation into the
editor); this module is the opposite direction, CODE CHARACTERS -> SPOKEN
WORDS, which nothing else in the codebase already provides. No AI. No code
mutation.
"""

from __future__ import annotations

import difflib
import re
from typing import Dict, List, Optional

__all__ = [
    "read_line_exact", "read_punctuation", "spell_token", "read_char_by_char",
    "compare_exact",
]

# Character/operator -> spoken name. Longer operators are matched before their
# single-character prefixes (order matters in _TOKEN_RE below).
_SYMBOL_WORDS: Dict[str, str] = {
    "==": "equals equals", "!=": "not equals", "<=": "less than or equal",
    ">=": "greater than or equal", "//": "floor divide", "**": "power",
    "+=": "plus equals", "-=": "minus equals", "*=": "times equals", "/=": "divide equals",
    "->": "arrow",
    "=": "equals", "+": "plus", "-": "minus", "*": "times", "/": "divide",
    "%": "percent", "<": "less than", ">": "greater than",
    "(": "open paren", ")": "close paren",
    "[": "open bracket", "]": "close bracket",
    "{": "open brace", "}": "close brace",
    ":": "colon", ",": "comma", ".": "dot", ";": "semicolon",
    "_": "underscore", "#": "hash", "\\": "backslash", "|": "pipe",
    "&": "ampersand", "^": "caret", "~": "tilde", "@": "at", "!": "exclamation",
    "'": "single quote", '"': "double quote", "`": "backtick",
}
# Longest symbols first so the regex prefers "==" over "=" + "=".
_SYMBOLS_BY_LENGTH = sorted(_SYMBOL_WORDS, key=len, reverse=True)
_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*"          # identifiers/keywords
    r"|\d+\.\d+|\d+"                    # numbers
    r"|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""  # quoted strings (simple)
    r"|" + "|".join(re.escape(s) for s in _SYMBOLS_BY_LENGTH) +
    r"|\s+|."
)

_MAX_SPOKEN_CHARS = 900


def _clamp_line(lines: List[str], line: Optional[int]) -> int:
    n = line if isinstance(line, int) and line >= 1 else 1
    return min(n, len(lines)) if lines else 1


def _leading_whitespace_note(raw: str) -> str:
    stripped = raw.lstrip(" \t")
    leading = raw[:len(raw) - len(stripped)]
    if not leading:
        return ""
    if "\t" in leading:
        n = leading.count("\t")
        return f"{n} tab{'s' if n != 1 else ''} of indentation, then "
    n = len(leading)
    return f"{n} space{'s' if n != 1 else ''} of indentation, then "


def _tokenize_line(text: str) -> List[str]:
    return _TOKEN_RE.findall(text)


def _speak_token(tok: str) -> Optional[str]:
    """One token -> its spoken form, or None for a whitespace run (rendered as
    an explicit 'space' marker by the caller instead)."""
    if tok.isspace():
        return None
    if tok in _SYMBOL_WORDS:
        return _SYMBOL_WORDS[tok]
    if (tok.startswith("'") and tok.endswith("'")) or (tok.startswith('"') and tok.endswith('"')):
        quote_word = "single quote" if tok[0] == "'" else "double quote"
        inner = tok[1:-1]
        return f"{quote_word} {inner} {quote_word}" if inner else f"{quote_word} {quote_word}"
    return tok  # identifier, keyword, or number: spoken as-is, case preserved


def read_line_exact(code: str, line: Optional[int]) -> str:
    """'read exact line' / 'read this line exactly': every token on the line,
    in order, with explicit 'space' markers and named punctuation -- case and
    whitespace preserved, nothing paraphrased. Very long lines are capped so
    this can't produce runaway speech; say 'read punctuation on this line' for
    just the symbols on a long line instead."""
    lines = (code or "").splitlines()
    if not lines:
        return "There is no code yet."
    n = _clamp_line(lines, line)
    raw = lines[n - 1]
    if not raw.strip():
        return f"Line {n} is blank." if not raw else f"Line {n} is blank, {len(raw)} leading whitespace characters."
    indent_note = _leading_whitespace_note(raw)
    body = raw.lstrip(" \t")
    spoken_tokens = []
    for tok in _tokenize_line(body):
        spoken = _speak_token(tok)
        spoken_tokens.append(spoken if spoken is not None else "space")
    spoken = ", ".join(t for t in spoken_tokens if t)
    if len(spoken) > _MAX_SPOKEN_CHARS:
        spoken = spoken[:_MAX_SPOKEN_CHARS] + ", ... line truncated. Say 'read punctuation on this line' for just the symbols."
    return f"Line {n}: {indent_note}{spoken}."


def read_punctuation(code: str, line: Optional[int]) -> str:
    """'read punctuation' / 'read punctuation on this line': ONLY the named
    symbols on the line, in order -- skips identifiers and numbers, for when
    you just need to check which operators/brackets are present."""
    lines = (code or "").splitlines()
    if not lines:
        return "There is no code yet."
    n = _clamp_line(lines, line)
    raw = lines[n - 1]
    body = raw.lstrip(" \t")
    symbols = [_SYMBOL_WORDS[tok] for tok in _tokenize_line(body) if tok in _SYMBOL_WORDS]
    if not symbols:
        return f"Line {n} has no punctuation characters."
    return f"Line {n} punctuation, in order: " + ", ".join(symbols) + "."


def spell_token(code: str, line: Optional[int]) -> str:
    """'spell current token' / 'spell this token' / 'read current token': the
    first identifier, keyword, or number on the line, spelled letter by
    letter (capitals called out). CodeUp does not track cursor COLUMN today
    (only the line), so "current token" means the first meaningful token on
    the current line -- an honest simplification, not a guess at a column
    that was never sent."""
    lines = (code or "").splitlines()
    if not lines:
        return "There is no code yet."
    n = _clamp_line(lines, line)
    body = lines[n - 1].lstrip(" \t")
    token = next((t for t in _tokenize_line(body)
                  if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|\d+\.\d+|\d+", t)), None)
    if token is None:
        return f"Line {n} has no word or number to spell."
    letters = []
    for ch in token:
        if ch.isupper():
            letters.append(f"capital {ch.lower()}")
        else:
            letters.append(ch)
    return f"{token}, spelled: " + ", ".join(letters) + "."


def read_char_by_char(code: str, line: Optional[int], max_chars: int = 120) -> str:
    """'read character by character': every character on the line, one at a
    time (not grouped into tokens) -- the most granular precision reading,
    for proofreading exact punctuation/case in a short, tricky line. Capped
    at max_chars so a long line can't produce uncontrolled speech."""
    lines = (code or "").splitlines()
    if not lines:
        return "There is no code yet."
    n = _clamp_line(lines, line)
    raw = lines[n - 1]
    if not raw:
        return f"Line {n} is blank."
    truncated = len(raw) > max_chars
    chars = raw[:max_chars]
    spoken = []
    for ch in chars:
        if ch == " ":
            spoken.append("space")
        elif ch == "\t":
            spoken.append("tab")
        elif ch in _SYMBOL_WORDS:
            spoken.append(_SYMBOL_WORDS[ch])
        elif ch.isupper():
            spoken.append(f"capital {ch.lower()}")
        else:
            spoken.append(ch)
    result = f"Line {n}, character by character: " + ", ".join(spoken) + "."
    if truncated:
        result += f" (truncated at {max_chars} characters)"
    return result


def compare_exact(before: str, after: str) -> str:
    """'compare exact' / 'what changed character by character': a
    character-level diff of two line strings (not the line-level diff
    audio_diff.py already owns) -- for spotting a single changed/added/
    removed character, e.g. a typo fix."""
    before, after = before or "", after or ""
    if before == after:
        return "No character-level difference."
    matcher = difflib.SequenceMatcher(None, before, after)
    parts = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_seg, new_seg = before[i1:i2], after[j1:j2]
        if tag == "replace":
            parts.append(f"at position {i1}, \"{old_seg}\" changed to \"{new_seg}\"")
        elif tag == "delete":
            parts.append(f"at position {i1}, \"{old_seg}\" was removed")
        elif tag == "insert":
            parts.append(f"at position {i1}, \"{new_seg}\" was added")
    return "; ".join(parts) + "." if parts else "No character-level difference."
