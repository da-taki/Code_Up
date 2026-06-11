"""
Error replay: deterministic broken-vs-fixed code comparison.

Pure / Flask-free. Given the most recent broken code + its error and the fixed
code (the app reads these from its existing per-session run snapshots), explain
what changed and why it fixed the error — preferring a few targeted, friendly
explanations (indentation, added variable for a NameError, added colon) and
falling back to a plain line diff. Nothing is invented; no cloud AI is involved.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional

NO_HISTORY = ("I do not have a recent broken-and-fixed version to compare yet. "
              "Run code with an error, fix it, and run again.")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def diff_lines(before: str, after: str) -> List[Dict[str, Any]]:
    """Line-level diff with indentation info."""
    before_lines = (before or "").splitlines()
    after_lines = (after or "").splitlines()
    changes: List[Dict[str, Any]] = []
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                b = before_lines[i1 + k] if i1 + k < i2 else ""
                a = after_lines[j1 + k] if j1 + k < j2 else ""
                changes.append({"line": j1 + k + 1, "before": b, "after": a, "kind": "changed",
                                "before_indent": _indent(b), "after_indent": _indent(a)})
        elif tag == "delete":
            for k in range(i1, i2):
                changes.append({"line": k + 1, "before": before_lines[k], "after": "", "kind": "removed",
                                "before_indent": _indent(before_lines[k]), "after_indent": 0})
        elif tag == "insert":
            for k in range(j1, j2):
                changes.append({"line": k + 1, "before": "", "after": after_lines[k], "kind": "added",
                                "before_indent": 0, "after_indent": _indent(after_lines[k])})
    return changes


def _nameerror_name(error: str) -> str:
    m = re.search(r"NameError: name '([^']+)' is not defined", error or "")
    return m.group(1) if m else ""


def _assigns_name(code: str, name: str) -> bool:
    return bool(name) and bool(re.search(rf"^\s*{re.escape(name)}\s*=(?!=)", code or "", re.MULTILINE))


def explain(before: str, after: str, error: str = "") -> Dict[str, Any]:
    """Compare ``before`` and ``after`` and explain the fix."""
    before = before or ""
    after = after or ""
    if not before.strip() or not after.strip():
        return {"has_comparison": False, "summary": NO_HISTORY, "speech": NO_HISTORY,
                "changed_lines": [], "explanation": NO_HISTORY}

    changes = diff_lines(before, after)
    changed_lines = [c["line"] for c in changes]

    # 1) Indentation fix: a line kept its text but gained indentation.
    for c in changes:
        if (c["kind"] == "changed" and c["before"].strip() == c["after"].strip()
                and c["after_indent"] > c["before_indent"] and c["before"].strip()):
            snippet = c["after"].strip()
            kw = "print" if snippet.startswith("print") else "line"
            expl = (f"The {kw} on line {c['line']} was not indented before. "
                    f"Now it is indented by {c['after_indent']} spaces, so it sits inside the block above. "
                    "That fixes the indentation error.")
            return _result(expl, changed_lines)

    # 2) NameError fixed by adding the missing variable.
    missing = _nameerror_name(error)
    if missing and not _assigns_name(before, missing) and _assigns_name(after, missing):
        for c in changes:
            if c["kind"] == "added" and re.match(rf"\s*{re.escape(missing)}\s*=", c["after"]):
                expl = (f"Before, {missing} was used but never defined, which caused the NameError. "
                        f"You added {c['after'].strip()} on line {c['line']}, so {missing} now has a value.")
                return _result(expl, changed_lines)
        expl = (f"You defined {missing}, which was missing before. "
                "That fixes the NameError for that name.")
        return _result(expl, changed_lines)

    # 3) Added colon (common SyntaxError fix).
    for c in changes:
        if (c["kind"] == "changed" and c["before"].rstrip().rstrip(":") == c["after"].rstrip().rstrip(":")
                and not c["before"].rstrip().endswith(":") and c["after"].rstrip().endswith(":")):
            expl = (f"Line {c['line']} was missing a colon at the end. "
                    "Adding the colon fixes the syntax error.")
            return _result(expl, changed_lines)

    # 4) Fallback: plain line diff summary.
    if not changes:
        msg = "The two versions look the same to me; I see no changes to explain."
        return {"has_comparison": True, "summary": msg, "speech": msg,
                "changed_lines": [], "explanation": msg}
    parts = [f"{_count(len(changes))} changed."]
    for c in changes[:4]:
        if c["kind"] == "changed":
            parts.append(f"Line {c['line']} changed to \"{c['after'].strip()}\".")
        elif c["kind"] == "added":
            parts.append(f"Line {c['line']} was added: \"{c['after'].strip()}\".")
        elif c["kind"] == "removed":
            parts.append(f"A line was removed: \"{c['before'].strip()}\".")
    expl = " ".join(parts)
    return _result(expl, changed_lines)


def _count(n: int) -> str:
    return f"{n} line{'s' if n != 1 else ''} " + ("were" if n != 1 else "was")


def _result(explanation: str, changed_lines: List[int]) -> Dict[str, Any]:
    return {"has_comparison": True, "summary": explanation, "speech": explanation,
            "changed_lines": sorted(set(changed_lines)), "explanation": explanation}


def from_snapshot(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build an explanation from an app run-snapshot dict (error_code/success_code)."""
    snapshot = snapshot or {}
    before = snapshot.get("error_code") or ""
    after = snapshot.get("success_code") or ""
    error = snapshot.get("error_msg") or ""
    if not before or not after:
        return {"has_comparison": False, "summary": NO_HISTORY, "speech": NO_HISTORY,
                "changed_lines": [], "explanation": NO_HISTORY}
    return explain(before, after, error)
