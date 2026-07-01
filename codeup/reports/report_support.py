
from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional

from codeup.learning import learning_recap

_CONCEPT_ORDER = [
    "print output", "variables", "loops", "conditionals (if/else)", "functions",
    "classes", "imports", "lists", "dictionaries", "list comprehension",
    "f-strings", "error handling", "file reading",
]


def detect_python_concepts(code: str) -> List[str]:
    found = set()
    text = code or ""
    try:
        tree = ast.parse(text)
    except SyntaxError:
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


_NUM_WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
              6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _num_word(n: int) -> str:
    return _NUM_WORDS.get(n, str(n))


def _oxford(items: List[str]) -> str:
    items = [str(i) for i in items]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _flatten_output(out: str, limit: int = 200) -> str:
    lines = [ln.strip() for ln in str(out or "").splitlines() if ln.strip()]
    joined = ", ".join(lines)
    return (joined[:limit] + "...") if len(joined) > limit else joined


def _sentence_end(text: str) -> str:
    return "" if str(text or "")[-1:] in ".!?:" else "."


def _call_is(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == name)


def _assign_target_name(node: ast.AST) -> str:
    target = None
    if isinstance(node, ast.Assign) and node.targets:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign):
        target = node.target
    return target.id if isinstance(target, ast.Name) else ""


def _for_range_info(node: ast.For):
    it = node.iter
    if not _call_is(it, "range"):
        return None
    args = it.args

    def lit(a):
        return a.value if isinstance(a, ast.Constant) and isinstance(a.value, int) else None

    if len(args) == 1 and lit(args[0]) is not None:
        n = lit(args[0])
        return (0, n, f"range({n})")
    if len(args) >= 2 and lit(args[0]) is not None and lit(args[1]) is not None:
        a, b = lit(args[0]), lit(args[1])
        return (a, b, f"range({a}, {b})")
    return None


def _range_values_phrase(start: int, stop: int) -> str:
    vals = list(range(start, stop))
    if not vals:
        return "no values"
    if len(vals) <= 6:
        return _oxford([str(v) for v in vals])
    return f"{vals[0]}, {vals[1]}, and so on up to {vals[-1]}"


def _block_has_print(node: ast.AST) -> bool:
    return any(_call_is(c, "print") for c in ast.walk(node))


def _name_in_print(tree: ast.AST, name: str) -> bool:
    if not name:
        return False
    for node in ast.walk(tree):
        if _call_is(node, "print"):
            for arg in ast.walk(node):
                if isinstance(arg, ast.Name) and arg.id == name:
                    return True
    return False


def describe_program_behavior(code: str) -> List[str]:
    code = code or ""
    if not code.strip():
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ["The program currently has a syntax error, so it will not run until that line is fixed."]

    sents: List[str] = []
    first_for = next((n for n in ast.walk(tree) if isinstance(n, ast.For)), None)
    first_while = next((n for n in ast.walk(tree) if isinstance(n, ast.While)), None)
    functions = [n for n in ast.iter_child_nodes(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n for n in ast.iter_child_nodes(tree) if isinstance(n, ast.ClassDef)]
    assigns = [n for n in ast.iter_child_nodes(tree)
               if isinstance(n, (ast.Assign, ast.AnnAssign)) and _assign_target_name(n)]
    has_if = any(isinstance(n, ast.If) for n in ast.walk(tree))
    has_try = any(isinstance(n, (ast.Try, ast.Raise)) for n in ast.walk(tree))
    has_print = any(_call_is(n, "print") for n in ast.walk(tree))

    if first_for is not None:
        info = _for_range_info(first_for)
        inner_print = _block_has_print(first_for)
        action = "a print statement" if inner_print else "an action"
        if info is not None:
            start, stop, disp = info
            count = max(0, stop - start)
            sents.append(f"It uses a for loop to repeat {action} {_num_word(count)} times.")
            sents.append(f"The loop is controlled by {disp}, so the values are {_range_values_phrase(start, stop)}.")
        else:
            sents.append(f"It uses a for loop to repeat {action}.")
        if inner_print:
            sents.append("The indented print line is the action the loop repeats.")
    elif first_while is not None:
        sents.append("It uses a while loop that repeats an action while its condition stays true.")

    if functions:
        names = _oxford([f.name for f in functions[:3]])
        if len(functions) == 1:
            sents.append(f"It defines a function called {names} that groups steps you can reuse.")
        else:
            sents.append(f"It defines functions called {names}.")

    if classes:
        cnames = _oxford([c.name for c in classes[:3]])
        if len(classes) == 1:
            sents.append(f"It defines a class called {cnames}, which bundles related data and methods.")
        else:
            sents.append(f"It defines classes called {cnames}.")

    if assigns and first_for is None and not functions and not classes:
        name = _assign_target_name(assigns[0])
        if name and _name_in_print(tree, name):
            sents.append(f"It stores a value in the variable {name} and then prints it.")
        elif has_print:
            sents.append(f"It stores a value in the variable {name}, then prints a result.")
        else:
            sents.append(f"It stores a value in the variable {name} and uses it later.")

    if has_if and first_for is None and first_while is None:
        sents.append("It uses an if statement to decide which lines run.")

    if has_try:
        sents.append("It uses try and except to handle errors without crashing.")

    if not sents and has_print:
        sents.append("It prints output to the screen.")
    if not sents:
        sents.append("It runs a few simple top-level statements.")
    return sents


def _multifile_behavior(files: Dict[str, str], entry: str) -> List[str]:
    if not files:
        return []
    entry = entry if entry in files else next(iter(sorted(files)), "")
    sents: List[str] = []
    if entry:
        sents.append(f"The entry point is {entry}; start reading there.")
    entry_code = files.get(entry, "")
    entry_stem = entry[:-3] if entry.endswith(".py") else entry
    local = []
    for path in sorted(files):
        if not path.endswith(".py"):
            continue
        stem = path.rsplit("/", 1)[-1][:-3]
        if stem and stem != entry_stem and re.search(rf"\b(?:import|from)\s+{re.escape(stem)}\b", entry_code):
            local.append(stem)
    if local:
        sents.append(f"{entry} imports {_oxford(local)} to use their functions, so the files work together as one program.")
    return sents


def _last_output_speech(mem: Optional[Dict[str, Any]]) -> str:
    mem = mem or {}
    ok = mem.get("last_run_ok")
    err = (mem.get("last_run_error") or "").strip()
    out = (mem.get("last_run_output") or "").strip()
    if ok is True and out:
        flat = _flatten_output(out)
        return f"The last successful output was {flat}{_sentence_end(flat)}"
    if ok is False and err:
        first = err.splitlines()[0][:120]
        return f"The last run hit an error: {first}{_sentence_end(first)}"
    if out:
        flat = _flatten_output(out)
        return f"The last output was {flat}{_sentence_end(flat)}"
    return ""


def _looks_like_python(path: str) -> bool:
    return path.endswith(".py")


def _file_role(path: str, content: str) -> str:
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
        third = {"numpy", "pandas", "matplotlib"}
        joined = "\n".join(files.values())
        requirements = sorted({lib for lib in third if re.search(rf"\bimport\s+{lib}\b|\bfrom\s+{lib}\b", joined)})

    run_instruction = _run_instruction(is_project, entry, files)
    last_run = _last_run_line(mem)
    last_output_phrase = _last_output_speech(mem)

    if is_project:
        behavior = _multifile_behavior(files, entry)
    else:
        behavior = describe_program_behavior(next(iter(files.values()), ""))

    session_features = [str(f) for f in (mem or {}).get("features_used", []) if str(f)]
    next_step = learning_recap.suggest_next_step(mem or {})
    errors_fixed = _errors_fixed_line(mem or {})

    report_md = _render_markdown(title, is_project, file_summaries, all_concepts,
                                 requirements, run_instruction, last_run, behavior,
                                 session_features, errors_fixed, next_step)
    speech, speech_more = _render_speech(title, is_project, file_summaries, all_concepts,
                                         run_instruction, verbosity, behavior, last_output_phrase,
                                         session_features, errors_fixed, next_step)

    return {
        "success": True,
        "has_content": bool(files),
        "title": title,
        "is_project": is_project,
        "files": file_summaries,
        "concepts": all_concepts,
        "behavior": behavior,
        "requirements": requirements,
        "run_instruction": run_instruction,
        "last_run": last_run,
        "session_features": session_features,
        "errors_fixed": errors_fixed,
        "next_step": next_step,
        "report_md": report_md,
        "speech": speech,
        "speech_more": speech_more,
    }


def _render_markdown(title, is_project, file_summaries, concepts, requirements,
                     run_instruction, last_run, behavior=None, session_features=None,
                     errors_fixed: str = "", next_step: str = "") -> str:
    lines = [f"# {title}", ""]
    if not file_summaries:
        lines.append("There is no code or project to report on yet.")
        return "\n".join(lines)
    kind = "Multi-file project" if is_project else "Single-file program"
    lines += [f"_{kind}, generated by CodeUp from the learner's current project._", ""]
    if behavior:
        lines += ["## What this program does", "", " ".join(behavior), ""]
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
    lines += ["", "## Errors fixed", "", errors_fixed or "No fixed error has been recorded yet."]
    lines += ["", "## Commands and session history", ""]
    lines.append(", ".join(session_features or []) if session_features else "No voice-command history has been recorded yet.")
    lines += ["", "## Next steps", "", next_step or "Run the program and ask for a walkthrough."]
    lines += ["", "---", "", "_Generated by CodeUp from the learner's current project._"]
    return "\n".join(lines)


def _render_speech(title, is_project, file_summaries, concepts, run_instruction,
                   verbosity: str = "normal", behavior=None, last_output: str = "",
                   session_features=None, errors_fixed: str = "", next_step: str = ""):
    if not file_summaries:
        return ("There is no code or project to report on yet. Create or generate some code first.", "")
    behavior = behavior or []
    if is_project:
        names = ", ".join(r["path"] for r in file_summaries[:6])
        head = f"Project report ready. This is a multi-file Python project with {len(file_summaries)} files: {names}."
    else:
        head = "Project report ready. This is a single-file Python program."
    if str(verbosity or "").lower() in ("concise", "expert"):
        return (f"{head} {run_instruction}", "")
    behavior_part = (" " + " ".join(behavior)) if behavior else (
        (" It uses " + ", ".join(concepts) + ".") if concepts else "")
    concept_part = (" Concepts used: " + ", ".join(concepts) + ".") if concepts else ""
    last_part = (" " + last_output) if last_output else ""
    summary = f"{head}{behavior_part}{concept_part} {run_instruction}{last_part} Say more to hear next steps."
    more_bits: List[str] = []
    if errors_fixed:
        more_bits.append(errors_fixed)
    if session_features:
        more_bits.append("Session history includes " + ", ".join(session_features[:4]) + ".")
    if next_step:
        more_bits.append("Next step: " + next_step)
    return (summary, " ".join(more_bits).strip())


def _errors_fixed_line(mem: Dict[str, Any]) -> str:
    features = [str(f) for f in mem.get("features_used", []) if str(f)]
    last_error = str(mem.get("last_run_error") or "").strip()
    ran_ok = mem.get("last_run_ok")
    if "debugged errors" in features and ran_ok is True:
        return "The learner fixed an earlier error and the latest run succeeded."
    if last_error and ran_ok is False:
        return f"An error is still open: {last_error.splitlines()[0][:140]}"
    if last_error:
        return f"Recent error context: {last_error.splitlines()[0][:140]}"
    return ""
