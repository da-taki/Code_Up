
from __future__ import annotations

import ast
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from codeup.integrations import groq_key_manager
from codeup.projects.project_support import ProjectPathError, normalize_project_path


ALLOWED_ACTIONS = {
    "replace_current_code",
    "update_project_files",
    "ask_clarification",
    "refuse_unsafe",
}
HIGH_CONFIDENCE_THRESHOLD = 0.78
MEDIUM_CONFIDENCE_THRESHOLD = 0.55
MAX_UPDATED_CODE_CHARS = 80_000
MAX_PROJECT_EDIT_FILES = 12

_UNSAFE_IMPORTS = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "requests",
    "urllib",
    "http",
    "ftplib",
    "shutil",
    "pathlib",
    "glob",
    "pickle",
    "importlib",
}
_UNSAFE_CALLS = {
    "open",
    "eval",
    "exec",
    "__import__",
    "compile",
    "globals",
    "locals",
}
_UNSAFE_ATTRS = {
    "system",
    "popen",
    "run",
    "call",
    "check_call",
    "check_output",
}
_SHELL_WORD_RE = re.compile(
    r"\b(?:powershell|cmd\.exe|bash|sh\s+-c|curl|wget|rm\s+-rf|del\s+/|"
    r"format\s+[a-z]:|api[_-]?key|secret|token|password)\b",
    re.IGNORECASE,
)
_MARKDOWN_FENCE_RE = re.compile(r"```")
_VAGUE_EDIT_RE = re.compile(
    r"^(?:change|make|use|fix|update|modify|do|edit|replace)\s+"
    r"(?:it|that|this|the\s+code|something)?(?:\s+also)?$"
    r"|^change\s+this$|^do\s+this$",
    re.IGNORECASE,
)
_EDIT_HINT_RE = re.compile(
    r"\b(?:make|change|replace|add|remove|use|turn|convert|now|instead|"
    r"row|line|condition|loop|output|print|ask the user|input|bigger|smaller|"
    r"shorter|simpler|wider|triangle|square|passing marks|threshold|arey)\b",
    re.IGNORECASE,
)

_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def _number(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _norm(str(value))
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return _NUMBER_WORDS.get(text, _ORDINAL_WORDS.get(text))


def _friendly_text(text: str) -> str:
    value = " ".join(str(text or "").strip().strip(".").split())
    if not value:
        return "Hello"
    lower = value.lower()
    if lower == "codeup":
        return "CodeUp"
    if lower == "welcome to codeup":
        return "Welcome to CodeUp"
    if lower in {"hello", "hi"}:
        return lower.capitalize()
    return value[0].upper() + value[1:]


def _json_string(value: str) -> str:
    return json.dumps(str(value or ""))


def looks_like_edit_request(text: str) -> bool:
    return bool(_EDIT_HINT_RE.search(str(text or "")))


def is_unsafe_edit_instruction(text: str) -> bool:
    lower = _norm(text)
    return bool(
        re.search(
            r"\b(?:run forever|forever loop|infinite loop|never stop|while true|"
            r"delete files?|open files?|read files?|network|download|shell|terminal)\b",
            lower,
        )
    )


def is_vague_edit_instruction(text: str) -> bool:
    return bool(_VAGUE_EDIT_RE.match(_norm(text)))


def planner_messages(
    *,
    current_code: str,
    edit_instruction: str,
    previous_generation_request: str = "",
    last_run_output: str = "",
    last_error: str = "",
    last_edit_summary: str = "",
    active_file: str = "main.py",
    project_files: Optional[Dict[str, str]] = None,
    mapper_slots: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:

    system = (
        "You are editing beginner Python code for a visually impaired learner. "
        "Preserve the student's original program idea unless the edit asks for a change. "
        "Make the smallest safe change that satisfies the edit. Keep code beginner-friendly. "
        "Keep code runnable. Do not add unnecessary complexity. Do not use unsafe imports, "
        "file operations, network calls, subprocess, eval, exec, or infinite loops. "
        "Do not return markdown. Return JSON only. If the edit is unclear, return ask_clarification. "
        "If the request is unsafe, return refuse_unsafe. For single-file code, return the full "
        "updated code in updated_code. Do not return patches only unless the app already has a "
        "safe patch applier."
    )
    files = project_files or {}
    file_summary = ", ".join(sorted(files)[:20]) if files else "(single editor file)"
    user = (
        "Allowed actions: replace_current_code, update_project_files, ask_clarification, refuse_unsafe.\n"
        "Single-file schema: {\"action\":\"replace_current_code\",\"confidence\":0.0,"
        "\"updated_code\":\"...\",\"summary\":\"...\",\"needs_clarification\":false,"
        "\"clarification_question\":\"\",\"safety_notes\":[]}.\n"
        "Multi-file schema: {\"action\":\"update_project_files\",\"confidence\":0.0,"
        "\"files\":[{\"path\":\"main.py\",\"content\":\"...\"}],\"summary\":\"...\","
        "\"needs_clarification\":false,\"clarification_question\":\"\",\"safety_notes\":[]}.\n\n"
        f"Active file: {active_file or 'main.py'}\n"
        f"Project files: {file_summary}\n"
        f"Previous generation request: {previous_generation_request[:500] or '(none)'}\n"
        f"Last run output: {last_run_output[:500] or '(none)'}\n"
        f"Last error: {last_error[:500] or '(none)'}\n"
        f"Last edit summary: {last_edit_summary[:300] or '(none)'}\n"
        f"Mapper slots: {json.dumps(mapper_slots or {}, sort_keys=True)[:500]}\n"
        f"User edit instruction: {str(edit_instruction or '')[:500]}\n\n"
        "Current editor code:\n"
        f"{str(current_code or '')[:12000]}\n\n"
        "Return one JSON object only."
    )
    return system, user


def _strict_json(raw: Any) -> Tuple[Optional[dict], str]:
    text = str(raw or "").strip()
    if not text:
        return None, "empty_response"
    if _MARKDOWN_FENCE_RE.search(text):
        return None, "markdown_response"
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None, "invalid_json"
    if not isinstance(parsed, dict):
        return None, "plan_not_object"
    return parsed, ""


def _top_level_allowed(plan: dict) -> bool:
    allowed = {
        "action",
        "confidence",
        "updated_code",
        "files",
        "summary",
        "needs_clarification",
        "clarification_question",
        "safety_notes",
    }
    return not (set(plan) - allowed)


def _normalized_for_compare(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _code_safety_error(code: str, *, transcript: str = "") -> str:
    value = str(code or "").replace("\x00", "").strip("\r\n")
    if not value.strip():
        return "empty_code"
    if len(value) > MAX_UPDATED_CODE_CHARS:
        return "code_too_large"
    if _MARKDOWN_FENCE_RE.search(value):
        return "markdown_in_code"
    if _SHELL_WORD_RE.search(value):
        return "shell_or_secret_text"
    if _normalized_for_compare(value) == _normalized_for_compare(transcript):
        return "transcript_as_code"
    try:
        tree = ast.parse(value)
    except SyntaxError:
        return "syntax_error"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = node.names if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [ast.alias(name=node.module, asname=None)]
            for alias in names:
                root = (alias.name or "").split(".", 1)[0]
                if root in _UNSAFE_IMPORTS:
                    return "unsafe_import"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _UNSAFE_CALLS:
                return "unsafe_call"
            if isinstance(func, ast.Attribute) and func.attr in _UNSAFE_ATTRS:
                return "unsafe_call"
        if isinstance(node, ast.While):
            constant_true = isinstance(node.test, ast.Constant) and node.test.value is True
            name_true = isinstance(node.test, ast.Name) and node.test.id in {"True", "true"}
            if (constant_true or name_true) and not any(isinstance(child, ast.Break) for child in ast.walk(node)):
                return "obvious_infinite_loop"
    return ""


def validate_plan(plan: Any, *, current_code: str = "", transcript: str = "") -> Tuple[bool, str, Dict[str, Any]]:
    if not isinstance(plan, dict):
        return False, "plan_not_object", {}
    if not _top_level_allowed(plan):
        return False, "unknown_top_level_key", {}
    action = str(plan.get("action") or "").strip()
    if action not in ALLOWED_ACTIONS:
        return False, "action_not_allowed", {}
    try:
        confidence = float(plan.get("confidence", 0.0))
    except (TypeError, ValueError):
        return False, "invalid_confidence", {}
    if not 0.0 <= confidence <= 1.0:
        return False, "invalid_confidence", {}

    summary = str(plan.get("summary") or "").replace("\x00", "").strip()[:240]
    clarification = str(plan.get("clarification_question") or "").replace("\x00", "").strip()[:240]
    safety_notes = plan.get("safety_notes") or []
    if not isinstance(safety_notes, list):
        return False, "invalid_safety_notes", {}
    safety_notes = [str(note)[:120] for note in safety_notes[:10]]

    normalized = {
        "action": action,
        "confidence": confidence,
        "summary": summary,
        "needs_clarification": bool(plan.get("needs_clarification", False)),
        "clarification_question": clarification,
        "safety_notes": safety_notes,
    }
    if action in {"ask_clarification", "refuse_unsafe"}:
        if action == "ask_clarification" and not clarification and not summary:
            return False, "missing_clarification", {}
        return True, "", normalized

    if action == "replace_current_code":
        updated = str(plan.get("updated_code") or "").replace("\x00", "").strip("\r\n")
        reason = _code_safety_error(updated, transcript=transcript)
        if reason:
            return False, reason, {}
        normalized["updated_code"] = updated
        return True, "", normalized

    files = plan.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_PROJECT_EDIT_FILES:
        return False, "invalid_files", {}
    normalized_files: List[Dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            return False, "invalid_file_entry", {}
        try:
            path = normalize_project_path(item.get("path") or "")
        except ProjectPathError:
            return False, "invalid_file_path", {}
        content = str(item.get("content") or "").replace("\x00", "").strip("\r\n")
        if path.endswith(".py"):
            reason = _code_safety_error(content, transcript=transcript)
            if reason:
                return False, reason, {}
        if len(content) > MAX_UPDATED_CODE_CHARS:
            return False, "code_too_large", {}
        normalized_files.append({"path": path, "content": content})
    normalized["files"] = normalized_files
    return True, "", normalized


def parse_plan_response(raw: Any, *, current_code: str = "", transcript: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
    parsed, reason = _strict_json(raw)
    if reason:
        return None, reason
    ok, reason, normalized = validate_plan(parsed, current_code=current_code, transcript=transcript)
    if not ok:
        return None, reason
    return normalized, ""


def plan_edit(
    *,
    current_code: str,
    edit_instruction: str,
    ai_fn: Callable[[str, str], str],
    previous_generation_request: str = "",
    last_run_output: str = "",
    last_error: str = "",
    last_edit_summary: str = "",
    active_file: str = "main.py",
    project_files: Optional[Dict[str, str]] = None,
    mapper_slots: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    system, user = planner_messages(
        current_code=current_code,
        edit_instruction=edit_instruction,
        previous_generation_request=previous_generation_request,
        last_run_output=last_run_output,
        last_error=last_error,
        last_edit_summary=last_edit_summary,
        active_file=active_file,
        project_files=project_files,
        mapper_slots=mapper_slots,
    )
    try:
        raw = ai_fn(system, user)
    except Exception as exc:
        safe_reason = groq_key_manager.redact_known_keys(str(exc))[:160]
        return {"status": "failed", "reason": safe_reason}
    plan, reason = parse_plan_response(raw, current_code=current_code, transcript=edit_instruction)
    if not plan:
        safe_reason = groq_key_manager.redact_known_keys(reason or "invalid_plan")[:160]
        return {"status": "invalid", "reason": safe_reason}
    return {"status": "planned", "plan": plan}


def _replace_print_text(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    match = re.search(
        r"\b(?:say|says|print|show)\s+(.+?)(?:\s+instead|\s+rather than\b|$)",
        instruction,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    text = _friendly_text(match.group(1))
    pattern = re.compile(r'print\(\s*(["\'])(.*?)\1\s*\)', re.DOTALL)
    if not pattern.search(code):
        return None
    updated = pattern.sub(f"print({_json_string(text)})", code, count=1)
    return updated, f'Changed the print message to "{text}".'


def _replace_range_count(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    low = _norm(instruction)
    count_match = re.search(r"\b(?:print|show|use|make\s+it\s+print)\s+([a-z0-9-]+)\s+(?:values?|numbers?|times?)\b", low)
    if count_match:
        count = _number(count_match.group(1))
        if count and 1 <= count <= 100:
            updated, n = re.subn(r"range\(\s*\d+\s*\)", f"range({count})", code, count=1)
            if n:
                return updated, f"Changed the loop to print {count} numbers."

    bounds = re.search(r"\b(?:go|print|count|range)\s+from\s+([a-z0-9-]+)\s+to\s+([a-z0-9-]+)\b", low)
    if not bounds:
        bounds = re.search(r"\bfrom\s+([a-z0-9-]+)\s+to\s+([a-z0-9-]+)\b", low)
    if bounds:
        start = _number(bounds.group(1))
        end = _number(bounds.group(2))
        if start is not None and end is not None and abs(end - start) <= 100:
            step = 1 if end >= start else -1
            stop = end + step
            if step == 1:
                range_text = f"range({start}, {stop})"
            else:
                range_text = f"range({start}, {stop}, {step})"
            updated, n = re.subn(r"range\(\s*-?\d+(?:\s*,\s*-?\d+){0,2}\s*\)", range_text, code, count=1)
            if n:
                return updated, f"Changed the loop range to go from {start} to {end}."
    return None


def _replace_passing_marks(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    low = _norm(instruction)
    if not re.search(r"\b(?:passing|pass|marks?|threshold)\b", low):
        return None
    numbers = [_number(token) for token in re.findall(r"\b[a-z0-9-]+\b", low)]
    numbers = [n for n in numbers if n is not None]
    if not numbers:
        return None
    new_value = numbers[-1]
    if not (0 <= new_value <= 100):
        return None
    old_value = numbers[0] if len(numbers) >= 2 else None
    if old_value is not None:
        updated, n = re.subn(rf"(?P<prefix>\bmarks?\s*[<>]=?\s*){old_value}\b", rf"\g<prefix>{new_value}", code, count=1)
        if n:
            return updated, f"Changed the passing marks from {old_value} to {new_value}."
    updated, n = re.subn(r"(?P<prefix>\bmarks?\s*[<>]=?\s*)\d+\b", rf"\g<prefix>{new_value}", code, count=1)
    if n:
        return updated, f"Changed the passing marks to {new_value}."
    return None


def _make_marks_input(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    low = _norm(instruction)
    if not re.search(r"\b(?:ask\s+the\s+user|input)\b", low) or "mark" not in low:
        return None
    updated, n = re.subn(
        r"(?m)^([ \t]*)marks?[ \t]*=[ \t]*-?\d+[ \t]*$",
        r'\1marks = int(input("Enter your marks: "))',
        code,
        count=1,
    )
    if n:
        return updated, "Changed marks to ask the user for input."
    return None


def _add_name_input(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    low = _norm(instruction)
    if "name" not in low or not re.search(r"\b(?:ask|input|add)\b", low):
        return None
    if re.search(r"\bname\s*=\s*input\s*\(", code):
        return None
    lines = code.splitlines()
    insert_at = 0
    for index, line in enumerate(lines):
        if "input(" in line:
            insert_at = index
            break
    lines.insert(insert_at, 'name = input("Enter name: ")')
    updated = "\n".join(lines)
    updated = re.sub(
        r'print\("Next age:",\s*result\)',
        'print(name, "will be", result)',
        updated,
        count=1,
    )
    return updated, "Added a name input and included the name in the final output."


def _use_function_for_age_result(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    low = _norm(instruction)
    if "function" not in low:
        return None
    if re.search(r"(?m)^def\s+\w+\s*\(", code):
        return None
    if "age" not in code or "age + 1" not in code:
        return None
    source = code.strip("\n")
    source = re.sub(r"(?m)^result\s*=\s*age\s*\+\s*1\s*$", "result = next_age(age)", source, count=1)
    updated = "def next_age(age):\n    return age + 1\n\n" + source
    return updated, "Changed the program to use a function for the age calculation."


def _ensure_print_result_at_end(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    low = _norm(instruction)
    if not re.search(r"\bprint\b.*\bresult\b.*\bend\b|\bprint\s+the\s+result\b", low):
        return None
    if re.search(r"(?m)^print\([^)]*result[^)]*\)\s*$", code.strip().splitlines()[-1] if code.strip() else ""):
        return None
    if "result" not in code:
        return None
    updated = code.rstrip() + '\nprint("Result:", result)'
    return updated, "Printed the result at the end of the program."


def _pattern_row_edit(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    low = _norm(instruction)
    row_match = re.search(r"\b(?:(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)|(\d+))(?:st|nd|rd|th)?\s+row\b", low)
    if row_match:
        row_token = row_match.group(1) or row_match.group(2)
    else:
        row_match = re.search(r"\brow\s+([a-z0-9-]+)\b", low)
        row_token = row_match.group(1) if row_match else ""
    count_match = re.search(r"\b(?:have|has|having|with|to)\s+([a-z0-9-]+)\b", low)
    if not row_match or not count_match:
        return None
    row_value = _number(row_token)
    count_value = _number(count_match.group(1))
    if not row_value or not count_value or not (1 <= row_value <= 100 and 1 <= count_value <= 100):
        return None

    simple = re.search(
        r'for\s+row\s+in\s+range\(\s*(?P<rows>\d+)\s*\):\s*\n\s*print\(\s*(?P<quote>["\'])(?P<sym>[^"\']+)(?P=quote)\s*\*\s*(?P<count>\d+)\s*\)',
        code,
    )
    if simple:
        rows = int(simple.group("rows"))
        if row_value > rows:
            return None
        sym = simple.group("sym")
        old_count = int(simple.group("count"))
        replacement = (
            f"for row in range({rows}):\n"
            f"    if row == {row_value - 1}:\n"
            f"        print({_json_string(sym)} * {count_value})\n"
            "    else:\n"
            f"        print({_json_string(sym)} * {old_count})"
        )
        return code[: simple.start()] + replacement + code[simple.end() :], (
            f"Changed row {row_value} to print {count_value} symbols."
        )

    existing = re.search(
        r'(?ms)(if\s+row\s*==\s*)\d+(\s*:\s*\n\s*print\(\s*["\'][^"\']+["\']\s*\*\s*)\d+(\s*\))',
        code,
    )
    if existing:
        updated = code[: existing.start()] + f"{existing.group(1)}{row_value - 1}{existing.group(2)}{count_value}{existing.group(3)}" + code[existing.end() :]
        return updated, f"Changed row {row_value} to print {count_value} symbols."
    return None


def _pattern_style_edit(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    low = _norm(instruction)
    if "number" in low and re.search(r"\b(?:star|stars|\*)\b", low):
        updated, n = re.subn(r'print\(\s*(["\'])\*\1\s*\*\s*(\d+)\s*\)', r"print(str(row + 1) * \2)", code)
        if n:
            return updated, "Changed the pattern to use row numbers instead of stars."
    if "triangle" in low:
        simple = re.search(
            r'for\s+row\s+in\s+range\(\s*(?P<rows>\d+)\s*\):\s*\n\s*print\(\s*(?P<quote>["\'])(?P<sym>[^"\']+)(?P=quote)\s*\*\s*(?P<count>\d+)\s*\)',
            code,
        )
        if simple:
            rows = int(simple.group("rows"))
            sym = simple.group("sym")
            return f"for row in range(1, {rows + 1}):\n    print({_json_string(sym)} * row)", (
                "Changed the square pattern into a triangle."
            )
    return None


def _pattern_size_edit(code: str, instruction: str) -> Optional[Tuple[str, str]]:
    low = _norm(instruction)
    if "bigger" not in low and "smaller" not in low and "wider" not in low:
        return None
    delta = -1 if "smaller" in low else 1

    def repl(match: re.Match) -> str:
        value = int(match.group(1))
        return f"range({max(1, min(100, value + delta))})"

    updated, n1 = re.subn(r"range\(\s*(\d+)\s*\)", repl, code, count=1)

    def repl_mult(match: re.Match) -> str:
        value = int(match.group(1))
        return f"* {max(1, min(100, value + delta))}"

    updated, n2 = re.subn(r"\*\s*(\d+)\b", repl_mult, updated, count=1)
    if n1 or n2:
        word = "smaller" if delta < 0 else "bigger"
        return updated, f"Made the pattern {word}."
    return None


def local_edit(current_code: str, instruction: str) -> Dict[str, Any]:

    code = str(current_code or "").strip("\n")
    text = str(instruction or "").strip()
    if not code:
        if is_unsafe_edit_instruction(text):
            return {
                "status": "refuse",
                "message": "I will not make code run forever or use unsafe operations. Ask for a safe loop that stops.",
            }
        no_code_edit_target = re.search(
            r"\b(?:make|change|replace|add|remove|use|row|line|condition|loop|"
            r"output|print|ask\s+the\s+user|input|bigger|smaller|triangle|"
            r"square|passing\s+marks|threshold)\b",
            text,
            re.IGNORECASE,
        )
        if not no_code_edit_target and not is_vague_edit_instruction(text):
            return {"status": "unknown"}
        return {
            "status": "clarify",
            "message": 'I can edit code after you create some. Try saying "generate code for..." first.',
        }
    if is_unsafe_edit_instruction(text):
        return {
            "status": "refuse",
            "message": "I will not make code run forever or use unsafe operations. Ask for a safe loop that stops.",
        }
    if is_vague_edit_instruction(text):
        return {
            "status": "clarify",
            "message": "What change should I make? For example, say change the text, update the loop count, or add input.",
        }
    if not looks_like_edit_request(text):
        return {"status": "unknown"}

    for editor in (
        _add_name_input,
        _use_function_for_age_result,
        _ensure_print_result_at_end,
        _pattern_row_edit,
        _pattern_style_edit,
        _pattern_size_edit,
        _replace_print_text,
        _replace_range_count,
        _replace_passing_marks,
        _make_marks_input,
    ):
        result = editor(code, text)
        if not result:
            continue
        updated, summary = result
        reason = _code_safety_error(updated, transcript=text)
        if reason:
            return {"status": "invalid", "reason": reason}
        if updated.strip() == code.strip():
            continue
        return {
            "status": "edited",
            "updated_code": updated.strip("\n"),
            "summary": summary,
            "confidence": 0.82,
        }
    return {"status": "unknown"}
