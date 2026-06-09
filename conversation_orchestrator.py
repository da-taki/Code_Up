import json
import re
from typing import Any, Callable, Dict, List, Optional

from intent_parser import parse_intent
from symbolic_specs import build_exact_symbol_generation, constraint_summary, normalize_spoken_symbols


MAX_ACTIONS = 5
MAX_PROMPT = 1200
ALLOWED_ACTION_TYPES = {
    "generate_code",
    "run",
    "explain",
    "map_code",
    "sonify",
    "open_file",
    "start_tutorial",
    "show_help",
    "answer_question",
}

WAKE_RE = re.compile(
    r"^\s*(?:hey|okay|ok|hello|hi)?\s*"
    r"(?:code\s*up|codeup|codup|cold\s*up|coat\s*up|could\s*up|code\s*app|cold\s*app)"
    r"[\s,;:\-]*",
    re.IGNORECASE,
)

NOISY_REPLACEMENTS = [
    (re.compile(r"\bast\s+risks?\b", re.IGNORECASE), "asterisks"),
    (re.compile(r"\baster\s+risks?\b", re.IGNORECASE), "asterisks"),
    (re.compile(r"\ba\s+sterisks?\b", re.IGNORECASE), "asterisks"),
    (re.compile(r"\bhash\s+tags?\b", re.IGNORECASE), "hashes"),
    (re.compile(r"\bcold\s+up\b", re.IGNORECASE), "code up"),
    (re.compile(r"\bcodup\b", re.IGNORECASE), "code up"),
]


def _safe_text(value: Any, limit: int = MAX_PROMPT) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > limit:
        return text[:limit]
    return text


def cleanup_transcript(text: str) -> str:
    cleaned = _safe_text(text, limit=2000)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for pattern, replacement in NOISY_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def strip_wake_phrase(text: str) -> Dict[str, Any]:
    cleaned = cleanup_transcript(text)
    match = WAKE_RE.match(cleaned)
    if not match:
        return {"wake_detected": False, "text": cleaned}
    stripped = cleaned[match.end():].strip(" ,;:-")
    return {"wake_detected": True, "text": stripped or cleaned}


def _contains_multi_connector(text: str) -> bool:
    return bool(re.search(r"\b(?:then|and then|after that)\b", text, flags=re.IGNORECASE))


def _split_parts(text: str) -> List[str]:
    value = re.sub(r"\b(?:and then|after that)\b", " then ", text, flags=re.IGNORECASE)
    value = re.sub(
        r"\s+and\s+(?=(?:run|open|walk|explain|map|sonify|read|start|show|tell)\b)",
        " then ",
        value,
        flags=re.IGNORECASE,
    )
    parts = [part.strip(" ,.;:") for part in re.split(r"\bthen\b", value, flags=re.IGNORECASE)]
    return [part for part in parts if part]


def _clarification(raw_text: str, normalized_text: str, question: str, confidence: float = 0.65) -> Dict[str, Any]:
    return validate_plan({
        "confidence": confidence,
        "normalized_text": normalized_text or cleanup_transcript(raw_text),
        "needs_clarification": True,
        "clarification_question": question,
        "actions": [],
        "spoken_summary": question,
    })


def _plan(actions: List[Dict[str, Any]], normalized_text: str, summary: str, confidence: float = 0.9) -> Dict[str, Any]:
    return validate_plan({
        "confidence": confidence,
        "normalized_text": normalized_text,
        "needs_clarification": False,
        "clarification_question": "",
        "actions": actions,
        "spoken_summary": summary,
    })


def _has_clear_2d_pattern_context(text: str) -> bool:
    normalized = normalize_spoken_symbols(text)
    has_size = bool(re.search(r"\b\d+\s+(?:by|x|times)\s+\d+\b|\b\d+\s+rows?\b|\brow\s+\d+\b|\bline\s+\d+\b", normalized))
    has_symbol = any(symbol in normalized for symbol in ("*", "#")) or bool(re.search(r"\b(?:star|asterisk|hash)\b", text, flags=re.IGNORECASE))
    return has_size and has_symbol


def _exact_plan(text: str, source: str) -> Optional[Dict[str, Any]]:
    exact = build_exact_symbol_generation(text, source=source)
    if not exact:
        return None
    normalized = exact.get("understood") or "; ".join(constraint_summary(text)) or cleanup_transcript(text)
    if not exact.get("success"):
        return _clarification(text, normalized, exact.get("message") or "Please type the exact symbol and counts.")
    prompt = cleanup_transcript(text)
    action = {
        "type": "generate_code",
        "source": "deterministic",
        "prompt": prompt,
        "constraints": {"summary": constraint_summary(prompt), "understood": normalized},
        "requires_exact_symbols": True,
    }
    return _plan(
        [action],
        f"Generate Python code for this exact-symbol task: {normalized}.",
        f"I understood an exact-symbol task: {normalized}.",
        confidence=0.96,
    )


def _strip_generation_lead_in(part: str) -> str:
    prompt = re.sub(
        r"^(?:please\s+)?(?:generate|create|make|write|build)\s+(?:python\s+)?(?:code\s+)?(?:to|for)?\s*",
        "",
        part,
        flags=re.IGNORECASE,
    ).strip()
    if re.search(r"\b(?:print\s+)?numbers?\s+(?:zero|0)\s+to\s+(?:two|2)\b", prompt, flags=re.IGNORECASE):
        return "print numbers zero to two"
    return prompt or part


def _part_to_action(part: str) -> Optional[Dict[str, Any]]:
    lower = part.lower().strip()
    if not lower:
        return None
    if re.fullmatch(r"(?:it\s+)?run(?:\s+it|\s+code|\s+my\s+code)?", lower):
        return {"type": "run", "source": "deterministic", "prompt": "", "constraints": {}, "requires_exact_symbols": False}
    run_file = re.match(r"^(?:run|execute)\s+(.+)$", lower)
    if run_file:
        path = run_file.group(1).strip()
        if path in {"it", "code", "my code", "this"}:
            return {"type": "run", "source": "deterministic", "prompt": "", "constraints": {}, "requires_exact_symbols": False}
        return {"type": "run", "source": "deterministic", "prompt": "", "path": path, "constraints": {}, "requires_exact_symbols": False}
    open_file = re.match(r"^open\s+(.+)$", lower)
    if open_file:
        return {"type": "open_file", "source": "deterministic", "prompt": "", "path": open_file.group(1).strip(), "constraints": {}, "requires_exact_symbols": False}
    if re.search(r"\b(?:walk\s+me\s+through|explain|tell\s+me\s+what\s+it\s+does|what\s+it\s+does)\b", lower):
        return {"type": "explain", "source": "deterministic", "prompt": "", "constraints": {}, "requires_exact_symbols": False}
    if re.search(r"\b(?:map\s+my\s+code|code\s+map)\b", lower):
        return {"type": "map_code", "source": "deterministic", "prompt": "", "constraints": {}, "requires_exact_symbols": False}
    if re.search(r"\bsonify\b", lower):
        return {"type": "sonify", "source": "deterministic", "prompt": "", "constraints": {}, "requires_exact_symbols": False}
    if re.search(r"\b(?:help|what\s+can\s+i\s+do|show\s+commands)\b", lower):
        return {"type": "show_help", "source": "deterministic", "prompt": "", "constraints": {}, "requires_exact_symbols": False}
    if re.search(r"\bstart\s+tutorial\b", lower):
        return {"type": "start_tutorial", "source": "deterministic", "prompt": "", "constraints": {}, "requires_exact_symbols": False}
    if re.search(r"\b(?:generate|create|make|write|build)\b", lower):
        prompt = _strip_generation_lead_in(part)
        return {"type": "generate_code", "source": "deterministic", "prompt": prompt, "constraints": {}, "requires_exact_symbols": False}
    return None


def _deterministic_plan(text: str, *, code: str, source: str, force: bool = False) -> Optional[Dict[str, Any]]:
    stripped = strip_wake_phrase(text)
    cleaned = stripped["text"]
    if not cleaned:
        return None

    exact = _exact_plan(cleaned, source)
    if exact:
        return exact

    lower = cleaned.lower()
    if "cube" in lower and not _has_clear_2d_pattern_context(cleaned):
        return _clarification(cleaned, cleaned, "Do you mean a printed 2D pattern or a 3D cube concept?")

    if re.search(r"\bfix\s+it\b", lower) and re.search(r"\brun\s+it\b", lower) and not str(code or "").strip():
        return _clarification(cleaned, cleaned, "There is no code to fix yet. Do you want me to generate an example?")

    parts = _split_parts(cleaned)
    actions = [_part_to_action(part) for part in parts]
    actions = [action for action in actions if action]
    if actions and (force or len(actions) > 1 or stripped["wake_detected"] or _contains_multi_connector(cleaned)):
        labels = [_action_label(action) for action in actions]
        return _plan(actions, cleaned, "I understood: " + ", then ".join(labels) + ".", confidence=0.9)

    if force and len(actions) == 1:
        return _plan(actions, cleaned, f"I understood: {_action_label(actions[0])}.", confidence=0.86)
    return None


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", str(text or "")).strip()
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _ai_prompt(text: str, code: str) -> tuple[str, str]:
    system = (
        "You are CodeUp's conversation orchestrator. Return only JSON. "
        "Do not generate code. Do not execute code. Produce a safe plan using only "
        "these action types: generate_code, run, explain, map_code, sonify, open_file, "
        "start_tutorial, show_help, answer_question. Preserve exact counts and symbols."
    )
    user = (
        "Transcript:\n"
        f"{text}\n\n"
        f"Editor has code: {'yes' if str(code or '').strip() else 'no'}\n\n"
        "Return JSON with keys confidence, normalized_text, needs_clarification, "
        "clarification_question, actions, spoken_summary. Each action has type, source, "
        "prompt, constraints, requires_exact_symbols, and optional path."
    )
    return system, user


def _ai_plan(text: str, code: str, ai_plan_fn: Optional[Callable[[str, str], str]]) -> Optional[Dict[str, Any]]:
    if not ai_plan_fn:
        return None
    try:
        system, user = _ai_prompt(text, code)
        raw = ai_plan_fn(system, user)
    except Exception:
        return None
    parsed = _extract_json_object(raw)
    if not parsed:
        return None
    return validate_plan(parsed)


def orchestrate_command(
    text: str,
    *,
    code: str = "",
    source: str = "typed",
    ai_plan_fn: Optional[Callable[[str, str], str]] = None,
) -> Optional[Dict[str, Any]]:
    cleaned = cleanup_transcript(text)
    stripped = strip_wake_phrase(cleaned)
    force = bool(stripped["wake_detected"] or _contains_multi_connector(stripped["text"]))

    exact = _exact_plan(stripped["text"], source)
    if exact:
        return exact
    lower = stripped["text"].lower()
    if "cube" in lower and not _has_clear_2d_pattern_context(stripped["text"]):
        return _clarification(stripped["text"], stripped["text"], "Do you mean a printed 2D pattern or a 3D cube concept?")
    if re.search(r"\bfix\s+it\b", lower) and re.search(r"\brun\s+it\b", lower) and not str(code or "").strip():
        return _clarification(stripped["text"], stripped["text"], "There is no code to fix yet. Do you want me to generate an example?")

    rough_generation = bool(re.search(r"\b(?:generate|create|make|write|build)\b", lower))
    if force and rough_generation:
        ai = _ai_plan(stripped["text"], code, ai_plan_fn)
        if ai and (ai.get("needs_clarification") or ai.get("actions")):
            return ai

    deterministic = _deterministic_plan(cleaned, code=code, source=source, force=force)
    if deterministic:
        return deterministic

    ai = _ai_plan(stripped["text"], code, ai_plan_fn)
    if ai:
        return ai

    if stripped["wake_detected"]:
        parsed = parse_intent(stripped["text"])
        intent = parsed.get("intent")
        if intent == "run":
            return _plan([{"type": "run", "source": "deterministic", "prompt": "", "constraints": {}, "requires_exact_symbols": False}], stripped["text"], "I understood: run code.", confidence=0.82)
        if intent in {"walk_through", "mentor_chat", "concept_question"}:
            return _plan([{"type": "explain", "source": "deterministic", "prompt": stripped["text"], "constraints": {}, "requires_exact_symbols": False}], stripped["text"], "I understood: explain the program.", confidence=0.82)
    return None


def validate_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    try:
        confidence = float(plan.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))
    normalized_text = _safe_text(plan.get("normalized_text"), limit=MAX_PROMPT)
    needs_clarification = bool(plan.get("needs_clarification", False))
    clarification = _safe_text(plan.get("clarification_question"), limit=300)
    actions_in = plan.get("actions") if isinstance(plan.get("actions"), list) else []
    actions: List[Dict[str, Any]] = []
    if not needs_clarification:
        for item in actions_in[:MAX_ACTIONS]:
            if not isinstance(item, dict):
                continue
            action_type = _safe_text(item.get("type"), limit=40)
            if action_type not in ALLOWED_ACTION_TYPES:
                continue
            source = _safe_text(item.get("source"), limit=40) or "ai_orchestrated"
            if source not in {"deterministic", "ai_orchestrated"}:
                source = "ai_orchestrated"
            action = {
                "type": action_type,
                "source": source,
                "prompt": _safe_text(item.get("prompt"), limit=MAX_PROMPT),
                "constraints": item.get("constraints") if isinstance(item.get("constraints"), dict) else {},
                "requires_exact_symbols": bool(item.get("requires_exact_symbols", False)),
            }
            path = _safe_text(item.get("path"), limit=120)
            if path:
                action["path"] = path
            actions.append(action)
    return {
        "confidence": confidence,
        "normalized_text": normalized_text,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification,
        "actions": actions,
        "spoken_summary": _safe_text(plan.get("spoken_summary") or clarification, limit=500),
    }


def _action_label(action: Dict[str, Any]) -> str:
    action_type = action.get("type")
    if action_type == "generate_code":
        return "generate code"
    if action_type == "run" and action.get("path"):
        return f"run {action['path']}"
    if action_type == "run":
        return "run code"
    if action_type == "explain":
        return "explain the program"
    if action_type == "map_code":
        return "map the code"
    if action_type == "sonify":
        return "sonify the block"
    if action_type == "open_file":
        return f"open {action.get('path', 'file')}"
    if action_type == "show_help":
        return "show help"
    if action_type == "start_tutorial":
        return "start tutorial"
    return action_type or "act"


def action_next_label(action: Dict[str, Any]) -> str:
    action_type = action.get("type")
    if action_type == "generate_code" and action.get("requires_exact_symbols"):
        return "Creating deterministic pattern code."
    if action_type == "generate_code":
        return "Generating code."
    if action_type == "run":
        return f"Running {action.get('path')}." if action.get("path") else "Running code."
    if action_type == "explain":
        return "Explaining the program."
    if action_type == "map_code":
        return "Mapping the code."
    if action_type == "sonify":
        return "Sonifying the block."
    if action_type == "open_file":
        return f"Opening {action.get('path', 'file')}."
    if action_type == "show_help":
        return "Showing command guide."
    if action_type == "start_tutorial":
        return "Starting guided tutorial."
    return "Preparing the next action."


def frontend_actions(plan: Dict[str, Any], *, input_source: str = "typed") -> List[Dict[str, Any]]:
    actions = []
    for action in plan.get("actions", []):
        action_type = action.get("type")
        common = {
            "orchestrated": True,
            "input_source": input_source,
            "source": "deterministic_exact" if action.get("source") == "deterministic" and action.get("requires_exact_symbols") else action.get("source", "ai_orchestrated"),
            "label": action_next_label(action),
        }
        if action_type == "generate_code":
            actions.append({**common, "action": "generate_code", "prompt": action.get("prompt", "")})
        elif action_type == "run":
            if action.get("path"):
                actions.append({**common, "action": "run_project_file", "path": action.get("path", "")})
            else:
                actions.append({**common, "action": "run"})
        elif action_type == "explain":
            actions.append({**common, "action": "walk_through"})
        elif action_type == "map_code":
            actions.append({**common, "action": "mentor_code_map"})
        elif action_type == "sonify":
            actions.append({**common, "action": "sonify_block"})
        elif action_type == "open_file":
            actions.append({**common, "action": "open_project_file", "path": action.get("path", "")})
        elif action_type == "start_tutorial":
            actions.append({**common, "action": "start_tutorial"})
        elif action_type == "show_help":
            actions.append({**common, "action": "help"})
        elif action_type == "answer_question":
            actions.append({**common, "action": "mentor_chat", "message": action.get("prompt", ""), "mode": "concept"})
    return actions
