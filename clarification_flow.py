"""Multi-turn clarification for symbol-pattern requests.

A blind beginner says a messy pattern request ("make a five by five thing with
the third line different"). We extract the slots we can (rows, cols, symbol,
special row/count), ask ONE specific question for what is missing, remember a
structured pending object, and then parse the user's answer ("stars, six on the
third line" / "it should have 6 rather than 5") back into those slots. When the
slots are complete we hand a clean, canonical command to the deterministic
exact-symbol generator — so Key 2 never has to invent the pattern.

Pure/deterministic and reuses the exact-symbol parser's own slot helpers, so the
answer always round-trips back through the same deterministic generator.
"""

import re
from typing import Dict, List, Optional

from symbolic_specs import (
    _dimensions,
    _find_symbol,
    _row_exception,
    normalize_spoken_symbols,
)

_SYMBOL_WORD = {"*": "star", "#": "hash", "+": "plus", "-": "dash", "/": "slash", "%": "percent"}
_DIFF_RE = re.compile(r"\b(different|special|changed|other|unique|more|fewer|extra|odd)\b", re.IGNORECASE)
_PATTERN_WORD_RE = re.compile(
    r"\b(pattern|patterns|star|stars|asterisks?|hash|hashes|hashtags?|symbol|symbols|"
    r"shape|shapes|thing|things|cube|square|triangle|diamond|"
    r"grid\s+of\s+(?:stars|hashes|symbols|dots))\b",
    re.IGNORECASE,
)


def has_pattern_intent(text: str) -> bool:
    """True when the text reads like a symbol/shape pattern (not, say, a grid of
    numbers) — used so we only clarify a missing symbol for real patterns."""
    return bool(_PATTERN_WORD_RE.search(text or ""))
_LINE_NUM_RE = re.compile(r"\b(\d+)\s+(?:line|row)\b")
_NUM_LINE_RE = re.compile(r"\b(?:line|row)\s+(\d+)\b")
_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
             6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}


def _ordinal(n: Optional[int]) -> str:
    if not n:
        return "special"
    return _ORDINALS.get(n, f"{n}th")


def _symbol_word(symbol: Optional[str], plural: bool = False) -> str:
    word = _SYMBOL_WORD.get(symbol or "", "symbol")
    return word + ("s" if plural else "")


def extract_pattern_slots(text: str) -> Dict[str, Optional[int]]:
    """Best-effort slots from a raw utterance: rows, cols, symbol, special_row,
    special_count (any may be None)."""
    norm = normalize_spoken_symbols(text)
    rows, cols = _dimensions(norm)
    symbol = _find_symbol(norm)
    special_row, special_count = _row_exception(norm)
    if special_row is None:
        line = _LINE_NUM_RE.search(norm) or _NUM_LINE_RE.search(norm)
        if line and _DIFF_RE.search(norm):
            special_row = int(line.group(1))
    return {
        "rows": rows, "cols": cols, "symbol": symbol,
        "special_row": special_row, "special_count": special_count,
    }


def missing_pattern_slots(slots: Dict) -> List[str]:
    missing: List[str] = []
    if not slots.get("symbol"):
        missing.append("symbol")
    if slots.get("special_row") is not None and slots.get("special_count") is None:
        missing.append("special_count")
    return missing


def is_pattern_clarifiable(slots: Dict) -> bool:
    """A real pattern (has a size) that is missing a needed slot."""
    return bool(slots.get("rows")) and bool(slots.get("cols")) and bool(missing_pattern_slots(slots))


def pattern_ready(slots: Dict) -> bool:
    if not (slots.get("rows") and slots.get("cols") and slots.get("symbol")):
        return False
    if slots.get("special_row") is not None and slots.get("special_count") is None:
        return False
    return True


def pattern_question(slots: Dict) -> str:
    missing = missing_pattern_slots(slots)
    row = slots.get("special_row")
    if "symbol" in missing and "special_count" in missing:
        return f"What symbol should I use, and how many symbols should the {_ordinal(row)} line have?"
    if "special_count" in missing:
        return f"How many {_symbol_word(slots.get('symbol'), plural=True)} should the {_ordinal(row)} line have?"
    if "symbol" in missing:
        return "What symbol should I use, stars or hashes?"
    return "Could you give me a little more detail about the pattern?"


def question_facts(question: str) -> List[str]:
    """Concrete facts an AI rephrase of ``question`` must preserve (only what the
    question actually states — a size and/or an ordinal line reference)."""
    facts: List[str] = []
    size = re.search(r"\b(\d+)\s+by\s+(\d+)\b", question or "")
    if size:
        facts.append(f"{size.group(1)} by {size.group(2)}")
    line = re.search(
        r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(line|row)\b",
        question or "", re.IGNORECASE,
    )
    if line:
        facts.append(f"{line.group(1)} {line.group(2)}")
    return facts


def parse_pattern_answer(text: str, slots: Dict) -> Dict[str, Optional[int]]:
    """Merge a user's answer ("stars, six on the third line", "6 rather than 5")
    into the known slots."""
    norm = normalize_spoken_symbols(text)
    new = dict(slots)

    symbol = _find_symbol(norm)
    if symbol:
        new["symbol"] = symbol

    row = _LINE_NUM_RE.search(norm) or _NUM_LINE_RE.search(norm)
    if row:
        new["special_row"] = int(row.group(1))

    # "6 rather than 5" / "six instead of five" -> the *new* value (6).
    swap = re.search(r"\b(\d+)\s+(?:rather than|instead of|not|over|in place of)\s+\d+", norm)
    if swap:
        new["special_count"] = int(swap.group(1))
    else:
        nums = [int(n) for n in re.findall(r"\d+", norm)]
        used = {new.get("special_row"), new.get("rows"), new.get("cols")}
        candidates = [n for n in nums if n not in used] or [n for n in nums if n != new.get("special_row")]
        if candidates and new.get("special_count") is None:
            new["special_count"] = candidates[0]
    return new


def build_pattern_command(slots: Dict) -> str:
    """Canonical command string for the deterministic exact-symbol generator."""
    rows, cols = slots["rows"], slots["cols"]
    word = _symbol_word(slots.get("symbol"))
    command = f"make a {rows} by {cols} {word} pattern"
    if slots.get("special_row") is not None and slots.get("special_count") is not None:
        command += f" where row {slots['special_row']} has {slots['special_count']} {word}s"
    return command
