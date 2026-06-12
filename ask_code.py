"""Ask My Code Mode — answer code-specific questions grounded in the current
program. Extends the existing meaning-based navigation with a small set of
deterministic question handlers (loop control, repeat counts, range what-ifs,
function purpose, condition role, symbol location).

Pure and Flask-free. Never edits or runs code, never answers general Python
theory here (those go to the concept Q&A path); answers come only from the AST
and source lines of the code that is on screen.
"""

import ast
import re
from typing import Any, Dict, List, Optional

import structure_tools

_WORD = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
         6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
_NUM = {v: k for k, v in _WORD.items()}

# Conservative whole/partial phrasings that the router treats as "ask my code".
# Deliberately code-referential so generic concept questions ("what does print
# do", "what is a variable") still fall through to the concept Q&A path.
_ROUTE_PATTERNS = [
    re.compile(r"^ask (?:about )?my code\b"),
    re.compile(r"^answer questions about my code$"),
    re.compile(r"\bwhat line controls the loop\b"),
    re.compile(r"\bwhat controls the loop\b"),
    re.compile(r"\bwhich line controls the loop\b"),
    re.compile(r"\bwhy (?:does|do|is|are).*\bprint\w*\b.*\b(times|twice|thrice|multiple)\b"),
    re.compile(r"\bif i change range\b"),
    re.compile(r"\bchange range\s+\w+\s+to\s+range\b"),
    re.compile(r"\bwhat does (?:this|the) function\b"),
    re.compile(r"\bwhat does the function \w+ do\b"),
    re.compile(r"\bwhy does (?:this|the) condition matter\b"),
    re.compile(r"\bwhat condition controls\b"),
    re.compile(r"\bwhere (?:is|does|are) (?:the )?[\w ]+ "
               r"(?:used|change[ds]?|assigned|set|calculated|computed|updated|defined|created)\b"),
]


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().rstrip(".!?").split())


def looks_like_code_question(text: str) -> bool:
    t = _norm(text)
    if not t:
        return False
    return any(rx.search(t) for rx in _ROUTE_PATTERNS)


def _to_int(token: str) -> Optional[int]:
    token = (token or "").strip().lower()
    if token.isdigit():
        return int(token)
    return _NUM.get(token)


def _word(n: int) -> str:
    return _WORD.get(n, str(n))


def _number_list(n: int) -> str:
    items = [str(i) for i in range(max(n, 0))]
    if not items:
        return "no values"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _safe_tree(code: str) -> Optional[ast.AST]:
    try:
        return ast.parse(code or "")
    except Exception:
        return None


def _line_text(code: str, line: Optional[int]) -> str:
    if not line:
        return ""
    lines = (code or "").splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def _loop_info(code: str) -> List[Dict[str, Any]]:
    tree = _safe_tree(code)
    out: List[Dict[str, Any]] = []
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            rc = None
            if isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.iter, ast.Call) \
                    and _call_name(node.iter) == "range" and node.iter.args \
                    and isinstance(node.iter.args[0], ast.Constant) \
                    and isinstance(node.iter.args[0].value, int):
                rc = node.iter.args[0].value
            out.append({"line": node.lineno, "text": _line_text(code, node.lineno),
                        "kind": "while" if isinstance(node, ast.While) else "for",
                        "range_count": rc})
    out.sort(key=lambda d: d["line"])
    return out


def _function_summary(node: ast.AST) -> str:
    has_return = any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(node))
    has_print = any(isinstance(n, ast.Call) and _call_name(n) == "print" for n in ast.walk(node))
    if has_return:
        return "takes its inputs and returns a value"
    if has_print:
        return "takes its inputs and prints something"
    return "groups a few steps you can reuse"


def _functions(code: str) -> List[Dict[str, Any]]:
    tree = _safe_tree(code)
    out: List[Dict[str, Any]] = []
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args]
            out.append({"name": node.name, "line": node.lineno, "params": params,
                        "summary": _function_summary(node)})
    out.sort(key=lambda d: d["line"])
    return out


def _if_lines(code: str) -> List[int]:
    tree = _safe_tree(code)
    if tree is None:
        return []
    return sorted(n.lineno for n in ast.walk(tree) if isinstance(n, ast.If))


def _msg(text: str) -> Dict[str, Any]:
    return {"action": "deterministic_message", "message": text, "speech": text}


def _nav(line: int, text: str, code: str = "") -> Dict[str, Any]:
    return {"action": "navigate_code", "line": line, "end_line": line,
            "message": text, "speech": text, "code_excerpt": _line_text(code, line)}


_FALLBACK = ("I cannot answer that from the current code yet. Try asking where a variable "
             "is used, what a loop controls, or what a function does.")


def _answer_range_change(q: str, code: str) -> Optional[Dict[str, Any]]:
    m = re.search(r"range\s*\(?\s*(\w+)\s*\)?\s*(?:to|into)\s*range\s*\(?\s*(\w+)", q)
    if not m:
        return None
    a, b = _to_int(m.group(1)), _to_int(m.group(2))
    if a is None or b is None:
        return None
    msg = (f"If you change range {a} to range {b}, the loop will run {_word(b)} times instead of "
           f"{_word(a)}. It will use values {_number_list(b)}.")
    loops = _loop_info(code)
    if loops:
        return _nav(loops[0]["line"], msg, code)
    return _msg(msg)


def _answer_print_count(q: str, code: str) -> Optional[Dict[str, Any]]:
    if "print" not in q:
        return None
    loops = _loop_info(code)
    loop = next((lp for lp in loops if lp["range_count"] is not None), None)
    if loop is not None:
        rc = loop["range_count"]
        msg = (f"The print runs {_word(rc)} times because the loop on line {loop['line']} repeats "
               f"that many times (range({rc})).")
        return _nav(loop["line"], msg, code)
    if loops:
        lp = loops[0]
        msg = (f"The print runs once for each time the loop on line {lp['line']} repeats. "
               f"That loop controls how many times it prints.")
        return _nav(lp["line"], msg, code)
    return None


def _answer_loop_control(code: str) -> Optional[Dict[str, Any]]:
    loops = _loop_info(code)
    if not loops:
        return _msg("There is no loop in the current code yet, so nothing controls a loop.")
    lp = loops[0]
    text = lp["text"] or f"the loop on line {lp['line']}"
    msg = (f"The loop is controlled by line {lp['line']}: {text}. That line decides how many "
           f"times the indented line runs.")
    return _nav(lp["line"], msg, code)


def _answer_function(q: str, code: str) -> Optional[Dict[str, Any]]:
    funcs = _functions(code)
    if not funcs:
        return _msg("There is no function in the current code yet.")
    named = None
    for fn in funcs:
        if re.search(rf"\b{re.escape(fn['name'].lower())}\b", q):
            named = fn
            break
    fn = named or funcs[0]
    params = ", ".join(fn["params"]) if fn["params"] else "no inputs"
    msg = (f"The function {fn['name']} on line {fn['line']} {fn['summary']}. "
           f"It takes {params}.")
    return _nav(fn["line"], msg, code)


def _answer_condition(code: str) -> Optional[Dict[str, Any]]:
    ifs = _if_lines(code)
    if not ifs:
        return _msg("There is no if statement in the current code yet.")
    line = ifs[0]
    msg = (f"The if statement on line {line} decides which lines run. The lines indented under it "
           f"run only when its test is true; otherwise they are skipped.")
    return _nav(line, msg, code)


def _answer_symbol(q: str, code: str) -> Optional[Dict[str, Any]]:
    m = re.search(r"where (?:is|does|are) (?:the )?([a-z_][\w ]*?) "
                  r"(used|change[ds]?|assigned|set|calculated|computed|updated|defined|created)\b", q)
    if not m:
        return None
    raw, verb = m.group(1).strip(), m.group(2)
    name = raw.split()[-1] if raw else ""
    if verb.startswith("change") or verb in ("assigned", "set", "calculated", "computed", "updated"):
        mode = "changed"
    elif verb in ("defined", "created"):
        mode = "defined"
    else:
        mode = "used"
    result = structure_tools.find_symbol(code, name, mode)
    if result.get("found"):
        return _nav(result.get("line"), result.get("message", ""), code)
    return _msg(result.get("message") or _FALLBACK)


def answer_code_question(question: str, code: str, session_memory: Optional[Dict[str, Any]] = None,
                         verbosity: str = "normal") -> Dict[str, Any]:
    """Answer a code-grounded question. Returns a deterministic_message or
    navigate_code action. Never hallucinates: unknown questions get a grounded
    fallback that points at what *can* be answered."""
    q = _norm(question)
    code = code or ""

    if not q:
        return _msg(_FALLBACK)

    if not code.strip():
        return _msg("There is no code to ask about yet. Try creating or opening a program first.")

    # Mode-entry phrases with no actual question.
    if q in ("ask my code", "ask about my code", "answer questions about my code", "ask my code mode"):
        msg = ("Ask me about your code. For example: what line controls the loop, why does this "
               "print three times, what does this function do, or where is a variable used.")
        return _msg(msg)

    # Dispatch most-specific first.
    for handler in (
        lambda: _answer_range_change(q, code) if "range" in q else None,
        lambda: _answer_print_count(q, code) if re.search(r"\bprint\w*\b", q)
        and re.search(r"\b(time|times|twice|thrice|multiple)\b", q) else None,
        lambda: _answer_loop_control(code) if re.search(r"\b(what|which) (line )?(controls|runs) the loop\b", q)
        or "what line controls the loop" in q or "what controls the loop" in q else None,
        lambda: _answer_function(q, code) if re.search(r"\bwhat does (?:this|the) function\b", q)
        or re.search(r"\bwhat does the function \w+ do\b", q) or "what does this function do" in q else None,
        lambda: _answer_condition(code) if re.search(r"\bcondition (matter|do|control)", q)
        or "what condition controls" in q or "why does this condition matter" in q else None,
        lambda: _answer_symbol(q, code) if q.startswith("where ") else None,
    ):
        result = handler()
        if result is not None:
            return result

    return _msg(_FALLBACK)
