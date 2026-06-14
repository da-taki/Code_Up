"""Key 2 intent repair layer for messy voice commands.

CodeUp is voice-first, so the router must understand the *intent* behind casual,
polite, filler-laden speech — not match literal command strings. This module:

  * extracts the real command from conversational noise wherever it appears
    (deterministic cores searched anywhere, not a brittle filler-strip list);
  * builds valid beginner Python for spoken insert commands, deciding text vs
    variable vs number from the actual editor code (AST), not blindly;
  * falls back to Key 2 (``ai_fn``) only when deterministic repair is unsure,
    and validates every Key 2 decision against an allowlist of known actions.

Deterministic CodeUp still executes and validates. Key 2 only analyzes intent.
Pure/Flask-free so it is easy to unit test.
"""

import ast
import json
import re
from typing import Any, Callable, Dict, Optional, Set

# Only these intent categories may ever be executed from a repaired decision.
ALLOWED_ACTIONS = {
    "stop_speaking", "stop_listening", "start_listening", "stop_everything",
    "run", "generate_code", "insert_line", "insert_block", "explain_code",
    "concept_answer", "start_tutorial", "tutorial_control", "code_map",
    "sonify_block", "open_project_file", "run_project_file", "clarify", "no_op",
}

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def _to_number(token: str) -> Optional[int]:
    t = str(token or "").strip().lower()
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    return _NUMBER_WORDS.get(t)


def _quote(text: str) -> str:
    return '"' + str(text or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# Editor context: which names are real variables (so we never quote them)
# ---------------------------------------------------------------------------
def _collect_target(node: ast.AST, names: Set[str]) -> None:
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _collect_target(elt, names)


def defined_names(code: str) -> Set[str]:
    """Names bound anywhere in ``code`` — assignments, for-targets, function defs
    and params, with-as, comprehensions, imports — so ``print i`` inside a loop
    stays ``print(i)`` while an undefined ``print name`` becomes text."""
    names: Set[str] = set()
    code = code or ""
    # Regex pass first so half-written code (a bare "for i in range(3):" with no
    # body yet, as the tutorial builds it line by line) still exposes its names.
    for match in re.finditer(r"^[ \t]*([A-Za-z_]\w*)\s*=(?!=)", code, re.MULTILINE):
        names.add(match.group(1))
    for match in re.finditer(r"\bfor\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+in\b", code):
        for name in re.split(r"\s*,\s*", match.group(1)):
            names.add(name)
    for match in re.finditer(r"\bdef\s+([A-Za-z_]\w*)\s*\(([^)]*)\)", code):
        names.add(match.group(1))
        for param in re.split(r"\s*,\s*", match.group(2)):
            param = param.split(":")[0].split("=")[0].strip().lstrip("*")
            if re.fullmatch(r"[A-Za-z_]\w*", param):
                names.add(param)
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                _collect_target(target, names)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _collect_target(node.target, names)
        elif isinstance(node, ast.For):
            _collect_target(node.target, names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            args = node.args
            for arg in list(args.args) + list(getattr(args, "posonlyargs", [])) + list(args.kwonlyargs):
                names.add(arg.arg)
        elif isinstance(node, ast.comprehension):
            _collect_target(node.target, names)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            _collect_target(node.optional_vars, names)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


# ---------------------------------------------------------------------------
# Spoken insert -> valid beginner Python
# ---------------------------------------------------------------------------
def print_argument_python(content: str, code: str = "", said_variable: bool = False) -> str:
    """Decide text vs variable vs number for a print() argument."""
    raw = " ".join(str(content or "").split())
    if not raw:
        return '""'
    explicit = re.match(r"(?:the\s+)?variable\s+([A-Za-z_]\w*)$", raw, re.IGNORECASE)
    if explicit:
        return explicit.group(1)
    numbered = re.match(r"(?:the\s+)?number\s+(.+)$", raw, re.IGNORECASE)
    if numbered:
        value = _to_number(numbered.group(1))
        if value is not None:
            return str(value)
    value = _to_number(raw)
    if value is not None:
        return str(value)
    if re.fullmatch(r"[A-Za-z_]\w*", raw):
        if said_variable or raw in defined_names(code):
            return raw
        return _quote(raw)
    return _quote(raw)


def build_insert_python(spoken: str, code: str = "") -> Optional[str]:
    """Return valid Python for a spoken print/loop insert, or None if this is not
    a print/loop insert (so the existing pipeline handles it)."""
    raw = " ".join(str(spoken or "").split())
    if not raw:
        return None

    indent = ""
    while re.match(r"^(?:an?\s+|the\s+)?(?:indent|indented|four\s+spaces|tab)\s+", raw, re.IGNORECASE):
        indent += "    "
        raw = re.sub(r"^(?:an?\s+|the\s+)?(?:indent|indented|four\s+spaces|tab)\s+", "", raw, flags=re.IGNORECASE).strip()

    loop = re.search(
        r"(?:for\s+loop|loop)\s+(?:that\s+|which\s+|to\s+)?(?:prints?|printing|print)\s+(.+?)\s+(\w+)\s+times?\b",
        raw, re.IGNORECASE,
    )
    if loop:
        count = _to_number(loop.group(2)) or 3
        said_var = bool(re.search(r"\bvariable\b", loop.group(1), re.IGNORECASE))
        arg = print_argument_python(re.sub(r"\bvariable\b", "", loop.group(1), flags=re.IGNORECASE).strip(), code, said_variable=said_var)
        return f"{indent}for i in range({count}):\n{indent}    print({arg})"

    saying = re.match(r"(?:a\s+|an\s+)?print(?:\s+line|\s+statement)?\s+(?:saying|that\s+says)\s+(.+)$", raw, re.IGNORECASE)
    if saying:
        return f"{indent}print({_quote(saying.group(1).strip())})"

    printed = re.match(r"(?:a\s+|an\s+)?print(?:\s+line|\s+statement)?\s+(.+)$", raw, re.IGNORECASE)
    if printed:
        body = printed.group(1).strip()
        said_var = bool(re.search(r"\bvariable\b", body, re.IGNORECASE))
        body = re.sub(r"^variable\s+", "variable ", body, flags=re.IGNORECASE)
        arg = print_argument_python(body, code, said_variable=said_var)
        return f"{indent}print({arg})"

    return None


# ---------------------------------------------------------------------------
# Simple counting-loop inserts ("insert a for loop", "make a loop that prints
# the first 3 whole numbers", "generate a loop that prints 0 to 2", ...)
# ---------------------------------------------------------------------------
def _oxford_join(items) -> str:
    items = [str(i) for i in items]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


# Insert/generate verb + (article/adjective) + a "(for) loop" object. The loop
# must be the object — phrasings like "make a beginner lesson on loops" name a
# lesson, not a loop, and are rejected by _LOOP_GUARD_RE below.
_COUNTING_LOOP_RE = re.compile(
    r"^(?:please\s+|hey\s+|ok\s+|okay\s+|alright\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)*"
    r"(?:insert|add|put|write|type|make|generate|create|build|give\s+me)\s+"
    r"(?:a\s+|an\s+|the\s+|me\s+a\s+|me\s+an\s+)?"
    r"(?:simple\s+|basic\s+|beginner\s+|short\s+|little\s+|quick\s+|small\s+)?"
    r"(?:for\s+|counting\s+)?loop\b(?P<tail>.*)$",
    re.IGNORECASE,
)
# Words that mean this is NOT a plain counting-loop insert (a lesson/report about
# loops, or the existing "prints X N times" value-loop) — defer to other routes.
_LOOP_GUARD_RE = re.compile(
    r"\b(lesson|exercise|quiz|report|trainer|handoff|story|tutorial|class|function|times)\b",
    re.IGNORECASE,
)


def _loop_bounds(tail: str):
    """Read (start, count, explicit) from the part after 'loop'.

    Returns (start, count, explicit) where the loop prints ``count`` integers
    starting at ``start``; ``explicit`` is True when the learner stated a count
    or range (vs. a bare "insert a for loop"). Returns (None, None, None) when
    the tail asks for something other than printed numbers (e.g. a string),
    so the existing insert pipeline keeps handling it.
    """
    low = " ".join(str(tail or "").lower().split())
    # Drop a trailing "in/into the editor" so a bare loop stays bare.
    low = re.sub(r"\b(?:in|into|to)\s+(?:the\s+)?editor\b\.?$", "", low).strip()
    if not low:
        return 0, 3, False  # bare loop -> a useful default counting loop
    # "0 to 2" / "zero to two" / "0 through 2" -> inclusive range start..stop.
    m = re.search(r"\b([a-z0-9-]+)\s+(?:to|through|up\s+to)\s+([a-z0-9-]+)\b", low)
    if m:
        a, b = _to_number(m.group(1)), _to_number(m.group(2))
        if a is not None and b is not None and b >= a:
            return a, (b - a + 1), True
    # "first 3 whole numbers" / "three numbers" / "5 natural numbers".
    m = re.search(r"\b(?:first\s+)?([a-z0-9-]+)\s+(?:whole\s+|natural\s+|counting\s+)?numbers?\b", low)
    if m:
        n = _to_number(m.group(1))
        if n is not None and n > 0:
            return 0, n, True
    # Generic "print numbers" / "counting" with no explicit count -> default 3.
    if re.search(r"\bnumbers?\b|\bcount(?:ing)?\b", low):
        return 0, 3, True
    # Tail present but not number-related (e.g. "that prints hello") -> defer.
    return None, None, None


def build_counting_loop_insert(text: str):
    """Recognise a request for a simple counting loop that prints numbers and
    return ``(python, confirmation)``, or None to let other handlers take it.

    Handles bare "insert a for loop", "put a for loop in the editor",
    "add a for loop", "make a loop that prints three numbers",
    "generate a loop that prints 0 to 2", and
    "insert loop printing first three whole numbers" — all of which should
    produce a real beginner loop, never a range(0) / pass stub.
    """
    raw = " ".join(str(text or "").split())
    if not raw or _LOOP_GUARD_RE.search(raw):
        return None
    m = _COUNTING_LOOP_RE.match(raw)
    if not m:
        return None
    start, count, explicit = _loop_bounds(m.group("tail") or "")
    if start is None:
        return None
    if start == 0:
        python = f"for i in range({count}):\n    print(i)"
        values = list(range(count))
    else:
        python = f"for i in range({start}, {start + count}):\n    print(i)"
        values = list(range(start, start + count))
    values_phrase = _oxford_join([str(v) for v in values])
    loop_words = "for loop" if explicit else "simple for loop"
    confirmation = f"Inserted a {loop_words} that prints {values_phrase}."
    return python, confirmation


_PY_KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally", "for",
    "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or",
    "pass", "raise", "return", "try", "while", "with", "yield", "print",
}
_VALUE_KEYWORD = (
    r"(?:and\s+)?(?:give\s+it\s+the\s+value|giving\s+it\s+the\s+value|give\s+the\s+value|"
    r"with\s+the\s+value|with\s+value|set\s+to|equals?\s+to|equals?|holding|storing|"
    r"that\s+holds|that\s+stores|that\s+is|that\s+equals|to|=)"
)


def _sanitize_name(name: Optional[str]) -> Optional[str]:
    """Turn a spoken name into a valid Python identifier, or None if impossible."""
    if not name:
        return None
    candidate = re.sub(r"[^0-9a-zA-Z_]+", "_", str(name).strip().lower()).strip("_")
    if not candidate or candidate[0].isdigit():
        return None
    if candidate in _PY_KEYWORDS:
        return None
    return candidate


def _value_python(value: str) -> "tuple[str, str]":
    """Build the Python literal for a spoken value and report its type."""
    v = " ".join(str(value or "").split()).strip().strip(".")
    number = _to_number(v)
    if number is not None:
        return str(number), "number"
    if v.lower() in ("true", "false"):
        return v.capitalize(), "boolean"
    return _quote(v), "string"


def parse_variable_assignment(utterance: str, code: str = "") -> Optional[Dict[str, Any]]:
    """Recognise a natural variable-assignment command and return its slots, or
    None. Slots include name, value, value_type, missing[], and python (when
    complete) so deterministic code can build/validate the assignment."""
    t = " ".join(str(utterance or "").split())

    set_form = re.match(r"^(?:please\s+)?set\s+([A-Za-z_]\w*)\s+(?:to|=|equals?\s+to|equals?)\s+(.+)$", t, re.IGNORECASE)
    if set_form:
        return _variable_slots(set_form.group(1), set_form.group(2))

    phrase = re.match(
        r"^(?:please\s+)?(?:insert|add|create|make|declare|define|put|new)\s+(?:a\s+|an\s+|new\s+)*variable\b\s*(.*)$",
        t, re.IGNORECASE,
    )
    if not phrase:
        return None
    rest = phrase.group(1).strip()
    if not rest:
        return _variable_slots(None, None)

    missing_name = re.match(rf"^{_VALUE_KEYWORD}\s+(.+)$", rest, re.IGNORECASE)
    if missing_name:
        return _variable_slots(None, missing_name.group(len(missing_name.groups())))

    named = re.match(r"^(?:named\s+|called\s+)?([A-Za-z_]\w*)\s*(.*)$", rest, re.IGNORECASE)
    if not named:
        return None
    name, tail = named.group(1), named.group(2).strip()
    if not tail:
        return _variable_slots(name, None)
    valued = re.match(rf"^{_VALUE_KEYWORD}\s+(.+)$", tail, re.IGNORECASE)
    if valued:
        return _variable_slots(name, valued.group(len(valued.groups())))
    return _variable_slots(name, None)


def _variable_slots(name: Optional[str], value: Optional[str]) -> Dict[str, Any]:
    py_name = _sanitize_name(name)
    missing = []
    if not py_name:
        missing.append("name")
    value_type = None
    py_value = None
    raw_value = " ".join(str(value or "").split()).strip().strip(".") if value else None
    if raw_value:
        py_value, value_type = _value_python(raw_value)
    else:
        missing.append("value")
    slots: Dict[str, Any] = {
        "construct": "variable_assignment", "name": py_name, "value": raw_value,
        "value_type": value_type, "missing": missing,
    }
    if not missing:
        slots["python"] = f"{py_name} = {py_value}"
    return slots


def parse_variable_answer(text: str, pending: Dict[str, Any]) -> Optional[str]:
    """Complete a remembered variable assignment from the user's answer; returns
    the Python line, or None if the answer is unusable (caller re-asks)."""
    if pending.get("missing") == "name" or not pending.get("name"):
        match = re.search(r"(?:call(?:\s+it)?|name(?:\s+it)?|named|called)\s+([A-Za-z_]\w*)", text, re.IGNORECASE)
        candidate = match.group(1) if match else text
        name = _sanitize_name(candidate)
        if not name:
            return None
        py_value, _ = _value_python(pending.get("value") or "")
        return f"{name} = {py_value}"
    # Missing value: the answer is the value.
    raw = " ".join(str(text or "").split()).strip().strip(".")
    if not raw:
        return None
    py_value, _ = _value_python(raw)
    return f"{pending['name']} = {py_value}"


def variable_question(slots: Dict[str, Any]) -> str:
    if "name" in slots.get("missing", []):
        return "What should I name the variable?"
    name = slots.get("name") or "the variable"
    return f"What value should {name} store?"


def extract_insert_content(utterance: str) -> Optional[str]:
    """Pull the code part out of a spoken insert command, tolerating polite
    wrappers ("can you put ... in the editor")."""
    t = " ".join(str(utterance or "").split())
    m = re.match(
        r"^(?:please\s+|hey\s+|ok\s+|okay\s+|alright\s+)*"
        r"(?:can\s+you\s+|could\s+you\s+|would\s+you\s+|please\s+)*"
        r"(?:insert|add|put|write|type)\s+(.+?)"
        r"(?:\s+(?:in|into|to)\s+(?:the\s+)?editor)?\.?$",
        t, re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Natural-speech intent repair
# ---------------------------------------------------------------------------
def _decision(intent: str, action: str, confidence: float, canonical: str,
              slots: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"handled": True, "intent": intent, "action": action, "confidence": confidence,
            "canonical_command": canonical, "slots": slots or {}, "clarification": ""}


def _noop() -> Dict[str, Any]:
    return {"handled": False, "intent": "no_op", "action": "no_op", "confidence": 0.0,
            "canonical_command": "", "slots": {}, "clarification": ""}


def _clarify(message: str) -> Dict[str, Any]:
    return {"handled": False, "intent": "clarify", "action": "clarify", "confidence": 0.4,
            "canonical_command": "", "slots": {}, "clarification": message}


def _clean_prompt(text: str) -> str:
    prompt = " ".join(str(text or "").split())
    prompt = re.sub(r"^(?:in\s+the\s+editor\s+)?(?:that\s+|which\s+|to\s+|for\s+|please\s+)+", "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\s+(?:please|now|for\s+me)\.?$", "", prompt, flags=re.IGNORECASE)
    return prompt.strip()


def _match_generation(t: str) -> Optional[Dict[str, Any]]:
    m = re.search(
        r"\b(?:generate|write|create|build|make|put)\b.*?\b(?:code|program|programme|script|function)\b"
        r"(?:\s+(?:that|which|to|for|printing|that\s+prints))?\s+(.+)$",
        t,
    )
    if not m:
        m = re.search(r"\bcode\s+(?:that|which|to|for)\s+(.+)$", t)
    if m:
        prompt = _clean_prompt(m.group(1))
        if prompt:
            return _decision("generate_code", "generate_code", 0.9,
                             f"generate code to {prompt}", {"prompt": prompt})
    return None


def repair(utterance: str, *, code: str = "",
           ai_fn: Optional[Callable[[str, str], str]] = None) -> Dict[str, Any]:
    """Analyze a messy command into a structured, allowlisted decision."""
    t = _norm(utterance)
    if not t:
        return _noop()

    # Speech-control semantics are distinct and high priority. "Stop everything"
    # cancels actions; "stop speaking" only cancels narration; "stop listening"
    # turns off the mic. These must never collapse into one another.
    if re.search(r"\bstop\s+everything\b|\bcancel\s+everything\b|\bstop\s+all\b|\bcancel\s+all\b|\babort\b|\bstop\s+all\s+actions\b", t):
        return _decision("control", "stop_everything", 0.95, "stop everything")
    if re.search(r"\bstop\s+(?:speaking|talking|narrating|the\s+narration|the\s+voice|the\s+speech)\b"
                 r"|\bstop\s+narration\b|\bbe\s+quiet\b|\bpause\s+(?:the\s+)?speech\b|\bquiet\s+down\b|\bshut\s+up\b", t):
        return _decision("control_speech", "stop_speaking", 0.95, "stop speaking")
    if re.search(r"\bstop\s+listening\b|\bturn\s+off\s+(?:the\s+)?mic(?:rophone)?\b|\bmute\s+(?:the\s+)?mic"
                 r"|\b(?:pause|disable|turn\s+off|stop)\s+voice\s+input\b|\bdisable\s+(?:the\s+)?mic(?:rophone)?\b|\bgo\s+silent\b", t):
        return _decision("control_voice", "stop_listening", 0.95, "stop listening")
    if re.search(r"\bstart\s+listening\b|\bunmute\b|\bwake\s+up\b|\bresume\s+listening\b|\bturn\s+on\s+(?:the\s+)?mic", t):
        return _decision("control_voice", "start_listening", 0.9, "start listening")

    generation = _match_generation(t)
    if generation:
        return generation
    if re.search(r"\bmap\s+(?:my|the|this)\s+code\b|\bcode\s+map\b|\bmap\s+(?:my|the)\s+program\b", t):
        return _decision("code_map", "code_map", 0.9, "map my code")
    if re.search(r"\bsonify\b", t):
        return _decision("sonify_block", "sonify_block", 0.9, "sonify block")
    if re.search(r"\b(?:start|open|begin|launch)\s+(?:the\s+|a\s+)?tutorial\b", t):
        return _decision("start_tutorial", "start_tutorial", 0.9, "start tutorial")
    if re.search(r"\bexplain\b|\bwalk\s+me\s+through\b|\bwhat\s+does\s+this\s+(?:code|program|line)?\s*do\b"
                 r"|\btell\s+me\s+what\s+this\s+(?:code|program)\b|\bwhat\s+is\s+this\s+(?:code|program)\b", t):
        return _decision("explain_code", "explain_code", 0.85, "explain this program")
    if re.search(r"\b(?:run|execute|launch)\b", t):
        return _decision("run", "run", 0.85, "run")

    ai = _ai_repair(utterance, code, ai_fn)
    if ai:
        return ai
    return _noop()


def _ai_repair(utterance: str, code: str,
               ai_fn: Optional[Callable[[str, str], str]]) -> Optional[Dict[str, Any]]:
    """Key 2 fallback: only used when deterministic repair is unsure. Output is
    validated against the allowlist; Key 2 may not invent files/output/errors."""
    if not ai_fn:
        return None
    system = (
        "You are CodeUp's voice intent analyzer. Map the user's messy spoken command to "
        "ONE known action. Return ONLY JSON: {handled, intent, action, confidence, "
        "canonical_command, slots, clarification}. action MUST be one of: "
        + ", ".join(sorted(ALLOWED_ACTIONS)) + ". Do not invent file names, code output, "
        "errors, or variables. If unsure, set handled false, action clarify, and give one "
        "short clarification question."
    )
    user = f"Editor has code: {'yes' if str(code or '').strip() else 'no'}\nCommand: {utterance}\nReturn the JSON decision."
    try:
        raw = ai_fn(system, user)
    except Exception:
        return None
    decision = _parse_decision(raw)
    if decision and validate_decision(decision):
        return decision
    return None


def _parse_decision(raw: str) -> Optional[Dict[str, Any]]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", str(raw or "")).strip()
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except (ValueError, TypeError):
            return None
    return parsed if isinstance(parsed, dict) else None


def validate_decision(decision: Dict[str, Any]) -> bool:
    """Reject anything outside the allowlist or structurally wrong."""
    if not isinstance(decision, dict):
        return False
    action = decision.get("action")
    if action not in ALLOWED_ACTIONS:
        return False
    try:
        confidence = float(decision.get("confidence", 0))
    except (TypeError, ValueError):
        return False
    if not 0.0 <= confidence <= 1.0:
        return False
    slots = decision.get("slots")
    if slots is not None and not isinstance(slots, dict):
        return False
    if action == "generate_code" and not (isinstance(slots, dict) and str(slots.get("prompt", "")).strip()):
        return False
    return True
