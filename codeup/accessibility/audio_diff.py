"""Deterministic, non-visual Audio Diff Review.

Sighted programmers scan red/green diff lines. A visually impaired learner cannot.
This module turns a before/after pair (single file or a multi-file project) into a
structured, spoken-friendly diff: which lines changed, what each change means in
plain English, and a conservative risk label (low / medium / high).

Everything here is deterministic: difflib + small line-level heuristics. No AI.
An AI layer may add colour later, but it must be grounded in what summarize_change
returns. Risk labels are intentionally conservative and never overclaim.
"""

import difflib
import re
from typing import Any, Dict, List, Optional

__all__ = [
    "diff_lines", "summarize_change", "narrate", "narrate_before_after",
    "change_meaning", "project_diff", "narrate_project", "RISK_LEVELS",
]

RISK_LEVELS = ("low", "medium", "high")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def diff_lines(before: str, after: str) -> List[Dict[str, Any]]:
    """Line-level diff as a list of {kind, line, before, after} records."""
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
                changes.append({"kind": "changed", "line": j1 + k + 1,
                                "before": b, "after": a})
        elif tag == "delete":
            for k in range(i1, i2):
                changes.append({"kind": "removed", "line": k + 1,
                                "before": before_lines[k], "after": ""})
        elif tag == "insert":
            for k in range(j1, j2):
                changes.append({"kind": "added", "line": j1 + (k - j1) + 1,
                                "before": "", "after": after_lines[k]})
    return changes


def change_meaning(change: Dict[str, Any]) -> str:
    """Plain-English meaning for one line change."""
    kind = change["kind"]
    line = change["line"]
    before = (change.get("before") or "").strip()
    after = (change.get("after") or "").strip()
    if kind == "added":
        return f"Line {line} adds: {after}" if after else f"Line {line} adds a blank line."
    if kind == "removed":
        return f"Line {line} removes: {before}" if before else f"Line {line} removes a blank line."
    # changed
    if before and after and before.lstrip() == after.lstrip() and _indent(change["after"]) != _indent(change["before"]):
        direction = "more" if _indent(change["after"]) > _indent(change["before"]) else "less"
        return f"Line {line} is now indented {direction}: {after}"
    return f"Line {line} changes from \"{before}\" to \"{after}\"."


_CONDITION_RE = re.compile(r"^\s*(if|elif|while)\b")
_LOOP_RE = re.compile(r"^\s*for\b|range\s*\(")
_DEF_RE = re.compile(r"^\s*def\b")
_RETURN_RE = re.compile(r"^\s*return\b")
_IMPORT_RE = re.compile(r"^\s*(import|from)\b")
_TRY_RE = re.compile(r"^\s*(try|except|finally)\b")
_IO_RE = re.compile(r"\bopen\s*\(")
_INPUT_RE = re.compile(r"\binput\s*\(")


def _risk(before: str, after: str, changes: List[Dict[str, Any]], files_changed: int) -> Dict[str, str]:
    removed = [c for c in changes if c["kind"] == "removed"]
    touched = [(c.get("before") or "") + "\n" + (c.get("after") or "") for c in changes]
    blob = "\n".join(touched)

    # High risk: broad or behaviour-critical edits. Conservative.
    if files_changed > 1:
        return {"level": "high", "reason": f"It changes {files_changed} files at once."}
    if len(removed) >= 5:
        return {"level": "high", "reason": f"It removes {len(removed)} lines."}
    if any(_TRY_RE.match(c.get("before", "")) for c in removed):
        return {"level": "high", "reason": "It removes error handling (a try/except block)."}
    if _IO_RE.search(blob):
        return {"level": "high", "reason": "It changes file input or output."}
    if _INPUT_RE.search(blob):
        return {"level": "high", "reason": "It changes how the program reads user input."}

    # Medium risk: changes that alter behaviour on a line.
    changed_or_added = [c for c in changes if c["kind"] in ("changed", "added")]
    for c in changed_or_added:
        text = c.get("after", "") or c.get("before", "")
        if _CONDITION_RE.match(text):
            return {"level": "medium", "reason": "It changes a condition, so which branch runs may change."}
        if _LOOP_RE.search(text):
            return {"level": "medium", "reason": "It changes a loop, so how many times it runs may change."}
        if _DEF_RE.match(text):
            return {"level": "medium", "reason": "It changes a function definition or its parameters."}
        if _RETURN_RE.match(text):
            return {"level": "medium", "reason": "It changes what a function returns."}
        if _IMPORT_RE.match(text):
            return {"level": "medium", "reason": "It changes the imports the program depends on."}

    return {"level": "low", "reason": "It is a small, local change."}


def summarize_change(before: str, after: str, *, file_name: str = "",
                     reason: str = "") -> Dict[str, Any]:
    """Structured diff for a single before/after pair."""
    changes = diff_lines(before, after)
    risk = _risk(before, after, changes, files_changed=1)
    added = sum(1 for c in changes if c["kind"] == "added")
    removed = sum(1 for c in changes if c["kind"] == "removed")
    changed = sum(1 for c in changes if c["kind"] == "changed")
    parts = []
    if added:
        parts.append(f"{added} added")
    if removed:
        parts.append(f"{removed} removed")
    if changed:
        parts.append(f"{changed} changed")
    summary = (", ".join(parts) + " line" + ("s" if (added + removed + changed) != 1 else "")) if parts else "no changes"
    return {
        "file": file_name or "",
        "files_changed": 1 if changes else 0,
        "reason": reason or "",
        "changes": changes,
        "added": added, "removed": removed, "changed": changed,
        "risk": risk["level"], "risk_reason": risk["reason"],
        "summary": summary,
        "before": before or "", "after": after or "",
    }


def narrate(change: Dict[str, Any], *, max_changes: int = 4) -> str:
    """Beginner-friendly spoken diff for one file change."""
    if not change or not change.get("changes"):
        return "I do not see any code change to review yet."
    file_label = change.get("file") or "the editor"
    lines = [f"I changed 1 file: {file_label}." if change.get("file")
             else "Here is what changed in the editor."]
    shown = change["changes"][:max_changes]
    for i, c in enumerate(shown, 1):
        lines.append(f"Change {i}: {change_meaning(c)}")
    extra = len(change["changes"]) - len(shown)
    if extra > 0:
        lines.append(f"And {extra} more change{'s' if extra != 1 else ''}.")
    lines.append(f"Risk: {change['risk'].capitalize()}. {change['risk_reason']}")
    lines.append("Say accept this change, reject this change, read before and after, or explain more.")
    return "\n".join(lines)


def narrate_before_after(change: Dict[str, Any]) -> str:
    if not change:
        return "There is no change to compare yet."
    before = (change.get("before") or "").strip() or "(empty)"
    after = (change.get("after") or "").strip() or "(empty)"
    return f"Before:\n{before}\n\nAfter:\n{after}"


def project_diff(before_files: Dict[str, str], after_files: Dict[str, str]) -> Dict[str, Any]:
    """Multi-file diff summary across a project's files."""
    before_files = before_files or {}
    after_files = after_files or {}
    names = sorted(set(before_files) | set(after_files))
    file_changes: List[Dict[str, Any]] = []
    for name in names:
        b = before_files.get(name, "")
        a = after_files.get(name, "")
        if b == a:
            continue
        change = summarize_change(b, a, file_name=name)
        if name not in before_files:
            change["summary"] = "new file"
        elif name not in after_files:
            change["summary"] = "deleted file"
            change["risk"] = "high"
            change["risk_reason"] = "The whole file was deleted."
        file_changes.append(change)
    riskiest = None
    order = {"low": 0, "medium": 1, "high": 2}
    for fc in file_changes:
        if riskiest is None or order[fc["risk"]] > order[riskiest["risk"]]:
            riskiest = fc
    return {
        "files_changed": len(file_changes),
        "file_changes": file_changes,
        "riskiest": riskiest,
    }


def narrate_project(project_change: Dict[str, Any]) -> str:
    fcs = project_change.get("file_changes") or []
    if not fcs:
        return "No files changed."
    lines = [f"I changed {len(fcs)} file{'s' if len(fcs) != 1 else ''}."]
    for i, fc in enumerate(fcs, 1):
        lines.append(f"File {i}: {fc['file']}. {fc['summary']}.")
    riskiest = project_change.get("riskiest")
    if riskiest and riskiest["risk"] != "low":
        lines.append(f"The riskiest change is in {riskiest['file']}: {riskiest['risk_reason']}")
    return "\n".join(lines)
