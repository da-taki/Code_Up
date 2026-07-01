"""Deterministic, non-visual Project Map.

Sighted programmers lean on file trees, tabs, breadcrumbs and outlines. This
module turns a single-file editor buffer or a session-scoped multi-file project
into a spoken map: which files exist, what each one defines, how they import one
another, and where the program starts.

Everything here is deterministic and AST-based. No AI, no network. If an AI layer
later wants to add colour, it must be grounded in the facts this module returns
(see build_map), never invent structure.
"""

import ast
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["build_map", "narrate", "find_file_for"]


def _stem(path: str) -> str:
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-3] if name.lower().endswith(".py") else name


def _is_py(path: str) -> bool:
    return str(path).lower().endswith(".py")


def _safe_parse(code: str) -> Optional[ast.AST]:
    try:
        return ast.parse(code or "")
    except SyntaxError:
        return None
    except (ValueError, TypeError):
        return None


def _dedupe(items: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _top_functions(tree: ast.AST) -> List[str]:
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _top_classes(tree: ast.AST) -> List[str]:
    return [n.name for n in tree.body if isinstance(n, ast.ClassDef)]


def _top_assignments(tree: ast.AST) -> List[str]:
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return _dedupe(names)


def _imported_roots(tree: ast.AST) -> List[str]:
    """Top-level module roots imported by this file (e.g. 'math', 'questions')."""
    roots: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # Only absolute imports name another top-level module by root.
            if node.module and node.level == 0:
                roots.append(node.module.split(".")[0])
    return _dedupe(roots)


def _has_top_level_code(tree: ast.AST) -> bool:
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef, ast.Assign,
                             ast.AnnAssign)):
            continue
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue  # module docstring
        return True
    return False


def _main_guard_line(tree: ast.AST) -> Optional[int]:
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                return node.lineno
            for comparator in node.test.comparators:
                if isinstance(comparator, ast.Name) and comparator.id == "__name__":
                    return node.lineno
    return None


def _first_exec_line(tree: ast.AST) -> Optional[int]:
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue  # module docstring
        return node.lineno
    return None


def _files_from_state(project_state: Dict[str, Any]) -> Tuple[Dict[str, str], bool]:
    if (project_state.get("is_project")
            and isinstance(project_state.get("files"), dict)
            and project_state["files"]):
        files = {str(k): (v if isinstance(v, str) else str(v or ""))
                 for k, v in project_state["files"].items()}
        return files, True
    code = project_state.get("code")
    return {"": str(code or "")}, False


def _analyze_file(name: str, code: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "name": name,
        "py": _is_py(name) or name == "",
        "parse_error": False,
        "functions": [], "classes": [], "variables": [],
        "imports": [], "loops": 0,
        "main_guard_line": None, "first_exec_line": None,
        "has_top_level_code": False,
        "line_count": len([ln for ln in str(code or "").splitlines()]),
    }
    if not info["py"]:
        return info
    tree = _safe_parse(code)
    if tree is None:
        info["parse_error"] = True
        return info
    info["functions"] = _top_functions(tree)
    info["classes"] = _top_classes(tree)
    info["variables"] = _top_assignments(tree)
    info["imports"] = _imported_roots(tree)
    info["loops"] = sum(1 for n in ast.walk(tree) if isinstance(n, (ast.For, ast.While)))
    info["main_guard_line"] = _main_guard_line(tree)
    info["first_exec_line"] = _first_exec_line(tree)
    info["has_top_level_code"] = _has_top_level_code(tree)
    return info


def build_map(project_state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deterministic structural map of the project or single file."""
    files, is_project = _files_from_state(project_state)
    local_stems = {_stem(name) for name in files if _is_py(name)}

    analyzed: List[Dict[str, Any]] = []
    for name in sorted(files):
        info = _analyze_file(name, files[name])
        # Which imports point at another file in this project.
        info["project_imports"] = [
            root for root in info["imports"]
            if root in local_stems and root != _stem(name)
        ]
        analyzed.append(info)

    entry_file, entry_line = _detect_entry(analyzed, project_state, is_project)

    return {
        "is_project": is_project,
        "file_count": len(files) if is_project else 1,
        "files": analyzed,
        "entry_file": entry_file,
        "entry_line": entry_line,
    }


def _detect_entry(analyzed, project_state, is_project):
    # 1. A file with an `if __name__ == "__main__":` guard wins.
    for info in analyzed:
        if info["main_guard_line"]:
            return info["name"], info["main_guard_line"]
    if not is_project:
        single = analyzed[0] if analyzed else None
        return (None, single["first_exec_line"]) if single else (None, None)
    # 2. The declared entry, or a conventional main.py.
    declared = str(project_state.get("entry") or "").strip()
    by_name = {info["name"]: info for info in analyzed}
    for candidate in (declared, "main.py"):
        if candidate and candidate in by_name:
            info = by_name[candidate]
            return candidate, (info["first_exec_line"] or 1)
    # 3. Otherwise the first file that actually runs top-level code.
    for info in analyzed:
        if info["py"] and not info["parse_error"] and info["has_top_level_code"]:
            return info["name"], info["first_exec_line"]
    return None, None


def _join(names: List[str]) -> str:
    names = list(names)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _count_phrase(singular: str, plural: str, names: List[str]) -> str:
    n = len(names)
    label = singular if n == 1 else plural
    return f"{n} {label}: {', '.join(names)}"


def _role(info: Dict[str, Any], entry_file: Optional[str]) -> str:
    if not info["py"]:
        if info["name"].lower().endswith("requirements.txt"):
            return "lists the project requirements"
        return "is a data or text file"
    if info["parse_error"]:
        return "has a syntax error, so it could not be read"
    if entry_file is not None and info["name"] == entry_file:
        return "starts the program"
    parts: List[str] = []
    if info["classes"]:
        parts.append("defines " + _count_phrase("class", "classes", info["classes"]))
    if info["functions"]:
        parts.append("defines " + _count_phrase("function", "functions", info["functions"]))
    if parts:
        return " and ".join(parts)
    if info["variables"]:
        return "defines " + _count_phrase("variable", "variables", info["variables"])
    if info["has_top_level_code"]:
        return "runs top-level code"
    return "is empty"


def narrate(project_state: Dict[str, Any]) -> str:
    """Beginner-friendly spoken project map for single-file or multi-file work."""
    data = build_map(project_state)

    if not data["is_project"]:
        return _narrate_single(data)

    py_files = [f for f in data["files"] if f["py"]]
    other_files = [f for f in data["files"] if not f["py"]]
    lines = ["Project map:", f"There are {data['file_count']} files.", ""]

    for info in data["files"]:
        lines.append(f"{info['name']} {_role(info, data['entry_file'])}.")
    lines.append("")

    importers = [f for f in py_files if f["project_imports"]]
    if importers:
        for info in importers:
            lines.append(f"{info['name']} imports {_join(info['project_imports'])}.")
    else:
        lines.append("No file imports another project file.")
    lines.append("")

    if data["entry_file"] and data["entry_line"]:
        lines.append(f"The program starts in {data['entry_file']}, line {data['entry_line']}.")
    elif data["entry_file"]:
        lines.append(f"The program starts in {data['entry_file']}.")
    else:
        lines.append("I could not find a clear starting point. Tell me which file runs first.")

    if other_files:
        lines.append("")
        lines.append("Say 'what functions are in this project' or name a file for more detail.")
    return "\n".join(lines).strip()


def _narrate_single(data: Dict[str, Any]) -> str:
    info = data["files"][0] if data["files"] else None
    if info is None or (not str(info.get("name")) and info["line_count"] == 0
                        and not any([info["functions"], info["classes"], info["variables"]])):
        return ("There is no code yet. Write some Python in the editor, or create "
                "project files, then ask for the project map again.")
    if info["parse_error"]:
        return ("Project map:\nThis is a single file, but it has a syntax error so I "
                "could not read its structure. Try 'check my code' to find the problem.")

    lines = ["Project map:", "This is a single file.", ""]
    if info["classes"]:
        lines.append("It " + "defines " + _count_phrase("class", "classes", info["classes"]) + ".")
    if info["functions"]:
        lines.append("It " + "defines " + _count_phrase("function", "functions", info["functions"]) + ".")
    if info["variables"] and not (info["functions"] or info["classes"]):
        lines.append("It " + "defines " + _count_phrase("variable", "variables", info["variables"]) + ".")
    if info["loops"]:
        lines.append(f"It has {info['loops']} loop{'s' if info['loops'] != 1 else ''}.")
    external = [m for m in info["imports"]]
    if external:
        lines.append(f"It imports {_join(external)}.")
    if not (info["functions"] or info["classes"] or info["variables"] or info["loops"]):
        lines.append("It runs simple top-level code.")
    if info["main_guard_line"]:
        lines.append(f"The program starts at the main block, line {info['main_guard_line']}.")
    elif data["entry_line"]:
        lines.append(f"The program starts at line {data['entry_line']}.")
    return "\n".join(lines).strip()


def find_file_for(project_state: Dict[str, Any], query: str) -> Optional[str]:
    """Best-effort deterministic file lookup for 'open the file that ...' commands.

    Returns a filename or None. Matching is structural/lexical only: the file that
    defines a `main` function/entry, or whose name contains a keyword from the
    query. Never guesses semantically.
    """
    data = build_map(project_state)
    q = str(query or "").lower()
    py_files = [f for f in data["files"] if f["py"] and not f["parse_error"]]

    if "main" in q:
        for info in py_files:
            if "main" in info["functions"]:
                return info["name"]
        if data["entry_file"]:
            return data["entry_file"]

    # Lexical: a meaningful word from the query appearing in a file's stem.
    stopwords = {"open", "the", "file", "that", "handles", "with", "for", "this",
                 "a", "an", "which", "has", "code", "function", "where", "is"}
    words = [w for w in ''.join(c if c.isalnum() else ' ' for c in q).split()
             if w and w not in stopwords]
    for word in words:
        for info in py_files:
            stem = _stem(info["name"]).lower()
            if word in stem or stem in word:
                return info["name"]
    return None
