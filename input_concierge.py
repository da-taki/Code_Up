
import ast
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from intent_parser import IntentParser, _ONES

_PARSER = IntentParser()

_SAMPLE_STR_POOL = ["Taknoor", "Asha", "Noor", "Kiran"]
_SAMPLE_INT_POOL = ["16", "21", "30", "42"]
_SAMPLE_FLOAT_POOL = ["92.5", "85.0", "78.5", "90.0"]

_LABEL_STOPWORDS = {
    "enter", "please", "your", "the", "a", "an", "type", "input", "give", "me",
    "what", "is", "are", "value", "values", "of", "for", "tell", "put", "write",
    "provide", "and", "in", "to", "number", "kindly",
}

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")



def field_name(label: str) -> str:
    tokens = re.findall(r"[a-zA-Z]+", (label or "").lower())
    keywords = [t for t in tokens if t not in _LABEL_STOPWORDS]
    if keywords:
        return keywords[-1]
    return tokens[-1] if tokens else ""


def detect_inputs(code: str) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return found

    wrapper_types: Dict[int, str] = {}
    input_nodes: List[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("int", "float", "str") and node.args:
                arg = node.args[0]
                if (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Name)
                    and arg.func.id == "input"
                ):
                    wrapper_types[id(arg)] = node.func.id
            if node.func.id == "input":
                input_nodes.append(node)

    input_nodes.sort(key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
    for idx, node in enumerate(input_nodes):
        label = ""
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            label = node.args[0].value.strip()
        display = label or f"Input {idx + 1}"
        found.append({
            "label": display,
            "name": field_name(label) or f"input {idx + 1}",
            "type": wrapper_types.get(id(node), "str"),
        })
    return found[:50]


def uses_input(code: str) -> bool:
    return bool(re.search(r"\binput\s*\(", code or ""))



def normalize_spoken_number(phrase: str) -> Optional[str]:
    s = (phrase or "").strip().lower()
    if not s:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return s

    parts = re.split(r"\b(?:point|dot|decimal)\b", s, maxsplit=1)
    int_words = parts[0].strip()
    if int_words:
        int_val = _PARSER._word_to_number(int_words)
        if int_val is None:
            return None
    else:
        int_val = 0

    if len(parts) == 1:
        return str(int_val)

    digits: List[str] = []
    for word in parts[1].split():
        if word.isdigit():
            digits.append(word)
        elif word in _ONES and _ONES[word] < 10:
            digits.append(str(_ONES[word]))
        else:
            return None
    if not digits:
        return str(int_val)
    return f"{int_val}.{''.join(digits)}"


def normalize_value(raw: str) -> str:
    value = (raw or "").strip().strip(",.;:")
    number = normalize_spoken_number(value)
    return number if number is not None else value



_SAMPLE_RE = re.compile(r"\bsample\s+values?\b", re.IGNORECASE)
_RUN_WITH_PREFIX = re.compile(
    r"^\s*(?:run\s+(?:it\s+|the\s+code\s+|this\s+|that\s+)?with|"
    r"use\s+(?:the\s+)?(?:inputs?|values?)|with)\s+",
    re.IGNORECASE,
)
_IS_ARE_RE = re.compile(r"\b[a-zA-Z]\w*\s+(?:is|are)\s+\S", re.IGNORECASE)


def _values_from_rest(rest: str) -> List[str]:
    rest = rest.strip()
    if not rest:
        return []
    if "," in rest:
        return [p.strip() for p in rest.split(",") if p.strip()]
    tokens = rest.split()
    if len(tokens) > 1 and all(_NUM_RE.fullmatch(t) for t in tokens):
        return tokens
    return [rest]


def _parse_spec(spec: str) -> Tuple[List[Tuple[str, List[str]]], List[str]]:
    pairs: List[Tuple[str, List[str]]] = []
    positional: List[str] = []
    clauses = re.split(r"\s*(?:,|;|\band\b)\s*", spec, flags=re.IGNORECASE)
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        m = re.match(r"^([a-zA-Z]\w*)\s+is\s+(.+)$", clause, re.IGNORECASE)
        if m:
            pairs.append((m.group(1), [m.group(2).strip()]))
            continue
        m = re.match(r"^([a-zA-Z]\w*)\s+are\s+(.+)$", clause, re.IGNORECASE)
        if m:
            pairs.append((m.group(1), _values_from_rest(m.group(2))))
            continue
        m = re.match(r"^([a-zA-Z]\w*)\s+(.+)$", clause)
        if m:
            pairs.append((m.group(1), _values_from_rest(m.group(2))))
            continue
        positional.extend(_values_from_rest(clause))
    return pairs, positional


def parse_value_command(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None

    if _SAMPLE_RE.search(raw):
        return {"sample": True, "explicit": True, "pairs": [], "positional": []}

    explicit = False
    spec = None
    prefix = _RUN_WITH_PREFIX.match(raw)
    if prefix:
        explicit = True
        spec = raw[prefix.end():].strip()
    elif _IS_ARE_RE.search(raw):
        spec = raw
    else:
        return None

    if not spec:
        return None
    pairs, positional = _parse_spec(spec)
    if not pairs and not positional:
        return None
    return {"sample": False, "explicit": explicit, "pairs": pairs, "positional": positional}



def _norm_field(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _field_match(provided: str, code_name: str) -> bool:
    a, b = _norm_field(provided), _norm_field(code_name)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _map_positions(code_inputs: List[Dict[str, str]], parsed: Dict[str, Any]) -> List[Optional[str]]:
    n = len(code_inputs)
    values: List[Optional[str]] = [None] * n

    named = [(field, list(vals)) for field, vals in parsed.get("pairs", [])]
    positional: List[str] = list(parsed.get("positional", []))

    for i, inp in enumerate(code_inputs):
        for field, vals in named:
            if vals and _field_match(field, inp["name"]):
                values[i] = vals.pop(0)
                break

    leftover: List[str] = []
    for _field, vals in named:
        leftover.extend(vals)
    pool = positional + leftover

    pi = 0
    for i in range(n):
        if values[i] is None and pi < len(pool):
            values[i] = pool[pi]
            pi += 1
    return values


def _coerce(value_type: str, value: str) -> Tuple[bool, str]:
    normalised = normalize_value(value)
    if value_type == "int":
        try:
            return True, str(int(normalised))
        except (TypeError, ValueError):
            try:
                as_float = float(normalised)
                if as_float.is_integer():
                    return True, str(int(as_float))
            except (TypeError, ValueError):
                pass
            return False, normalised
    if value_type == "float":
        try:
            float(normalised)
            return True, normalised
        except (TypeError, ValueError):
            return False, normalised
    return True, normalised


def _example_for(value_type: str) -> str:
    if value_type == "int":
        return "16"
    if value_type == "float":
        return "92.5"
    return "Taknoor"


def sample_values(code_inputs: List[Dict[str, str]]) -> List[str]:
    counters = {"str": 0, "int": 0, "float": 0}
    pools = {"str": _SAMPLE_STR_POOL, "int": _SAMPLE_INT_POOL, "float": _SAMPLE_FLOAT_POOL}
    values: List[str] = []
    for inp in code_inputs:
        value_type = inp.get("type", "str")
        pool = pools.get(value_type, _SAMPLE_STR_POOL)
        idx = counters.get(value_type, 0)
        values.append(pool[idx % len(pool)])
        counters[value_type] = idx + 1
    return values


def _describe(code_inputs: List[Dict[str, str]], values: List[str]) -> str:
    return " and ".join(f"{inp['name']} {val}" for inp, val in zip(code_inputs, values))


def concierge_request_message(code_inputs: List[Dict[str, str]]) -> str:
    names = [inp["name"] for inp in code_inputs] or ["a value"]
    listed = ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]
    example_pairs = " and ".join(f"{inp['name']} {_example_for(inp['type'])}" for inp in code_inputs[:3])
    return (
        f"This program needs input values: {listed}. What should I use? "
        f"Say, for example: run with {example_pairs}, or say use sample values."
    )


_NUMBER_WORDS = set(_ONES) | {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred"}


def _looks_like_value_attempt(text: str, code_inputs: List[Dict[str, str]]) -> bool:
    low = (text or "").lower()
    tokens = set(re.findall(r"[a-z]+", low))
    field_hits = sum(
        1 for inp in code_inputs
        if inp["name"] and re.search(rf"\b{re.escape(inp['name'])}\b", low)
    )
    has_number = bool(re.search(r"\b\d+\b", low)) or bool(tokens & _NUMBER_WORDS)
    return field_hits >= 2 or (field_hits >= 1 and has_number)


def _anchored_parse(text: str, code_inputs: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    field_names = [inp["name"] for inp in code_inputs if inp["name"]]
    if not field_names:
        return None
    fillers = {"run", "with", "use", "the", "values", "value", "inputs", "input", "and", "please", "it", "this", "to", "set"}
    pairs: List[List[Any]] = []
    matched_any = False
    for tok in re.findall(r"[A-Za-z0-9.]+", text):
        low = tok.lower()
        matched_field = next((fn for fn in field_names if _field_match(low, fn)), None)
        if matched_field:
            pairs.append([matched_field, []])
            matched_any = True
        elif low in fillers:
            continue
        elif pairs:
            pairs[-1][1].append(tok)
    if not matched_any or not any(vals for _f, vals in pairs):
        return None
    result = [(field, [" ".join(vals)]) for field, vals in pairs if vals]
    return {"sample": False, "explicit": True, "pairs": result, "positional": []}


def _map_and_validate(
    code_inputs: List[Dict[str, str]],
    parsed: Optional[Dict[str, Any]],
    text: str,
    ai_value_fn: Optional[Callable[[List[Dict[str, str]], str], Optional[List[str]]]],
) -> Tuple[Optional[List[str]], Optional[str]]:
    values = _map_positions(code_inputs, parsed) if parsed else [None] * len(code_inputs)

    if ai_value_fn and any(v is None for v in values):
        ai_values = None
        try:
            ai_values = ai_value_fn(code_inputs, text)
        except Exception:
            ai_values = None
        if ai_values:
            for i in range(len(values)):
                if values[i] is None and i < len(ai_values) and str(ai_values[i]).strip():
                    values[i] = str(ai_values[i]).strip()

    final: List[str] = []
    for inp, value in zip(code_inputs, values):
        if value is None:
            return None, (
                f"I still need a value for {inp['name']}. "
                f"Try saying: {inp['name']} is {_example_for(inp['type'])}."
            )
        ok, normalised = _coerce(inp["type"], value)
        if not ok:
            return None, (
                f"The value for {inp['name']} should be a number. "
                f"Try saying: {inp['name']} is {_example_for(inp['type'])}."
            )
        final.append(normalised)
    return final, None


def build_input_plan(
    code: str,
    text: str,
    *,
    ai_value_fn: Optional[Callable[[List[Dict[str, str]], str], Optional[List[str]]]] = None,
) -> Optional[Dict[str, Any]]:
    parsed = parse_value_command(text)
    if parsed is None and not (code or "").strip():
        return None

    code_inputs = detect_inputs(code)

    if parsed is None:
        if code_inputs and _looks_like_value_attempt(text, code_inputs):
            parsed = _anchored_parse(text, code_inputs)
        if parsed is None:
            return None

    if not (code or "").strip():
        return {
            "status": "ask_for_code",
            "message": (
                "There is no code yet. Please add or generate some code first, "
                "then tell me the input values."
            ),
        }

    if not code_inputs:
        if parsed.get("explicit") or parsed.get("sample"):
            return {"status": "no_input"}
        return None

    if not parsed["explicit"] and not parsed["sample"]:
        provided_fields = [f for f, _ in parsed.get("pairs", [])]
        if not any(_field_match(f, inp["name"]) for f in provided_fields for inp in code_inputs):
            return None

    if parsed["sample"]:
        values = sample_values(code_inputs)
        message = "Using sample values: " + _describe(code_inputs, values) + "."
        return {"status": "ready", "values": values, "message": message, "summary": "Running with sample values."}

    values, error = _map_and_validate(code_inputs, parsed, text, ai_value_fn)
    if error:
        return {"status": "type_error", "message": error}
    message = "Running with " + _describe(code_inputs, values) + "."
    return {"status": "ready", "values": values, "message": message, "summary": message}
