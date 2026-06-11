"""
Project report / teacher-handoff support for CodeUp.

Deterministic and Flask-free. Builds a concise, fact-grounded summary of the
learner's current program or multi-file project: file roles, detected Python
concepts (from the AST), how to run it, requirements, and the last run result.
Nothing is invented — every statement is derived from the actual files, real
metadata, or the session's stored run output/error. The Flask layer may pass the
result through Key 2 only to soften wording, grounded in these facts.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional

# Friendly concept labels in a stable, beginner-first order.
_CONCEPT_ORDER = [
    "print output", "variables", "loops", "conditionals (if/else)", "functions",
    "classes", "imports", "lists", "dictionaries", "list comprehension",
    "f-strings", "error handling", "file reading",
]


def detect_python_concepts(code: str) -> List[str]:
    """Return the beginner Python concepts present in ``code`` (AST-based)."""
    found = set()
    text = code or ""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Fall back to light regex cues so broken code still reports something.
        if re.search(r"\bprint\s*\(", text):
            found.add("print output")
        if re.search(r"^\s*\w+\s*=(?!=)", text, re.MULTILINE):
            found.add("variables")
        if re.search(r"\bfor\b|\bwhile\b", text):
            found.add("loops")
        if re.search(r"\bif\b", text):
            found.add("conditionals (if/else)")
        if re.search(r"\bdef\b", text):
            found.add("functions")
        return _ordered(found)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            found.add("print output")
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            found.add("variables")
        elif isinstance(node, (ast.For, ast.While, ast.AsyncFor)):
            found.add("loops")
        elif isinstance(node, ast.If):
            found.add("conditionals (if/else)")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.add("functions")
        elif isinstance(node, ast.ClassDef):
            found.add("classes")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found.add("imports")
        elif isinstance(node, (ast.List, ast.ListComp)):
            found.add("lists")
            if isinstance(node, ast.ListComp):
                found.add("list comprehension")
        elif isinstance(node, (ast.Dict, ast.DictComp)):
            found.add("dictionaries")
        elif isinstance(node, ast.JoinedStr):
            found.add("f-strings")
        elif isinstance(node, (ast.Try, ast.Raise)):
            found.add("error handling")
        elif isinstance(node, ast.With):
            found.add("file reading")
    return _ordered(found)


def _ordered(found) -> List[str]:
    return [c for c in _CONCEPT_ORDER if c in found]


def _looks_like_python(path: str) -> bool:
    return path.endswith(".py")


def _file_role(path: str, content: str) -> str:
    """A short, deterministic guess of a file's role from its name/content."""
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    if name in ("main.py", "app.py", "run.py", "__main__.py"):
        return "entry point / runner"
    if name == "requirements.txt":
        return "dependency list"
    if name.startswith("test_") or "/tests/" in low or low.startswith("tests/"):
        return "tests"
    if name.endswith(".md"):
        return "documentation"
    if name.endswith(".csv"):
        return "sample data"
    if name.endswith(".json"):
        return "data / configuration"
    if _looks_like_python(low):
        if 'if __name__ == "__main__"' in content or "if __name__ == '__main__'" in content:
            return "runnable module"
        return "helper module"
    return "supporting file"


def summarize_files(project_files: Dict[str, str]) -> List[Dict[str, Any]]:
    """One summary record per file: path, role, line count, detected concepts."""
    summaries: List[Dict[str, Any]] = []
    for path in sorted((project_files or {}).keys()):
        content = project_files[path] if isinstance(project_files.get(path), str) else str(project_files.get(path) or "")
        record = {
            "path": path,
            "role": _file_role(path, content),
            "lines": len(content.splitlines()),
            "concepts": detect_python_concepts(content) if _looks_like_python(path) else [],
        }
        summaries.append(record)
    return summaries


def _run_instruction(is_project: bool, entry: str, files: Dict[str, str]) -> str:
    if is_project:
        return (f"Run {entry} from the project root (say \"run {entry}\", or open it and press Ctrl+Enter). "
                "Imports resolve from the project root.")
    return "Run it by saying \"run\", pressing Ctrl+Enter, or pressing the Run button."


def _last_run_line(mem: Optional[Dict[str, Any]]) -> str:
    mem = mem or {}
    ok = mem.get("last_run_ok")
    err = (mem.get("last_run_error") or "").strip()
    out = (mem.get("last_run_output") or "").strip()
    if ok is True and out:
        first = out.splitlines()[0][:120]
        return f"Last run succeeded. Output started with: {first}"
    if ok is False and err:
        return f"Last run hit an error: {err.splitlines()[0][:140]}"
    if out:
        return f"Last output started with: {out.splitlines()[0][:120]}"
    return "No run has been recorded yet this session."


def build_project_report(project_state: Dict[str, Any],
                         mem: Optional[Dict[str, Any]] = None,
                         verbosity: str = "normal") -> Dict[str, Any]:
    """Build a fact-grounded report for the current program/project.

    ``project_state`` keys (all optional): ``is_project`` (bool), ``name``,
    ``files`` ({path: content}), ``code`` (single-file editor code), ``entry``,
    ``requirements`` (list). ``mem`` is the session working-memory dict.
    ``verbosity`` (concise/normal/detailed/beginner/expert) tunes the spoken
    summary length only — the written report always contains the full facts.
    """
    project_state = project_state or {}
    is_project = bool(project_state.get("is_project")) and bool(project_state.get("files"))

    if is_project:
        files = {p: (c if isinstance(c, str) else str(c or "")) for p, c in project_state["files"].items()}
        title = str(project_state.get("name") or "CodeUp project").strip()[:80]
        entry = project_state.get("entry") or "main.py"
    else:
        code = str(project_state.get("code") or "")
        files = {"main.py": code} if code.strip() else {}
        title = "CodeUp single-file program"
        entry = "main.py"

    file_summaries = summarize_files(files)
    all_concepts: List[str] = []
    for record in file_summaries:
        for concept in record["concepts"]:
            if concept not in all_concepts:
                all_concepts.append(concept)
    all_concepts = _ordered(set(all_concepts))

    requirements = [str(r).strip() for r in (project_state.get("requirements") or []) if str(r).strip()]
    if not requirements:
        # Infer obvious third-party libs from imports as a fallback.
        third = {"numpy", "pandas", "matplotlib"}
        joined = "\n".join(files.values())
        requirements = sorted({lib for lib in third if re.search(rf"\bimport\s+{lib}\b|\bfrom\s+{lib}\b", joined)})

    run_instruction = _run_instruction(is_project, entry, files)
    last_run = _last_run_line(mem)

    report_md = _render_markdown(title, is_project, file_summaries, all_concepts,
                                 requirements, run_instruction, last_run)
    speech = _render_speech(title, is_project, file_summaries, all_concepts,
                            run_instruction, verbosity)

    return {
        "success": True,
        "has_content": bool(files),
        "title": title,
        "is_project": is_project,
        "files": file_summaries,
        "concepts": all_concepts,
        "requirements": requirements,
        "run_instruction": run_instruction,
        "last_run": last_run,
        "report_md": report_md,
        "speech": speech,
    }


def _render_markdown(title, is_project, file_summaries, concepts, requirements,
                     run_instruction, last_run) -> str:
    lines = [f"# {title}", ""]
    if not file_summaries:
        lines.append("There is no code or project to report on yet.")
        return "\n".join(lines)
    kind = "Multi-file project" if is_project else "Single-file program"
    lines += [f"_{kind}, generated by CodeUp from the learner's current project._", ""]
    lines += ["## Files", ""]
    for record in file_summaries:
        concept_note = f" — concepts: {', '.join(record['concepts'])}" if record["concepts"] else ""
        lines.append(f"- `{record['path']}` ({record['role']}, {record['lines']} lines){concept_note}")
    lines += ["", "## How to run", "", run_instruction, ""]
    lines += ["## Python concepts used", ""]
    lines.append(", ".join(concepts) if concepts else "No standard concepts detected yet.")
    if requirements:
        lines += ["", "## Requirements", "", ", ".join(requirements)]
    lines += ["", "## Last run", "", last_run]
    lines += ["", "---", "", "_Generated by CodeUp from the learner's current project._"]
    return "\n".join(lines)


def _render_speech(title, is_project, file_summaries, concepts, run_instruction,
                   verbosity: str = "normal") -> str:
    if not file_summaries:
        return "There is no code or project to report on yet. Create or generate some code first."
    if is_project:
        names = ", ".join(r["path"] for r in file_summaries[:6])
        head = f"{title}. It has {len(file_summaries)} files: {names}."
    else:
        head = f"{title}."
    # Concise/expert: keep it to the essentials (what it is + how to run).
    if str(verbosity or "").lower() in ("concise", "expert"):
        return f"{head} {run_instruction}"
    concept_part = (" It uses " + ", ".join(concepts) + ".") if concepts else ""
    return f"{head}{concept_part} {run_instruction}"
