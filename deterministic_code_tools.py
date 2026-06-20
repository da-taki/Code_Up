import ast
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from project_support import SAFE_STDLIB_MODULES, THIRD_PARTY_MODULES


ALLOWED_MODULES = frozenset(SAFE_STDLIB_MODULES | set(THIRD_PARTY_MODULES))
BLOCKED_CALLS = frozenset({"eval", "exec", "compile", "open", "__import__"})
FILESYSTEM_CALLS = frozenset({
    "read_text", "write_text", "read_bytes", "write_bytes", "unlink", "rename",
    "replace", "mkdir", "rmdir", "touch",
})
BLOCK_STATEMENTS = (ast.For, ast.AsyncFor, ast.While, ast.If, ast.With, ast.AsyncWith,
                    ast.Try, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _parse(code: str) -> Tuple[Optional[ast.AST], Optional[SyntaxError]]:
    try:
        return ast.parse(code or ""), None
    except SyntaxError as exc:
        return None, exc


def _syntax_message(exc: SyntaxError) -> str:
    line = exc.lineno or 1
    if isinstance(exc, IndentationError):
        if "expected an indented block" in str(exc):
            return f"Line {line} is not indented after the block header."
        return f"There is an indentation problem near line {line}."
    return f"There is a syntax error near line {line}: {exc.msg}."


def _imports(tree: ast.AST) -> List[Tuple[str, int]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.lineno))
    return found


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _contains_break(loop: ast.While) -> bool:
    return any(isinstance(node, ast.Break) for stmt in loop.body for node in ast.walk(stmt))


def _is_while_true(node: ast.While) -> bool:
    return isinstance(node.test, ast.Constant) and node.test.value is True


def preflight_check(code: str, local_modules: Iterable[str] = ()) -> str:
    if not str(code or "").strip():
        return "The code is empty. Add some Python before running."
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    assert tree is not None
    local = set(local_modules)
    for name, line in _imports(tree):
        top = name.split(".", 1)[0]
        if top not in ALLOWED_MODULES and top not in local:
            return f"Line {line} imports {name}, which is blocked in the sandbox."
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in BLOCKED_CALLS:
            name = _call_name(node)
            return f"Line {node.lineno} uses {name}, which is blocked in the sandbox."
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "input":
            return f"Line {node.lineno} uses input. Set input values before running."
    for node in ast.walk(tree):
        if isinstance(node, ast.While) and _is_while_true(node) and not _contains_break(node):
            return f"The while True loop on line {node.lineno} has no break and may not stop."
    return "The code looks ready to run."


def indentation_check(code: str) -> str:
    if not str(code or "").strip():
        return "The code is empty, so there is no indentation to check."
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    assert tree is not None
    blocks = [node for node in ast.walk(tree) if isinstance(node, BLOCK_STATEMENTS)]
    blocks.sort(key=lambda node: node.lineno)
    if blocks and blocks[0].body:
        block = blocks[0]
        kind = "loop" if isinstance(block, (ast.For, ast.AsyncFor, ast.While)) else "block"
        return (f"Line {block.lineno} starts a {kind}. Line {block.body[0].lineno} "
                "is indented inside it. I do not see an indentation problem.")
    return "I do not see an indentation problem."


def list_functions(code: str) -> str:
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    functions = sorted(
        ((node.name, node.lineno) for node in ast.walk(tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        key=lambda item: item[1],
    )
    if not functions:
        return "I found no functions in this file."
    details = [f"{name} on line {line}" for name, line in functions]
    if len(details) == 1:
        return f"I found 1 function: {details[0]}."
    return f"I found {len(details)} functions: {', '.join(details[:-1])}, and {details[-1]}."


def import_summary(code: str, local_modules: Iterable[str] = ()) -> str:
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    imports = _imports(tree)
    if not imports:
        return "This file has no imports."
    names = list(dict.fromkeys(name for name, _ in imports))
    local = set(local_modules)
    blocked = [name for name in names
               if name.split(".", 1)[0] not in ALLOWED_MODULES | local]
    base = f"This file imports {', '.join(names)}."
    if blocked:
        return f"{base} {blocked[0]} is blocked in the sandbox."
    return base


def sandbox_safety_check(code: str, local_modules: Iterable[str] = ()) -> str:
    if not str(code or "").strip():
        return "The code is empty. No obvious sandbox risks were found."
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    assert tree is not None
    local = set(local_modules)
    for name, line in _imports(tree):
        top = name.split(".", 1)[0]
        if top not in ALLOWED_MODULES and top not in local:
            return f"Line {line} imports {name}, which is blocked in the sandbox."
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in BLOCKED_CALLS:
            name = _call_name(node)
            return f"Line {node.lineno} uses {name}, which is unsafe in the sandbox."
        if isinstance(node, ast.Call) and _call_name(node) in FILESYSTEM_CALLS:
            name = _call_name(node)
            return f"Line {node.lineno} uses {name}, which accesses the filesystem."
        if isinstance(node, ast.While) and _is_while_true(node) and not _contains_break(node):
            return f"The while True loop on line {node.lineno} has no break and may not stop."
    return "No obvious sandbox risks were found."


def _literal_int(node: ast.AST) -> Optional[int]:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_int(node.operand)
        return -value if value is not None else None
    return None


def _range_count(node: ast.For) -> Optional[int]:
    call = node.iter
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "range" and not call.keywords and 1 <= len(call.args) <= 3):
        return None
    values = [_literal_int(arg) for arg in call.args]
    if any(value is None for value in values):
        return None
    try:
        return len(range(*values))
    except (TypeError, ValueError):
        return None


def loop_summary(code: str) -> str:
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    loops = sorted((node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While))),
                   key=lambda node: node.lineno)
    if not loops:
        return "I found no loops in this file."
    messages = []
    for loop in loops[:4]:
        if isinstance(loop, ast.While):
            messages.append(f"The while loop on line {loop.lineno} runs while its condition stays true.")
        else:
            count = _range_count(loop)
            if count is None:
                messages.append(f"The loop on line {loop.lineno} depends on a variable, so the exact count is not known before running.")
            else:
                messages.append(f"The loop on line {loop.lineno} runs {count} times.")
    return " ".join(messages)


def project_file_tree(project_state: Dict[str, Any]) -> str:
    if not project_state.get("is_project"):
        return "No multi-file project is active."
    names = sorted((project_state.get("files") or {}).keys())
    if not names:
        return "No multi-file project is active."
    if len(names) == 1:
        return f"Project has 1 file: {names[0]}."
    return f"Project has {len(names)} files: {', '.join(names[:-1])}, and {names[-1]}."


def project_health_check(project_state: Dict[str, Any]) -> str:
    if not project_state.get("is_project"):
        return "No multi-file project is active. Use preflight check for the current file."
    files = project_state.get("files") or {}
    entry = str(project_state.get("entry") or "main.py")
    if entry not in files:
        return f"Project issue: the entry file {entry} does not exist."
    python_files = {path: content for path, content in files.items() if path.endswith(".py")}
    if not python_files:
        return "Project issue: the project has no Python files."
    for path, content in files.items():
        if not str(content or "").strip():
            return f"Project issue: {path} is empty."
    local = {path[:-3].replace("/", ".").split(".", 1)[0] for path in python_files}
    third_party: Set[str] = set()
    for path, content in python_files.items():
        tree, error = _parse(content)
        if error:
            return f"Project issue: {path} has a syntax error near line {error.lineno or 1}."
        for name, _ in _imports(tree):
            top = name.split(".", 1)[0]
            if top in THIRD_PARTY_MODULES:
                third_party.add(top)
            elif top not in SAFE_STDLIB_MODULES and top not in local:
                return f"Project issue: {path} imports {top}, but no matching project file was found."
    if third_party and "requirements.txt" not in files:
        return "Project issue: third-party imports were found, but requirements.txt is missing."
    return "The project looks ready. The entry file and Python files are present, and local imports resolve."
