"""Deterministic, non-visual Error Trace Narration.

A visually impaired beginner should not have to scan a stack trace. This module
turns a Python error (either a full traceback or the one-line summary CodeUp
stores in ``last_run_error``) into a short spoken explanation: where the program
crashed, what likely caused it, the failing source line, and what to try next.

Everything here is deterministic and rule-based. No AI, no network. It never
invents runtime values: ``value_narration`` only reports a value when that value
is actually present in the error text. An AI layer may add colour later, but it
must be grounded in the facts ``analyze`` returns.
"""

import re
from typing import Any, Dict, List, Optional

__all__ = ["analyze", "narrate", "brief", "value_narration", "EXCEPTION_TYPES"]

_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\S+))?')
_EXC_LINE_RE = re.compile(
    r'^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Interrupt|Exit|Warning)):?\s*(.*)$'
)
# CodeUp's stored one-line summary, e.g. "Line 8: ValueError: bad" or
# "main.py line 6: TypeError: ...".
_SUMMARY_RE = re.compile(
    r'^(?:(\S+)\s+)?[Ll]ine\s+(\d+):\s*'
    r'([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)):\s*(.*)$'
)

# Frames inside these are CodeUp/Python internals, never the learner's code.
_NOISE_HINTS = ("sandbox_runner.py", "<frozen", "site-packages", "importlib",
                "runpy.py", "<path>")

EXCEPTION_TYPES = (
    "SyntaxError", "IndentationError", "TabError", "NameError", "TypeError",
    "ValueError", "IndexError", "KeyError", "ZeroDivisionError", "AttributeError",
    "ImportError", "ModuleNotFoundError", "FileNotFoundError", "RecursionError",
)


def _basename(path: str) -> str:
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]


def _is_student_frame(filename: str, project_files: Optional[Dict[str, str]]) -> bool:
    if any(hint in filename for hint in _NOISE_HINTS):
        return False
    base = _basename(filename)
    if project_files and base in project_files:
        return True
    # Single-file runs label the user's code "<user>"; treat real .py names as
    # student code too unless they were filtered out above.
    return filename in ("<user>", "<string>", "<stdin>", "<unknown>") or base.endswith(".py")


def _parse_frames(text: str, project_files: Optional[Dict[str, str]]) -> List[Dict[str, Any]]:
    frames: List[Dict[str, Any]] = []
    for match in _FRAME_RE.finditer(text or ""):
        filename, line_s, func = match.group(1), match.group(2), match.group(3)
        frames.append({
            "file": filename,
            "base": _basename(filename),
            "line": int(line_s),
            "func": func or "<module>",
            "student": _is_student_frame(filename, project_files),
        })
    return frames


def _parse_exception(text: str):
    """Return (exception_type, message) from a traceback or one-line summary."""
    lines = [ln.rstrip() for ln in str(text or "").splitlines() if ln.strip()]
    # A full traceback ends with the exception line.
    for raw in reversed(lines):
        if raw.startswith(("File ", "Traceback", "^", "~")):
            continue
        m = _EXC_LINE_RE.match(raw.strip())
        if m:
            return _basename(m.group(1)), m.group(2).strip()
    # One-line stored summary: "Line N: Type: message".
    for raw in lines:
        m = _SUMMARY_RE.match(raw.strip())
        if m:
            return m.group(3), m.group(4).strip()
    return "", ""


def _summary_line(text: str) -> Optional[int]:
    m = _SUMMARY_RE.match(str(text or "").strip().splitlines()[0]) if str(text or "").strip() else None
    return int(m.group(2)) if m else None


def _code_line(line: Optional[int], code: str, file_text: Optional[str]) -> str:
    source = file_text if file_text is not None else code
    if not line or line < 1 or not source:
        return ""
    rows = str(source).splitlines()
    if line > len(rows):
        return ""
    return rows[line - 1].strip()


def _extract_value(exc_type: str, message: str) -> str:
    """Return the offending value ONLY when the error text names it; else ""."""
    msg = message or ""
    if exc_type == "ValueError":
        m = re.search(r"invalid literal for int\(\) with base \d+:\s*(.+)$", msg)
        if m:
            return m.group(1).strip()
        m = re.search(r"could not convert string to float:\s*(.+)$", msg)
        if m:
            return m.group(1).strip()
    if exc_type == "KeyError":
        return msg.strip()
    if exc_type == "NameError":
        m = re.search(r"name '([^']+)'", msg)
        if m:
            return f"'{m.group(1)}'"
    if exc_type == "ZeroDivisionError":
        return "zero"
    if exc_type == "ModuleNotFoundError" or exc_type == "ImportError":
        m = re.search(r"No module named '([^']+)'", msg)
        if m:
            return f"'{m.group(1)}'"
    return ""


def _explain(exc_type: str, message: str, line: Optional[int]) -> Dict[str, str]:
    """Beginner cause + next steps per exception type. Deterministic templates."""
    where = f"line {line}" if line else "the failing line"
    msg = message or ""

    if exc_type in ("IndentationError", "TabError"):
        cause = "A line is not indented the way Python expects."
        if "expected an indented block" in msg.lower():
            cause = "Python expected an indented block after a loop, if, or function header."
        elif "unexpected indent" in msg.lower():
            cause = "A line is indented more than Python expected."
        return {"cause": cause,
                "next": "Make sure lines inside a block are indented, usually by four spaces, and line up consistently."}
    if exc_type == "SyntaxError":
        return {"cause": f"Python could not understand the code structure near {where}.",
                "next": "Check for a missing colon, bracket, or quote on or just before that line."}
    if exc_type == "NameError":
        m = re.search(r"name '([^']+)'", msg)
        name = m.group(1) if m else "a name"
        return {"cause": f"The name {name} is used before it has a value.",
                "next": f"Define {name} before you use it, or check for a typo in the name."}
    if exc_type == "TypeError":
        return {"cause": "An operation was used on the wrong type of value.",
                "next": "Check the types on that line; you may need to convert a value with int(), str(), or float()."}
    if exc_type == "ValueError":
        if "int()" in msg or "invalid literal for int" in msg:
            return {"cause": "Python tried to convert a value to a whole number, but the value did not look like a number.",
                    "next": "Make sure the value contains only digits, or wrap the conversion in try/except to handle bad input."}
        if "float" in msg:
            return {"cause": "Python tried to convert a value to a decimal number, but the value did not look like a number.",
                    "next": "Make sure the value is numeric before converting, or use try/except."}
        return {"cause": "A function received a value of the right type but with content it cannot use.",
                "next": "Check the value passed on that line against what the function expects."}
    if exc_type == "IndexError":
        return {"cause": "The code asked for a list or string position that does not exist.",
                "next": "Check the length; valid positions go from 0 to length minus one."}
    if exc_type == "KeyError":
        key = msg.strip()
        return {"cause": f"The dictionary has no key {key}." if key else "The dictionary has no such key.",
                "next": "Check the key name, or use .get() to handle a missing key safely."}
    if exc_type == "ZeroDivisionError":
        return {"cause": "The code divided a number by zero.",
                "next": "Make sure the divider is not zero before dividing."}
    if exc_type in ("ModuleNotFoundError", "ImportError"):
        m = re.search(r"No module named '([^']+)'", msg)
        mod = m.group(1) if m else "a module"
        return {"cause": f"Python could not find the module {mod}.",
                "next": f"Check the spelling of {mod}, install it, or make sure the file is in your project."}
    if exc_type == "FileNotFoundError":
        return {"cause": "The program tried to open a file that does not exist.",
                "next": "Check the file name and path, and that the file is in the project."}
    if exc_type == "AttributeError":
        return {"cause": "The code used a method or attribute the value does not have.",
                "next": "Check the value's type and the spelling of the attribute on that line."}
    if exc_type == "RecursionError":
        return {"cause": "A function called itself too many times without stopping.",
                "next": "Add a base case that stops the recursion."}
    return {"cause": "The program stopped because of an error.",
            "next": "Read the error type and the named line, then check that line."}


def _call_chain(student_frames: List[Dict[str, Any]]) -> List[str]:
    """Describe cross-file calls, e.g. 'main.py called calculate in score.py'."""
    chain: List[str] = []
    for prev, nxt in zip(student_frames, student_frames[1:]):
        if prev["base"] != nxt["base"]:
            chain.append(f"{prev['base']} called {nxt['func']} in {nxt['base']}")
    return chain


def analyze(error_text: str, *, traceback_text: str = "", code: str = "",
            project_files: Optional[Dict[str, str]] = None,
            executed_file: str = "") -> Dict[str, Any]:
    """Parse an error into structured, non-visual facts. Never raises."""
    full = str(traceback_text or "").strip()
    one_line = str(error_text or "").strip()
    primary = full or one_line

    result: Dict[str, Any] = {
        "has_error": bool(primary),
        "exception_type": "", "message": "", "file": None, "line": None,
        "code_line": "", "stack_summary": [], "likely_cause": "",
        "beginner_explanation": "", "next_steps": "", "value": "",
        "call_chain": [], "full_trace_available": bool(full),
        "full_trace": full,
    }
    if not primary:
        return result

    exc_type, message = _parse_exception(primary)
    result["exception_type"] = exc_type
    result["message"] = message

    frames = _parse_frames(full, project_files) if full else _parse_frames(one_line, project_files)
    student = [f for f in frames if f["student"]]
    crash = student[-1] if student else (frames[-1] if frames else None)

    if crash:
        result["line"] = crash["line"]
        base = crash["base"]
        if base not in ("<user>", "<string>", "<stdin>", "<unknown>"):
            result["file"] = base
        elif executed_file:
            result["file"] = _basename(executed_file)
    if result["line"] is None:
        result["line"] = _summary_line(one_line) or _summary_line(full)
    if result["line"] is None:
        # Last resort: a bare "... on line N" mentioned anywhere in the text.
        m = re.search(r'\bline\s+(\d+)\b', primary, flags=re.IGNORECASE)
        if m:
            result["line"] = int(m.group(1))

    # Resolve the failing source line from the right file when we have it.
    file_text = None
    if result["file"] and project_files and result["file"] in project_files:
        file_text = project_files[result["file"]]
    result["code_line"] = _code_line(result["line"], code, file_text)

    result["stack_summary"] = [
        f"{f['base']} line {f['line']} in {f['func']}" for f in student
    ] or [f"{f['base']} line {f['line']} in {f['func']}" for f in frames]
    result["call_chain"] = _call_chain(student)

    explanation = _explain(exc_type, message, result["line"])
    result["likely_cause"] = explanation["cause"]
    result["beginner_explanation"] = explanation["cause"]
    result["next_steps"] = explanation["next"]
    result["value"] = _extract_value(exc_type, message)
    return result


def _where_sentence(analysis: Dict[str, Any]) -> str:
    file = analysis.get("file")
    line = analysis.get("line")
    if file and line:
        return f"The program crashed in {file}, line {line}."
    if line:
        return f"The program crashed at line {line}."
    return "The program ran into an error."


def narrate(analysis: Dict[str, Any], *, full: bool = False) -> str:
    """Short, beginner-friendly narration. Full traceback only when full=True."""
    if not analysis or not analysis.get("has_error"):
        return "There is no recent Python error to explain. Run your code first."

    parts: List[str] = [_where_sentence(analysis)]

    # Multi-file: explain who called whom before the cause.
    chain = analysis.get("call_chain") or []
    if chain:
        started = analysis["stack_summary"][0].split(" line ")[0] if analysis.get("stack_summary") else None
        if started:
            parts.append(f"The program started in {started}.")
        parts.append(". ".join(chain) + ".")

    exc_type = analysis.get("exception_type") or "an error"
    parts.append(f"The error is {exc_type}.")
    if analysis.get("beginner_explanation"):
        parts.append(analysis["beginner_explanation"])
    if analysis.get("code_line"):
        parts.append(f"The line was: {analysis['code_line']}")
    if analysis.get("next_steps"):
        parts.append(f"What to check next: {analysis['next_steps']}")

    if full and analysis.get("full_trace_available"):
        trace = analysis.get("full_trace") or "; ".join(analysis.get("stack_summary") or [])
        parts.append("Full traceback: " + trace)
    elif analysis.get("full_trace_available"):
        parts.append("Say 'read full traceback' to hear the raw traceback.")

    return "\n".join(p for p in parts if p).strip()


def brief(analysis: Dict[str, Any]) -> str:
    """One-shot 'Latest error:' summary for read-errors-only. No raw traceback."""
    if not analysis or not analysis.get("has_error"):
        return ""
    exc = analysis.get("exception_type") or "an error"
    cause = analysis.get("beginner_explanation") or ""
    return f"Latest error: {_where_sentence(analysis)} The error is {exc}. {cause}".strip()


def value_narration(analysis: Dict[str, Any]) -> str:
    """Answer 'what value caused this' without ever inventing a value."""
    if not analysis or not analysis.get("has_error"):
        return "There is no recent Python error to inspect. Run your code first."
    value = analysis.get("value")
    line = analysis.get("line")
    if value:
        return (f"The value that caused the {analysis['exception_type']} was {value}. "
                f"{analysis.get('beginner_explanation', '')}").strip()
    where = f" The failing line was: {analysis['code_line']}" if analysis.get("code_line") else \
            (f" The failing line was line {line}." if line else "")
    return ("I cannot see the exact runtime value from this error, but I can tell you what "
            f"failed. {analysis.get('beginner_explanation', '')}{where}").strip()
