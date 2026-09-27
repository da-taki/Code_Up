"""Natural variable and container creation: slot filling for CREATE_VARIABLE.

"make variable score 95", "Variable insert karo with value taki", "marks ki
list banao 90 80 95", "make a library with key value pairs, Paris expensive,
Amsterdam cheap" are one structured operation with three slots:

    name        a valid, non-keyword Python identifier ("first name" -> first_name)
    value_type  string / integer / float / boolean / none / list / tuple / set /
                dictionary / variable / expression
    value       a small value TREE (never Python source from the learner)

The deterministic parser fills whichever slots the words give, reports the
missing ones (asked one at a time by the caller), and flags utterances it
could only partly read as ``partial`` so the caller can ask the semantic
resolver instead of guessing. The semantic resolver's JSON goes through
``request_from_semantic`` - the same tree, the same limits.

Python is only ever produced by ``render`` from a validated tree, and the
result is re-parsed and checked against an AST allowlist. Nothing here uses
eval/exec.

Value trees are JSON-safe lists so they survive the session store:
    ["str", s] ["int", n] ["float", f] ["bool", b] ["none"] ["name", id]
    ["expr", src] ["list", [..]] ["tuple", [..]] ["set", [..]]
    ["dict", [[key_tree, value_tree], ..]]
"""

from __future__ import annotations

import ast
import json
import keyword
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from codeup.commands import intent_repair

MAX_ITEMS = 50
MAX_DEPTH = 3
MAX_STRING = 200
MAX_NAME_LEN = 40
MAX_NAME_WORDS = 3

VALUE_TYPES = ("string", "integer", "float", "boolean", "none", "list", "tuple", "set", "dictionary",
               "variable", "expression")
CONTAINERS = ("list", "tuple", "set", "dictionary")

# Same policy as renaming (deterministic_code_tools._SHADOWED_BUILTINS): a
# variable must not hide these builtins. Each has a beginner-friendly swap.
SHADOWED_BUILTINS = {"list": "items", "dict": "data", "str": "text", "int": "number", "sum": "total",
                     "input": "user_input", "print": "message"}

_NODE_KIND = {"str": "string", "int": "integer", "float": "float", "bool": "boolean", "none": "none",
              "name": "variable", "expr": "expression", "list": "list", "tuple": "tuple", "set": "set",
              "dict": "dictionary"}

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

_TYPE_WORDS = {
    "variable": "variable", "var": "variable", "varible": "variable", "veriable": "variable",
    "variabel": "variable", "vairable": "variable",
    "list": "list", "array": "list",
    "tuple": "tuple", "tupple": "tuple",
    "set": "set",
    "dictionary": "dictionary", "dict": "dictionary", "dictonary": "dictionary", "dictionery": "dictionary",
    "dictionaries": "dictionary", "mapping": "dictionary", "hashmap": "dictionary",
}
_KV_PHRASES = (("key", "and", "value", "pairs"), ("keys", "and", "values"), ("key", "value", "pairs"),
               ("key", "value", "pair"), ("key-value", "pairs"), ("key-value", "pair"), ("key", "values"),
               ("key", "value"))
_DICT_TOKEN = "\x00dict"

_CREATE_VERBS = {"make", "create", "insert", "add", "declare", "define", "put", "new", "initialize",
                 "initialise", "store", "build", "set", "assign", "banao", "bana", "banado", "banaao", "banaye",
                 "banaiye", "banaen", "bnao", "rakho", "rakh", "rakhdo", "change", "update"}
# "make score 95" / "set age to 16": verbs that may create without the word variable.
_BARE_VERBS = {"make", "create", "set", "assign", "declare", "define", "initialize", "initialise", "banao",
               "bana", "banado", "rakho", "change", "update"}
_AUX = {"karo", "kar", "kardo", "do", "dena", "de", "dijiye", "please", "hai", "ho", "na", "me", "now", "us"}
_ARTICLES = {"a", "an", "the", "ek", "new"}
_CONNECTORS = {"ka", "ki", "ke", "se", "aur", "and", "type", "called", "named"}
_EMPTY_WORDS = {"empty", "khali", "khaali", "blank"}
_FILLERS = {"please", "pls", "acha", "achha", "accha", "ok", "okay", "so", "now", "hey", "bhai", "yaar",
            "just", "alright", "well", "um", "uh", "hmm", "then", "also", "and", "let's", "lets"}
_LEAD_PHRASES = (("let", "us"), ("can", "you"), ("could", "you"), ("would", "you"), ("will", "you"),
                 ("i", "want", "to"), ("i", "want"), ("i'd", "like", "to"), ("i", "would", "like", "to"))
_TRAIL_AUX = {"rakho", "rakh", "rakhdo", "karo", "kar", "kardo", "do", "dena", "de", "dijiye", "please",
              "hai", "ho", "na", "now", "banao", "bana", "banado"}

# Words that are never a requested name: pronouns, question/relative words,
# Hinglish function words. Seeing one where a name should be means the
# sentence is free-form, so the semantic resolver reads it instead.
_NOT_A_NAME = {"it", "this", "that", "these", "those", "which", "who", "what", "whose", "where", "when", "to",
               "from", "of", "in", "into", "with", "by", "using", "containing", "me", "my", "i", "we", "you",
               "chahiye", "mujhe", "hume", "humein", "main", "mai", "hum", "tum", "aap", "dete", "hain", "jiska",
               "jiski", "jiske", "uski", "uska", "iska", "iski", "isme", "usme", "wala", "wali", "kya", "ko",
               "bhi", "toh", "sure", "some", "any", "all", "value", "values", "items"}
# Things learners MAKE that are not variables ("make a calculator").
_CODE_NOUNS = {"function", "functions", "class", "loop", "loops", "program", "programs", "code", "calculator",
               "game", "app", "file", "project", "method", "if", "condition", "print", "statement", "line",
               "comment", "comments", "input", "while", "module", "import", "script", "test", "report", "lesson",
               "tutorial", "quiz", "snippet", "block", "error", "bug", "pattern", "pyramid", "star", "table",
               "output", "text", "font", "voice", "speech", "sound", "noise", "changes", "change", "sense",
               "breakpoint", "breakpoints", "inputs", "speed", "volume", "theme", "mode", "language", "zoom",
               "rate", "verbosity", "contrast", "level", "note", "notes", "summary", "map", "copy", "backup"}
# A "value" containing these is a description of a program, not literal data
# ("a list of even numbers", "a dictionary that maps names to ages").
_DESCRIPTION_WORDS = {"numbers", "random", "even", "odd", "squares", "square", "cubes", "prime", "primes",
                      "first", "last", "till", "until", "through", "between", "print", "prints", "printing",
                      "that", "which", "who", "using", "input", "inputs", "user", "users", "loop", "sorted",
                      "sort", "reverse", "reversed", "multiples", "factorial", "fibonacci", "every", "each",
                      "integers", "strings", "maps", "map", "program", "function", "bigger", "smaller",
                      "larger", "louder", "quieter", "faster", "slower", "better", "simpler", "shorter",
                      "longer", "readable", "clearer", "clear", "instead", "code"}
_PARTICLES = {"in", "on", "off", "out", "up"}
_STATEMENT_WORDS = {"print", "input", "if", "while", "return", "def", "import", "loop", "function", "class", "elif",
                    "else", "try", "comment"}
_PRONOUNS = {"it", "this", "that", "these", "those", "my", "me", "i", "we", "you", "isko", "ise", "usko", "ye",
             "yeh"}
_GREETINGS = {"hello", "hi", "hey", "welcome", "bye", "goodbye", "thanks", "namaste", "congratulations"}

_TOKEN_RE = re.compile(r'"[^"]*"|“[^”]*”|-?\d+(?:\.\d+)?|[^\W\d][\w\'’-]*|[,;:=&+*/]')


@dataclass
class Tok:
    text: str
    low: str


def _tokens(text: str) -> List[Tok]:
    raw = str(text or "").strip()
    raw = re.sub(r"[.!?]+\s*$", "", raw)
    out: List[Tok] = []
    for match in _TOKEN_RE.finditer(raw):
        word = match.group(0)
        if not word.startswith(("\"", "“")):
            word = word.strip("'’")
            if not word:
                continue
        out.append(Tok(word, word.lower().replace("’", "'")))
    return out


def _collapse_kv(toks: List[Tok]) -> List[Tok]:
    out: List[Tok] = []
    i = 0
    while i < len(toks):
        for phrase in _KV_PHRASES:
            n = len(phrase)
            if tuple(t.low for t in toks[i:i + n]) == phrase:
                out.append(Tok("key value pairs", _DICT_TOKEN))
                i += n
                break
        else:
            out.append(toks[i])
            i += 1
    return out


def _strip_lead(toks: List[Tok]) -> List[Tok]:
    changed = True
    while toks and changed:
        changed = False
        if toks[0].low in _FILLERS or toks[0].low == ",":
            toks, changed = toks[1:], True
            continue
        for phrase in _LEAD_PHRASES:
            if tuple(t.low for t in toks[:len(phrase)]) == phrase:
                toks, changed = toks[len(phrase):], True
                break
    return toks


def _strip_trail(toks: List[Tok]) -> List[Tok]:
    while toks and (toks[-1].low in _TRAIL_AUX or toks[-1].low in {",", "aur", "and"}):
        toks = toks[:-1]
    return toks


# ---------------------------------------------------------------------------
# Scalars
# ---------------------------------------------------------------------------

_UNITS = {w: n for w, n in intent_repair._NUMBER_WORDS.items() if n < 20}
_TENS = {w: n for w, n in intent_repair._NUMBER_WORDS.items() if 20 <= n < 100}


def _word_number(words: Sequence[str]) -> Optional[int]:
    if not words:
        return None
    total, current, seen = 0, 0, False
    for word in words:
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word == "thousand":
            total += (current or 1) * 1000
            current = 0
        else:
            return None
        seen = True
    return total + current if seen else None


def _number(words: Sequence[str]) -> Optional[Any]:
    words = [w for w in words if w]
    if not words:
        return None
    sign = 1
    if words[0] in ("minus", "negative") and len(words) > 1:
        sign, words = -1, words[1:]
    joined = " ".join(words)
    if re.fullmatch(r"-?\d+", joined):
        return sign * int(joined)
    if re.fullmatch(r"-?\d+\.\d+", joined):
        return sign * float(joined)
    if "point" in words:
        idx = words.index("point")
        whole = _number(words[:idx]) if idx else 0
        digits = words[idx + 1:]
        frac = "".join(str(_UNITS[d]) if d in _UNITS and _UNITS[d] < 10 else (d if d.isdigit() else "?")
                       for d in digits)
        if isinstance(whole, int) and frac and "?" not in frac:
            return sign * float(f"{abs(whole)}.{frac}") * (1 if whole >= 0 else -1)
        return None
    value = _word_number(words)
    return None if value is None else sign * value


def _is_literal(tok: Tok) -> bool:
    return (_number([tok.low]) is not None or tok.low in ("true", "false", "none", "null")
            or tok.text.startswith(("\"", "“")))


_OPERATORS = {"plus": "+", "+": "+", "minus": "-", "times": "*", "*": "*", "into": "*", "multiplied": "*",
              "divided": "/", "/": "/", "over": "/"}


def _expression(words: List[Tok], names: Set[str]) -> Optional[str]:
    """"score times 2" -> "score * 2" when every operand is a number or a
    name the program defines and at least one operand is such a name."""
    parts: List[str] = []
    expect_operand, used_name, i = True, False, 0
    while i < len(words):
        low = words[i].low
        if expect_operand:
            j = i + 1
            if low in ("minus", "negative") and j < len(words):
                j += 1
            number = _number([w.low for w in words[i:j]])
            if number is not None:
                parts.append(repr(number))
            elif low in names and re.fullmatch(r"[A-Za-z_]\w*", words[i].text):
                parts.append(words[i].text)
                used_name = True
                j = i + 1
            else:
                return None
            i, expect_operand = j, False
        else:
            if low not in _OPERATORS:
                return None
            parts.append(_OPERATORS[low])
            i += 1
            if low in ("multiplied", "divided") and i < len(words) and words[i].low == "by":
                i += 1
            expect_operand = True
    if expect_operand or len(parts) < 3 or not used_name:
        return None
    return " ".join(parts)


def _unquote(text: str) -> str:
    return text[1:-1] if len(text) >= 2 and text[0] in "\"“" else text


def _scalar(words: List[Tok], names: Set[str], *, display: bool = False) -> Optional[list]:
    """A value tree for spoken words: number / bool / None / known name /
    expression, otherwise the words as a string (never a new identifier)."""
    words = [w for w in words if w.low not in {",", ";"}]
    if not words:
        return None
    lows = [w.low for w in words]
    if len(words) == 1 and words[0].text.startswith(("\"", "“")):
        return ["str", _unquote(words[0].text)[:MAX_STRING]]
    number = _number(lows)
    if number is not None:
        return ["float", number] if isinstance(number, float) else ["int", number]
    if lows in (["true"], ["sach"]):
        return ["bool", True]
    if lows in (["false"], ["jhooth"], ["jhoot"]):
        return ["bool", False]
    if lows in (["none"], ["null"]):
        return ["none"]
    if len(words) == 2 and lows[0] == "variable" and lows[1] in names:
        return ["name", words[1].text]
    if len(words) == 1 and words[0].text in names and re.fullmatch(r"[A-Za-z_]\w*", words[0].text):
        return ["name", words[0].text]
    expression = _expression(words, names)
    if expression:
        return ["expr", expression]
    text = " ".join(w.text for w in words)
    text = re.sub(r"\s+([,;:])", r"\1", text)
    if display and (len(words) > 1 or lows[0] in _GREETINGS):
        text = intent_repair.display_text(text)
    return ["str", text[:MAX_STRING]]


def _key(words: List[Tok]) -> Optional[list]:
    if not words:
        return None
    number = _number([w.low for w in words])
    if isinstance(number, int):
        return ["int", number]
    return ["str", _unquote(" ".join(w.text for w in words))[:MAX_STRING]]


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------

def _chunks(words: List[Tok]) -> Tuple[List[List[Tok]], bool]:
    """Split on commas / "and" / "aur". Returns (chunks, had_separators)."""
    chunks: List[List[Tok]] = [[]]
    separated = False
    for tok in words:
        if tok.low in {",", ";", "and", "aur", "&"}:
            separated = True
            if chunks[-1]:
                chunks.append([])
            continue
        chunks[-1].append(tok)
    return [c for c in chunks if c], separated


def _items(words: List[Tok], names: Set[str]) -> List[list]:
    chunks, separated = _chunks(words)
    if not separated:
        chunks = [[w] for w in words if w.low not in {",", ";"}]
    return [item for item in (_scalar(c, names) for c in chunks) if item is not None]


def _pair_tokens(chunk: List[Tok]) -> List[Tok]:
    return [t for t in chunk if t.low not in {"is", "hai", ":", "=", "->", "equals", "means", "ka", "ki"}]


def _pairs(words: List[Tok], names: Set[str]) -> Tuple[Optional[List[list]], str]:
    """Key/value pairs from "Paris expensive, Amsterdam cheap" or
    "apples 50 bananas 30"; "Taki marks 95, Aman marks 88" nests one level."""
    chunks, separated = _chunks(words)
    pairs: List[list] = []
    if separated and len(chunks) >= 1 and all(len(_pair_tokens(c)) >= 2 for c in chunks):
        for chunk in chunks:
            toks = _pair_tokens(chunk)
            key = _key(toks[:1])
            rest = toks[1:]
            if len(rest) >= 2 and len(rest) % 2 == 0 and all(_is_literal(t) for t in rest[1::2]) and \
                    not any(_is_literal(t) for t in rest[0::2]):
                inner, _problem = _pairs(rest, names)
                value = ["dict", inner or []]
            else:
                value = _scalar(rest, names)
            pairs.append([key, value])
        return _dedupe_pairs(pairs), ""
    if separated and len(chunks) >= 2 and any(len(_pair_tokens(c)) < 2 for c in chunks):
        return None, "odd_pairs"   # "name and age": keys without values
    flat = [t for t in _pair_tokens(words) if t.low not in {",", ";", "and", "aur", "&"}]
    if not flat:
        return [], ""
    if len(flat) % 2:
        return None, "odd_pairs"
    for i in range(0, len(flat), 2):
        pairs.append([_key([flat[i]]), _scalar([flat[i + 1]], names)])
    return _dedupe_pairs(pairs), ""


def _dedupe_pairs(pairs: List[list]) -> List[list]:
    seen: Dict[str, int] = {}
    out: List[list] = []
    for key, value in pairs:
        marker = json.dumps(key)
        if marker in seen:
            out[seen[marker]] = [key, value]
        else:
            seen[marker] = len(out)
            out.append([key, value])
    return out


def _looks_like_pairs(words: List[Tok]) -> bool:
    chunks, separated = _chunks(words)
    if separated and len(chunks) >= 2 and all(len(_pair_tokens(c)) == 2 for c in chunks):
        return not any(_is_literal(_pair_tokens(c)[0]) for c in chunks)
    flat = [t for t in words if t.low not in {",", ";", "and", "aur", "&"}]
    return (len(flat) >= 4 and len(flat) % 2 == 0 and all(_is_literal(t) for t in flat[1::2])
            and not any(_is_literal(t) for t in flat[0::2]))


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------

@dataclass
class Request:
    name: Optional[str] = None
    value: Optional[list] = None
    kind: str = "variable"             # the noun the learner used: variable/list/tuple/set/dictionary
    missing: List[str] = field(default_factory=list)
    problem: str = ""                  # keyword / builtin / invalid_name / odd_pairs / bad_value
    bad_name: str = ""
    partial: bool = False
    confidence: float = 0.95

    @property
    def value_type(self) -> str:
        return _NODE_KIND.get(self.value[0], "") if self.value else ""

    @property
    def complete(self) -> bool:
        return not self.missing and not self.problem and not self.partial and self.value is not None

    def to_pending(self) -> Dict[str, Any]:
        return {"name": self.name, "value": self.value, "kind": self.kind, "missing": list(self.missing),
                "problem": self.problem, "bad_name": self.bad_name}

    @classmethod
    def from_pending(cls, pending: Dict[str, Any]) -> "Request":
        return cls(name=pending.get("name"), value=pending.get("value"), kind=pending.get("kind") or "variable",
                   missing=list(pending.get("missing") or []), problem=pending.get("problem") or "",
                   bad_name=pending.get("bad_name") or "")

    def semantic(self) -> Dict[str, Any]:
        """The structured CREATE_VARIABLE representation (for logs / clients)."""
        return {"intent": "CREATE_VARIABLE",
                "parameters": {"name": self.name, "value_type": self.value_type or self.kind,
                               "value": to_json(self.value) if self.value is not None else None},
                "missing": list(self.missing), "confidence": self.confidence,
                "needs_clarification": bool(self.missing or self.problem)}


def _name_from(words: Sequence[str]) -> Tuple[Optional[str], str, str]:
    """(identifier, problem, offending_name)."""
    words = [w for w in words if w]
    if not words:
        return None, "missing", ""
    if len(words) > MAX_NAME_WORDS:
        return None, "invalid_name", " ".join(words)
    candidate = "_".join(re.sub(r"[^0-9a-z_]+", "_", w.lower()) for w in words)
    candidate = re.sub(r"_+", "_", candidate).strip("_")
    if not candidate or candidate[0].isdigit() or not candidate.isidentifier() or len(candidate) > MAX_NAME_LEN:
        return None, "invalid_name", " ".join(words)
    if keyword.iskeyword(candidate):
        return None, "keyword", candidate
    if candidate in SHADOWED_BUILTINS:
        return None, "builtin", candidate
    return candidate, "", ""


def _set_name(req: Request, words: Sequence[str]) -> None:
    name, problem, bad = _name_from(words)
    if name:
        req.name = name
        req.missing = [m for m in req.missing if m != "name"]
    elif problem == "missing":
        if "name" not in req.missing:
            req.missing.insert(0, "name")
    else:
        req.name, req.problem, req.bad_name = None, problem, bad
        if "name" not in req.missing:
            req.missing.insert(0, "name")


def build_value(words: List[Tok], kind: str, marker: str, names: Set[str]) -> Tuple[Optional[list], str, str]:
    """(value_tree, resolved_kind, problem) for the words after the name.

    kind is the noun said so far; marker is how the value was introduced
    ("value", "items", "with", "of", "to" or "" for none)."""
    words = [w for w in words]
    while words and (words[0].low in _TYPE_WORDS or words[0].low == _DICT_TOKEN or words[0].low in
                     {",", ":", "of", "the", "a", "an"} or words[0].low in _EMPTY_WORDS):
        low = words[0].low
        if low == _DICT_TOKEN:
            kind = "dictionary"
        elif low in _TYPE_WORDS and _TYPE_WORDS[low] != "variable":
            kind = _TYPE_WORDS[low]
        words = words[1:]
    words = _strip_trail(words)
    if not words:
        return None, kind, ""
    if kind == "variable":
        if marker == "value" or marker == "to":
            chunks, separated = _chunks(words)
            if separated and len(chunks) >= 2 and all(_number([t.low for t in c]) is not None for c in chunks):
                kind = "list"
            else:
                return _scalar(words, names, display=True), kind, ""
        elif _looks_like_pairs(words):
            kind = "dictionary"
        else:
            chunks, separated = _chunks(words)
            if (separated and len(chunks) >= 2) or (not separated and len(words) >= 3 and marker in
                                                      {"items", "with", "of"} and _expression(words, names) is None):
                kind = "list"
            else:
                return _scalar(words, names, display=True), kind, ""
    if kind == "dictionary":
        pairs, problem = _pairs(words, names)
        if pairs is None:
            return None, kind, problem
        return ["dict", pairs[:MAX_ITEMS]], kind, ""
    items = _items(words, names)[:MAX_ITEMS]
    if kind == "set":
        if any(item[0] in ("list", "dict", "set") for item in items):
            return None, kind, "bad_value"
        unique: List[list] = []
        for item in items:
            if item not in unique:
                unique.append(item)
        items = unique
    return [{"list": "list", "tuple": "tuple", "set": "set"}[kind], items], kind, ""


def _empty(kind: str) -> list:
    return {"list": ["list", []], "tuple": ["tuple", []], "set": ["set", []], "dictionary": ["dict", []]}[kind]


def _find_marker(low: List[str], verb: str, type_positions: List[int]) -> Optional[Tuple[int, int, str]]:
    markers = (
        (("and", "give", "it", "the", "value"), "value"), (("give", "it", "the", "value"), "value"),
        (("giving", "it", "the", "value"), "value"), (("give", "it", "value"), "value"),
        (("give", "the", "value"), "value"), (("set", "to"), "value"), (("that", "holds"), "value"),
        (("that", "stores"), "value"), (("that", "equals"), "value"), (("holding",), "value"),
        (("storing",), "value"),
        (("with", "the", "value"), "value"), (("with", "a", "value", "of"), "value"), (("with", "value", "of"), "value"),
        (("with", "value"), "value"), (("with", "the", "values"), "items"), (("with", "values"), "items"),
        (("with", "the", "items"), "items"), (("with", "items"), "items"), (("with", "elements"), "items"),
        (("whose", "value", "is"), "value"), (("value", "of"), "value"), (("value", "is"), "value"),
        (("value",), "value"), (("values",), "items"), (("items",), "items"), (("elements",), "items"),
        (("is", "equal", "to"), "value"), (("equal", "to"), "value"), (("equals", "to"), "value"),
        (("equals",), "value"), (("equal",), "value"), (("=",), "value"),
        (("containing",), "items"), (("contains",), "items"), (("having",), "items"), (("jisme",), "items"),
        (("jismein",), "items"), (("with",), "with"), (("of",), "of"), ((":",), "items"), (("to",), "to"),
    )
    for i in range(1, len(low)):
        for phrase, kind in markers:
            if tuple(low[i:i + len(phrase)]) != phrase:
                continue
            if kind == "of" and i - 1 not in type_positions:
                continue
            if kind == "to" and verb not in {"set", "assign", "change", "update"}:
                continue
            return i, i + len(phrase), kind
    return None


def _explicit_name(toks: List[Tok], used: Set[int], stop_words: Set[str]) -> Optional[Tuple[List[str], int]]:
    """Name phrases: "called X", "named X", "naam X", "X naam se", "name X"
    (followed by a value marker). Returns (words, end index) and marks used."""
    low = [t.low for t in toks]

    def _take(start: int) -> List[int]:
        taken = []
        j = start
        while j < len(toks) and len(taken) < MAX_NAME_WORDS and low[j] not in stop_words and \
                low[j] not in _TYPE_WORDS and low[j] != _DICT_TOKEN and not _is_literal(toks[j]):
            taken.append(j)
            j += 1
        return taken

    for i, word in enumerate(low):
        if word in ("called", "named") or (word in ("call", "name") and i + 1 < len(low) and low[i + 1] == "it"):
            start = i + (2 if low[min(i + 1, len(low) - 1)] == "it" and word in ("call", "name") else 1)
            taken = _take(start)
            if taken:
                used.update(range(i, taken[-1] + 1))
                return [low[j] for j in taken], taken[-1] + 1
    for i, word in enumerate(low):
        if word != "naam":
            continue
        if i + 1 < len(low) and low[i + 1] in {"se", "ka", "ki", "ke", "wala", "wali"} and i >= 1 and \
                low[i - 1] not in stop_words and low[i - 1] not in _TYPE_WORDS and low[i - 1] not in _NOT_A_NAME:
            used.update({i - 1, i, i + 1})
            if i >= 2 and low[i - 2] in {"ka", "ki"}:
                used.add(i - 2)
            return [low[i - 1]], i + 2
        if i + 1 < len(low) and low[i + 1] not in stop_words and low[i + 1] not in _TYPE_WORDS and \
                not _is_literal(toks[i + 1]):
            used.update({i, i + 1})
            if i >= 1 and low[i - 1] in {"ka", "ki", "ke"}:
                used.add(i - 1)
            return [low[i + 1]], i + 2
    for i, word in enumerate(low):
        # "make variable name score with value 95": "name" introduces the
        # name only when a value marker follows it. "variable name with
        # value taki" names the variable `name`.
        if word == "name" and i + 2 < len(low) and low[i + 1] not in stop_words and \
                low[i + 2] in {"with", "value", "equals", "equal", "=", ",", "and", "aur"} and \
                low[i + 1] not in _TYPE_WORDS and not _is_literal(toks[i + 1]):
            used.update({i, i + 1})
            return [low[i + 1]], i + 2
    return None


_STOP_FOR_NAME = _CREATE_VERBS | _AUX | _ARTICLES | {"ka", "ki", "ke", "se", "aur", "and", "with", "value",
                                                      "values", "items", "equals", "equal", "=", ",", ":", "to",
                                                      "of", "containing", "having", "jisme", "jismein"} | _EMPTY_WORDS


def parse(text: str, code: str = "") -> Optional[Request]:
    """A CREATE_VARIABLE request for the utterance, or None if it is not one."""
    raw = " ".join(str(text or "").split())
    if not raw or "?" in raw:
        return None
    toks = _strip_lead(_collapse_kv(_tokens(raw)))
    if not toks:
        return None
    low = [t.low for t in toks]
    if low[0] in {"what", "which", "how", "why", "where", "who", "when", "is", "are", "does", "kya", "kaun",
                  "kitne", "show", "read", "tell", "list", "watch", "unwatch", "stop", "rename", "delete",
                  "remove", "find", "explain", "print", "go", "jump", "track"} and \
            not (low[0] == "list" and len(low) > 1 and low[1] in _CREATE_VERBS):
        return None
    names = intent_repair.defined_names(code)

    # The verb ("banana hai" is a mis-heard "banana hai" = make).
    verb_positions = [i for i, w in enumerate(low) if w in _CREATE_VERBS and not (w == "set" and i > 0)]
    verb_positions += [i for i, w in enumerate(low) if w == "banana" and i + 1 < len(low) and
                       low[i + 1] in {"hai", "hain", "he"}]
    verb = low[min(verb_positions)] if verb_positions else ""
    if verb == "banana":
        verb = "bana"
    if low[0] == "set" and len(low) > 1 and low[1] in {"called", "named", "of", "with", "banao", "bana"}:
        verb_positions = [p for p in verb_positions if p != 0]
        verb = low[min(verb_positions)] if verb_positions else ""

    type_positions = [i for i, w in enumerate(low) if (w in _TYPE_WORDS and i not in verb_positions
                                                       and not (w == "set" and low[i + 1:i + 2] == ["to"]))
                      or w == _DICT_TOKEN]
    marker = _find_marker(low, verb, type_positions)
    head_end = marker[0] if marker else len(toks)
    marker_kind = marker[2] if marker else ""

    kind = "variable"
    said_type = False
    for i in type_positions:
        if i < head_end or (marker and i >= marker[1] and i <= marker[1] + 1):
            said_type = True
            word_kind = "dictionary" if low[i] == _DICT_TOKEN else _TYPE_WORDS[low[i]]
            if word_kind != "variable":
                kind = word_kind   # "list" is more specific than "variable"
    empty = any(w in _EMPTY_WORDS for w in low[:head_end])

    if not verb and not said_type:
        return None
    head_words = set(low[:head_end])
    if head_words & {"example", "examples", "demo", "sample"}:
        return None   # "make a list example" is a beginner template
    after_verb = [w for w in low[(min(verb_positions) + 1 if verb_positions else 0):] if w not in _ARTICLES]
    if after_verb and after_verb[0] in _STATEMENT_WORDS:
        return None   # "insert print variable name" is a print statement
    if not verb and kind != "variable":
        return None   # "list variables", "dictionary methods" are not creation requests

    used: Set[int] = set(verb_positions) | set(i for i in type_positions if i < head_end)
    head = toks[:head_end]
    explicit = _explicit_name(head, used, _STOP_FOR_NAME)

    req = Request(kind=kind)
    value_words: List[Tok] = []
    if marker:
        value_words = toks[marker[1]:]
    head_low = low[:head_end]

    def _consumable(i: int) -> bool:
        w = head_low[i]
        return (w in _CREATE_VERBS or w in _AUX or w in _ARTICLES or w in _CONNECTORS or w in _EMPTY_WORDS
                or w in _FILLERS or w in {",", "banana"} or i in used)

    name_idx: List[int] = []
    if explicit:
        name_words, name_end = explicit
        if not marker:
            # "dictionary banao prices naam se apple 50": the value follows the name.
            value_words = toks[name_end:]
            head_low = low[:name_end]
    else:
        content = [i for i in range(len(head_low)) if not _consumable(i)]
        if marker:
            name_idx = content
        else:
            last_type = max([i for i in type_positions if i < head_end], default=-1)
            before = [i for i in content if i < last_type]
            after = [i for i in content if i > last_type]
            if last_type >= 0 and before:
                name_idx = before
                value_start = last_type + 1
            elif last_type >= 0 and after:
                name_idx = [after[0]]
                value_start = after[0] + 1
                # "variable logged in false": several name words, then one literal.
                if kind == "variable" and len(after) in (3, 4) and _is_literal(toks[after[-1]]) \
                        and all(not _is_literal(toks[i]) for i in after[:-1]):
                    name_idx = after[:-1]
                    value_start = after[-1]
            elif last_type < 0:
                # "make username taki", "make score 95", "make logged in false"
                if verb not in _BARE_VERBS or any(w in _ARTICLES for w in low[:content[0] if content else 0]) or \
                        not content or content[0] != (verb_positions[0] + 1 if verb_positions else -1):
                    return None
                if _is_literal(toks[content[-1]]) and 2 <= len(content) <= 4 and \
                        all(not _is_literal(toks[i]) for i in content[:-1]):
                    name_idx = content[:-1]
                    value_start = content[-1]
                elif len(content) >= 2:
                    name_idx = [content[0]]
                    value_start = content[0] + 1
                else:
                    return None
            else:
                name_idx = []
                value_start = last_type + 1
            value_words = toks[value_start:]
            head_low = low[:value_start]
            used.update(name_idx)
        name_words = [low[i] for i in name_idx]

    # Anything in the head we could not account for means free-form speech.
    leftovers = [w for i, w in enumerate(head_low) if not (
        w in _CREATE_VERBS or w in _AUX or w in _ARTICLES or w in _CONNECTORS or w in _EMPTY_WORDS
        or w in _FILLERS or w in {",", "banana", "it", "call", "name", "naam"} or i in used or w in _TYPE_WORDS
        or w == _DICT_TOKEN)]
    if explicit and leftovers:
        req.partial = True
    # "logged in" is a fine name; "in" alone, or "mujhe chahiye", is not.
    bad_words = [w for i, w in enumerate(name_words) if w in _NOT_A_NAME and not (i and w in _PARTICLES)]
    if name_words and (bad_words or
                       (name_idx and name_idx != list(range(name_idx[0], name_idx[0] + len(name_idx))))):
        if not said_type:
            return None
        req.partial = True
    if name_words and name_words[0] in _PRONOUNS:
        return None   # "make it use a dictionary" edits existing code
    if not said_type:
        # No type word: only "make/set NAME VALUE" style commands, never
        # "make a calculator", "set speed to 2", "make trainer notes" or
        # dictated code ("insert x equals 5" types that line as written).
        if any(w in _CODE_NOUNS for w in name_words) or not name_words or verb not in _BARE_VERBS or \
                any(w in _ARTICLES for w in low[:head_end]):
            return None
        if not marker and any(t.low in _CODE_NOUNS for t in value_words):
            return None
        if marker_kind in ("of",):
            return None
    if any(w in _CODE_NOUNS for w in name_words) and kind != "variable":
        return None

    value_low = {t.low for t in value_words}
    if (marker_kind in ("with", "of", "") and value_low & _DESCRIPTION_WORDS and not
            any(t.text.startswith(("\"", "“")) for t in value_words)):
        return None   # "a list of even numbers", "make it print 5 numbers instead"
    if not said_type and value_words and any(w in {"loop", "times", "baar", "function"} for w in value_low) and \
            _expression(value_words, names) is None:
        return None
    if not marker:
        # "marks ki list banao 90 80 95": the verb sits between the noun and the items.
        while value_words and (value_words[0].low in _CREATE_VERBS or value_words[0].low in _AUX):
            value_words = value_words[1:]

    if verb in {"change", "update"} and "_".join(name_words) not in names:
        return None   # "change the variable name to total" renames; change/update only touch existing values
    _set_name(req, name_words)
    if marker_kind == "of" and not req.name and kind != "variable":
        # "create a list of 1 2 3" needs a name; "a list of fruits" is a description.
        if not all(_is_literal(t) or t.low in {",", "and", "aur"} for t in value_words):
            return None

    value, resolved, problem = build_value(value_words, kind, marker_kind, names)
    req.kind = resolved if resolved in CONTAINERS else kind
    if value is None and not problem:
        if resolved in CONTAINERS and (empty or not marker):
            value = _empty(resolved)   # "make list called marks" / "make empty set called seen"
        else:
            req.missing.append("value")
    if problem:
        req.problem = req.problem or problem
        if "value" not in req.missing:
            req.missing.append("value")
    req.value = value
    if req.partial:
        req.confidence = 0.5
    return req


def statement_for(text: str, code: str = "") -> Optional[str]:
    """Python for a complete creation request (used by structural edits)."""
    try:
        req = parse(text, code)
    except Exception:
        return None
    if req is None or not req.complete:
        return None
    return render(req.name, req.value)


# ---------------------------------------------------------------------------
# Clarification
# ---------------------------------------------------------------------------

_NOUN = {"variable": "variable", "list": "list", "tuple": "tuple", "set": "set", "dictionary": "dictionary"}


def question(req: Request) -> str:
    noun = _NOUN.get(req.kind, "variable")
    if req.problem == "keyword":
        return (f"{req.bad_name} is a Python keyword, so it cannot be a variable name. "
                f"What should I name the {noun} instead?")
    if req.problem == "builtin":
        swap = SHADOWED_BUILTINS.get(req.bad_name, "total")
        return (f"{req.bad_name} is already a Python built-in, and using it as a name would hide it. "
                f"Should I call it {swap} instead? Say yes, or say another name.")
    if req.problem == "invalid_name":
        return f"What should I name the {noun}? Use one or two words, for example score."
    if req.missing and req.missing[0] == "name":
        return f"What should I name the {noun}?"
    name = req.name or f"the {noun}"
    if req.problem == "odd_pairs":
        return ("I could not pair every key with a value. Say each key followed by its value, "
                "for example: Paris expensive, Amsterdam cheap.")
    if req.problem == "bad_value":
        return f"A set can only hold simple values. What items should {name} have?"
    if req.kind == "dictionary":
        return f"What keys and values should {name} have? For example: Paris expensive, Amsterdam cheap."
    if req.kind in ("list", "tuple", "set"):
        return f"What items should {name} have? For example: 90 80 95."
    return f"What value should {name} have?"


_YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "haan", "ha", "han", "ji", "theek", "thik", "correct",
        "fine", "right"}
_CANCEL_RE = re.compile(r"^(?:cancel|never\s*mind|nevermind|forget\s+it|leave\s+it|rehne\s+do|chhodo|no\s+thanks|"
                        r"stop|nahi|no)$")


def is_cancel(reply: str) -> bool:
    return bool(_CANCEL_RE.match(" ".join(str(reply or "").lower().strip().rstrip(".!?").split())))


def complete(pending: Dict[str, Any], reply: str, code: str = "") -> Tuple[Optional[Request], str]:
    """Fill the slot CodeUp asked for from the learner's reply.

    Returns (updated request, "") or (None, reason) when the reply is not an
    answer (reason "unmatched")."""
    req = Request.from_pending(pending)
    names = intent_repair.defined_names(code)
    toks = _strip_trail(_strip_lead(_collapse_kv(_tokens(reply))))
    if not toks:
        return None, "unmatched"
    low = [t.low for t in toks]
    if req.missing and req.missing[0] == "name":
        if req.problem == "builtin" and set(low) <= _YES | {"please", "do", "it", "that", "karo", "kar"}:
            req.problem, req.bad_name = "", ""
            _set_name(req, [SHADOWED_BUILTINS.get(pending.get("bad_name") or "", "total")])
            return req, ""
        req.problem, req.bad_name = "", ""
        used: Set[int] = set()
        explicit = _explicit_name(toks, used, _STOP_FOR_NAME)
        if explicit:
            words = explicit[0]
        elif len(toks) == 1:
            words = [low[0]]
        else:
            skip = {"variable", "ka", "ki", "ke", "naam", "name", "is", "should", "be", "it", "its", "call", "rakho",
                    "rakh", "rakhdo", "do", "karo", "the", "of", "use", "rakhna", "hoga", "wala"}
            words = [w for w in low if w not in skip and w not in _AUX]
        if not words or len(words) > MAX_NAME_WORDS or any(_is_literal(Tok(w, w)) for w in words):
            return None, "unmatched"
        _set_name(req, words)
        return req, ""
    # A value (or items, or key/value pairs).
    lead = {"value", "the", "is", "it", "its", "should", "be", "make", "set", "to", "with", "values", "items",
            "use", "equal", "equals", "="}
    while toks and toks[0].low in lead and len(toks) > 1:
        toks = toks[1:]
    toks = _strip_trail(toks)
    if not toks:
        return None, "unmatched"
    marker = "items" if req.kind in CONTAINERS else "value"
    value, resolved, problem = build_value(toks, req.kind, marker, names)
    if value is None:
        req.problem = problem or req.problem
        return (req, "") if problem else (None, "unmatched")
    req.value, req.problem = value, ""
    if resolved in CONTAINERS:
        req.kind = resolved
    req.missing = [m for m in req.missing if m != "value"]
    return req, ""


# ---------------------------------------------------------------------------
# Semantic (model) output -> the same validated request
# ---------------------------------------------------------------------------

def _json_ok(value: Any, depth: int = 0) -> bool:
    if depth > MAX_DEPTH:
        return False
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int):
        return abs(value) < 10 ** 15
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= MAX_STRING
    if isinstance(value, list):
        return len(value) <= MAX_ITEMS and all(_json_ok(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return len(value) <= MAX_ITEMS and all(isinstance(k, str) and len(k) <= MAX_STRING and
                                              _json_ok(v, depth + 1) for k, v in value.items())
    return False


def valid_semantic_value(value: Any) -> bool:
    return _json_ok(value)


def _tree_from_json(value: Any, depth: int = 0) -> list:
    if depth > MAX_DEPTH:
        raise ValueError("too deep")
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("bad float")
        return ["float", value]
    if isinstance(value, str):
        return ["str", value[:MAX_STRING]]
    if isinstance(value, list):
        return ["list", [_tree_from_json(v, depth + 1) for v in value[:MAX_ITEMS]]]
    if isinstance(value, dict):
        return ["dict", [[["str", str(k)[:MAX_STRING]], _tree_from_json(v, depth + 1)]
                         for k, v in list(value.items())[:MAX_ITEMS]]]
    raise ValueError("unsupported value")


def request_from_semantic(params: Dict[str, Any], code: str = "") -> Request:
    """Validated request from the model's CREATE_VARIABLE parameters.

    The model output is untrusted: names are re-sanitised, values are
    rebuilt as trees within the same limits, and a "variable" value must
    name something the program really defines."""
    names = intent_repair.defined_names(code)
    value_type = str(params.get("value_type") or "").strip().lower()
    value_type = {"str": "string", "text": "string", "int": "integer", "number": "integer", "bool": "boolean",
                  "dict": "dictionary", "null": "none", "array": "list"}.get(value_type, value_type)
    kind = value_type if value_type in CONTAINERS else "variable"
    req = Request(kind=kind, confidence=float(params.get("confidence") or 0.9))
    raw_name = str(params.get("name") or "").strip()
    _set_name(req, re.split(r"[\s_]+", raw_name) if raw_name else [])
    has_value = "value" in params and not (params.get("value") is None and value_type != "none")
    if value_type == "none":
        req.value = ["none"]
    elif not has_value:
        if value_type in CONTAINERS and params.get("empty"):
            req.value = _empty(value_type)
        else:
            req.missing.append("value")
    else:
        raw = params.get("value")
        try:
            if not _json_ok(raw):
                raise ValueError("limits")
            if value_type == "variable":
                if isinstance(raw, str) and raw in names and raw.isidentifier():
                    tree = ["name", raw]
                else:
                    tree = ["str", str(raw)[:MAX_STRING]]
            elif value_type == "integer" and isinstance(raw, str) and _number(raw.lower().split()) is not None:
                tree = _scalar(_tokens(raw), set()) or ["str", raw]
            elif value_type in ("float",) and isinstance(raw, (int, float)) and not isinstance(raw, bool):
                tree = ["float", float(raw)]
            else:
                tree = _tree_from_json(raw)
            if value_type in ("tuple", "set") and tree[0] == "list":
                tree = [value_type, tree[1]]
                if value_type == "set":
                    if any(item[0] in ("list", "dict") for item in tree[1]):
                        raise ValueError("unhashable")
                    tree[1] = [v for i, v in enumerate(tree[1]) if v not in tree[1][:i]]
            if value_type == "dictionary" and tree[0] != "dict":
                raise ValueError("not a dict")
            if value_type in ("list", "tuple", "set") and tree[0] not in ("list", "tuple", "set"):
                raise ValueError("not a sequence")
            req.value = tree
            if tree[0] in ("list", "tuple", "set", "dict"):
                req.kind = _NODE_KIND[tree[0]]
        except (ValueError, TypeError):
            req.problem = "bad_value"
            req.missing.append("value")
    return req


def to_json(tree: Optional[list]) -> Any:
    if not tree:
        return None
    tag = tree[0]
    if tag in ("str", "int", "float", "bool"):
        return tree[1]
    if tag == "none":
        return None
    if tag in ("name", "expr"):
        return {tag: tree[1]}
    if tag in ("list", "tuple", "set"):
        return [to_json(item) for item in tree[1]]
    if tag == "dict":
        return {str(to_json(k)): to_json(v) for k, v in tree[1]}
    return None


# ---------------------------------------------------------------------------
# Rendering and validation
# ---------------------------------------------------------------------------

def _render(tree: list, top: bool = False) -> str:
    tag = tree[0]
    if tag == "str":
        return json.dumps(str(tree[1]), ensure_ascii=False)
    if tag == "int":
        return str(int(tree[1]))
    if tag == "float":
        return repr(float(tree[1]))
    if tag == "bool":
        return "True" if tree[1] else "False"
    if tag == "none":
        return "None"
    if tag in ("name", "expr"):
        return str(tree[1])
    if tag == "list":
        return "[" + ", ".join(_render(item) for item in tree[1]) + "]"
    if tag == "tuple":
        items = [_render(item) for item in tree[1]]
        if not items:
            return "()"
        return "(" + items[0] + ",)" if len(items) == 1 else "(" + ", ".join(items) + ")"
    if tag == "set":
        return "set()" if not tree[1] else "{" + ", ".join(_render(item) for item in tree[1]) + "}"
    if tag == "dict":
        entries = [f"{_render(k)}: {_render(v)}" for k, v in tree[1]]
        if not entries:
            return "{}"
        if top and len(entries) >= 2:
            return "{\n" + "".join(f"    {entry},\n" for entry in entries) + "}"
        return "{" + ", ".join(entries) + "}"
    raise ValueError(f"unknown node {tag!r}")


_ALLOWED_NODES = (ast.Module, ast.Assign, ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple,
                  ast.Set, ast.Dict, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub,
                  ast.UAdd, ast.Call)


def is_safe_assignment(python: str) -> bool:
    try:
        tree = ast.parse(python)
    except SyntaxError:
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return False
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id == "set"
                                               and not node.args and not node.keywords):
            return False
    return True


def _depth(tree: list, depth: int = 0) -> int:
    if tree[0] in ("list", "tuple", "set"):
        return max([depth + 1] + [_depth(item, depth + 1) for item in tree[1]])
    if tree[0] == "dict":
        return max([depth + 1] + [_depth(v, depth + 1) for _k, v in tree[1]])
    return depth


def render(name: Optional[str], tree: Optional[list]) -> Optional[str]:
    """`name = <value>` built from a validated tree, or None if unsafe."""
    if not name or tree is None or not name.isidentifier() or keyword.iskeyword(name):
        return None
    try:
        if _depth(tree) > MAX_DEPTH:
            return None
        python = f"{name} = {_render(tree, top=True)}"
    except (ValueError, TypeError, IndexError):
        return None
    return python if is_safe_assignment(python) else None


def referenced_names(tree: Optional[list]) -> Set[str]:
    found: Set[str] = set()
    if not tree:
        return found
    if tree[0] == "name":
        found.add(tree[1])
    elif tree[0] == "expr":
        found.update(re.findall(r"[A-Za-z_]\w*", tree[1]))
    elif tree[0] in ("list", "tuple", "set"):
        for item in tree[1]:
            found |= referenced_names(item)
    elif tree[0] == "dict":
        for _k, v in tree[1]:
            found |= referenced_names(v)
    return found


def describe(name: str, tree: list, python: str) -> str:
    """Short spoken confirmation."""
    flat = " ".join(python.split())
    tag = tree[0]
    if tag == "dict" and len(tree[1]) >= 2:
        return f"I created a dictionary called {name} with {len(tree[1])} key-value pairs."
    if tag in ("list", "tuple", "set") and len(flat) > 70:
        noun = {"list": "list", "tuple": "tuple", "set": "set"}[tag]
        return f"I created a {noun} called {name} with {len(tree[1])} items."
    return f"I added {flat} to the editor."


# ---------------------------------------------------------------------------
# Putting it in the program
# ---------------------------------------------------------------------------

def _stores(tree: ast.AST, name: str) -> int:
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store))


def apply(code: str, name: str, python: str, refs: Set[str]) -> Tuple[str, str]:
    """(new_code, mode). mode: "append" (empty or unparsable code - the
    caller appends), "replace" (the one existing assignment was updated in
    place), "insert" (new variable placed after the setup lines), "end"."""
    if not str(code or "").strip():
        return python, "append"
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return python, "append"
    lines = code.splitlines()
    trailing = "\n" if code.endswith("\n") else ""
    top = [node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1
           and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name]
    if len(top) == 1 and _stores(tree, name) == 1:
        node = top[0]
        new = lines[:node.lineno - 1] + python.splitlines() + lines[node.end_lineno:]
        return "\n".join(new) + trailing, "replace"
    if _stores(tree, name):
        return code.rstrip("\n") + "\n" + python + trailing, "end"
    at = 0
    for node in tree.body:
        is_setup = isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)) or (
            isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant)
            and isinstance(node.value.value, str))
        if not is_setup:
            break
        at = node.end_lineno
    for ref in refs:
        top_defs = [node for node in tree.body if _stores(node, ref)
                    or (isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == ref)]
        if refs and not top_defs and ref in intent_repair.defined_names(code):
            return code.rstrip("\n") + "\n" + python + trailing, "end"
        for node in top_defs:
            at = max(at, node.end_lineno)
    new = lines[:at] + python.splitlines() + lines[at:]
    return "\n".join(new) + trailing, "insert"


# ---------------------------------------------------------------------------
# Updating an existing list / set / dictionary
# ---------------------------------------------------------------------------

_UPDATE_RE = re.compile(
    r"^(?:(?:please|now|also|ok|okay|acha)\s+)*(?:add|append|put|insert|include)\s+(?P<items>.+?)\s+"
    r"(?:to|into|in|inside)\s+(?:the\s+)?(?:(?:list|set|dictionary|dict)\s+)?(?P<name>[A-Za-z_]\w*)"
    r"(?:\s+(?:list|set|dictionary|dict))?$", re.IGNORECASE)
_UPDATE_HI_RE = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)\s+(?:mein|me|mai|main|men)\s+(?P<items>.+?)\s+"
    r"(?:add|append|daal|dal|daalo|dalo|jodo|jod)(?:\s+(?:karo|kar\s+do|kardo|do|dijiye))?$", re.IGNORECASE)


def _tree_from_ast(node: ast.AST, depth: int = 0) -> Optional[list]:
    if depth > MAX_DEPTH:
        return None
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            return ["bool", value]
        if value is None:
            return ["none"]
        if isinstance(value, int):
            return ["int", value]
        if isinstance(value, float):
            return ["float", value]
        if isinstance(value, str):
            return ["str", value]
        return None
    if isinstance(node, ast.Name):
        return ["name", node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, (int, float)) and not isinstance(node.operand.value, bool):
        return ["int" if isinstance(node.operand.value, int) else "float", -node.operand.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = [_tree_from_ast(elt, depth + 1) for elt in node.elts]
        if any(item is None for item in items):
            return None
        return [{ast.List: "list", ast.Tuple: "tuple", ast.Set: "set"}[type(node)], items]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "set" and not node.args:
        return ["set", []]
    if isinstance(node, ast.Dict):
        pairs = []
        for k, v in zip(node.keys, node.values):
            if k is None:
                return None
            kt, vt = _tree_from_ast(k, depth + 1), _tree_from_ast(v, depth + 1)
            if kt is None or vt is None:
                return None
            pairs.append([kt, vt])
        return ["dict", pairs]
    return None


def parse_container_update(text: str, code: str) -> Optional[Dict[str, Any]]:
    """"add Amsterdam to cities", "add banana 30 to prices", "cities mein
    Berlin add karo": grows a list / set / dictionary the program already
    assigns once at the top level. None when it is not such a request."""
    raw = " ".join(str(text or "").split()).rstrip(".!")
    match = _UPDATE_RE.match(raw) or _UPDATE_HI_RE.match(raw)
    if not match or not str(code or "").strip():
        return None
    name = match.group("name")
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    top = [node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1
           and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name]
    if len(top) != 1:
        return None
    node = top[0]
    current = _tree_from_ast(node.value)
    if current is None or current[0] not in ("list", "set", "dict", "tuple"):
        return None
    if current[0] == "tuple":
        return {"status": "clarify", "name": name,
                "message": f"{name} is a tuple, and a tuple cannot change after it is made. "
                           f"Say make {name} a list if you want to add to it."}
    names = intent_repair.defined_names(code)
    words = _strip_trail(_tokens(match.group("items")))
    if not words:
        return None
    if current[0] == "dict":
        pairs, problem = _pairs(words, names)
        if pairs is None or not pairs:
            return {"status": "clarify", "name": name,
                    "message": f"Say the key and its value, for example: add banana 30 to {name}."}
        merged = {json.dumps(k): i for i, (k, _v) in enumerate(current[1])}
        for key, value in pairs:
            marker = json.dumps(key)
            if marker in merged:
                current[1][merged[marker]] = [key, value]
            else:
                merged[marker] = len(current[1])
                current[1].append([key, value])
        added = len(pairs)
    else:
        items = _items(words, names)
        if not items:
            return None
        if current[0] == "set":
            items = [i for i in items if i not in current[1] and i[0] not in ("list", "dict", "set")]
        current[1].extend(items)
        added = len(items)
    if len(current[1]) > MAX_ITEMS:
        return {"status": "clarify", "name": name,
                "message": f"{name} would have more than {MAX_ITEMS} items, which is too many to add by voice."}
    python = render(name, current)
    if python is None:
        return None
    lines = code.splitlines()
    new = lines[:node.lineno - 1] + python.splitlines() + lines[node.end_lineno:]
    trailing = "\n" if code.endswith("\n") else ""
    noun = {"list": "list", "set": "set", "dict": "dictionary"}[current[0]]
    what = "pair" if current[0] == "dict" else "item"
    summary = f"I added {added} {what}{'s' if added != 1 else ''} to the {noun} {name}."
    return {"status": "ok", "name": name, "code": "\n".join(new) + trailing, "summary": summary,
            "python": python}
