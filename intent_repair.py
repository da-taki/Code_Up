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
    "stop_listening", "start_listening", "run", "generate_code", "insert_line",
    "insert_block", "explain_code", "concept_answer", "start_tutorial",
    "tutorial_control", "code_map", "sonify_block", "open_project_file",
    "run_project_file", "clarify", "no_op",
}

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
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

    generation = _match_generation(t)
    if generation:
        return generation
    if re.search(r"\bstop\s+listening\b|\bstop\s+(?:the\s+)?mic(?:rophone)?\b|\bgo\s+silent\b|\bmute\s+(?:the\s+)?mic", t):
        return _decision("control_voice", "stop_listening", 0.95, "stop listening")
    if re.search(r"\bstart\s+listening\b|\bunmute\b|\bwake\s+up\b|\bresume\s+listening\b", t):
        return _decision("control_voice", "start_listening", 0.95, "start listening")
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
