import ast
import csv
import io
import keyword
import re
import tokenize
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from codeup.projects.project_support import SAFE_STDLIB_MODULES, THIRD_PARTY_MODULES


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


def find_definition(code: str, name: str) -> Dict[str, Any]:
    name = str(name or "").strip()
    tree, error = _parse(code)
    if error:
        return {"found": False, "line": None, "message": _syntax_message(error)}
    if not name:
        return {"found": False, "line": None, "message": "Tell me which name to find."}
    assert tree is not None
    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            candidates.append((node.lineno, "function"))
        elif isinstance(node, ast.ClassDef) and node.name == name:
            candidates.append((node.lineno, "class"))
        elif isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store):
            candidates.append((node.lineno, "variable"))
        elif isinstance(node, ast.arg) and node.arg == name:
            candidates.append((node.lineno, "parameter"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                if bound == name:
                    candidates.append((node.lineno, "import"))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    candidates.append((node.lineno, "import"))
    if not candidates:
        return {"found": False, "line": None,
                "message": f"I could not find a definition for {name}."}
    if name == "main":
        function_candidates = [item for item in candidates if item[1] == "function"]
        if function_candidates:
            candidates = function_candidates
    line, kind = min(candidates, key=lambda item: item[0])
    if kind == "function":
        message = f"Function {name} starts on line {line}."
    elif kind == "class":
        message = f"Class {name} starts on line {line}."
    elif kind == "import":
        message = f"{name} is imported on line {line}."
    elif kind == "parameter":
        message = f"Parameter {name} is defined on line {line}."
    else:
        message = f"{name} is first assigned on line {line}."
    return {"found": True, "line": line, "end_line": line, "message": message,
            "action": "navigate_code", "name": name, "kind": kind}


def find_references(code: str, name: str) -> Dict[str, Any]:
    name = str(name or "").strip()
    tree, error = _parse(code)
    if error:
        return {"found": False, "line": None, "message": _syntax_message(error)}
    if not name:
        return {"found": False, "line": None, "message": "Tell me which name to search for."}
    assert tree is not None
    assigned = set()
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            (assigned if isinstance(node.ctx, ast.Store) else used).add(node.lineno)
        elif isinstance(node, ast.arg) and node.arg == name:
            assigned.add(node.lineno)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            used.add(node.lineno)
    assigned_lines = sorted(assigned)
    used_lines = sorted(used)
    if not assigned_lines and not used_lines:
        return {"found": False, "line": None, "assigned_lines": [], "used_lines": [],
                "message": f"I could not find any references to {name}."}

    def line_words(lines: List[int]) -> str:
        label = "line" if len(lines) == 1 else "lines"
        return f"{label} {', '.join(str(line) for line in lines[:6])}"

    if assigned_lines and used_lines:
        message = (f"{name} is assigned on {line_words(assigned_lines)} and used on "
                   f"{line_words(used_lines)}.")
    elif assigned_lines:
        message = f"{name} is assigned on {line_words(assigned_lines)}."
    else:
        message = f"{name} is used on {line_words(used_lines)}."
    first = min(assigned_lines + used_lines)
    return {"found": True, "line": first, "end_line": first,
            "assigned_lines": assigned_lines, "used_lines": used_lines,
            "message": message, "action": "navigate_code", "name": name}


def file_outline(code: str) -> str:
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    assert tree is not None
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    variables = set()
    functions = []
    classes = []
    loops = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            variables.update(target.id for target in targets if isinstance(target, ast.Name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append((node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            classes.append((node.name, node.lineno))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            loops.append(node.lineno)
    parts = [
        f"{len(imports)} import{'s' if len(imports) != 1 else ''}",
        f"{len(variables)} top-level variable{'s' if len(variables) != 1 else ''}",
        f"{len(functions)} function{'s' if len(functions) != 1 else ''}",
        f"{len(classes)} class{'es' if len(classes) != 1 else ''}",
    ]
    message = "This file has " + ", ".join(parts[:-1]) + f", and {parts[-1]}."
    symbols = [("Function", name, line) for name, line in functions]
    symbols.extend(("Class", name, line) for name, line in classes)
    symbols.sort(key=lambda item: item[2])
    details = [f"{kind} {name} starts on line {line}." for kind, name, line in symbols[:3]]
    if len(symbols) > 3:
        details.append(f"There are {len(symbols) - 3} more definitions.")
    elif loops:
        details.append(f"A top-level loop starts on line {loops[0]}.")
    return " ".join([message, *details])


def _imported_bindings(tree: ast.AST) -> Set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _local_bindings(tree: ast.AST) -> Set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def rename_variable(code: str, old_name: str, new_name: str) -> Dict[str, Any]:
    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    if not old_name.isidentifier() or keyword.iskeyword(old_name):
        return {"success": False, "message": f"{old_name or 'That name'} is not a valid Python identifier."}
    if not new_name.isidentifier() or keyword.iskeyword(new_name):
        return {"success": False, "message": f"{new_name or 'That name'} is not a valid Python identifier."}
    if old_name == new_name:
        return {"success": False, "message": f"{old_name} already has that name."}
    if new_name in _SHADOWED_BUILTINS:
        return {"success": False,
                "message": f"I cannot rename {old_name} to {new_name} because it would shadow a Python builtin."}
    tree, error = _parse(code)
    if error:
        return {"success": False, "message": _syntax_message(error)}
    assert tree is not None
    local_bindings = _local_bindings(tree)
    imported = _imported_bindings(tree)
    if old_name not in local_bindings:
        return {"success": False, "message": f"I could not find a local variable named {old_name}."}
    if old_name in imported:
        return {"success": False,
                "message": f"I cannot safely rename {old_name} because it is also an imported name."}
    defined_symbols = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    if new_name in local_bindings or new_name in imported or new_name in defined_symbols:
        return {"success": False,
                "message": f"I cannot rename {old_name} to {new_name} because {new_name} already exists."}
    positions = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == old_name:
            positions.add((node.lineno, node.col_offset))
        elif isinstance(node, ast.arg) and node.arg == old_name:
            positions.add((node.lineno, node.col_offset))
    tokens = []
    changed = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(code).readline):
            if token.type == tokenize.NAME and token.string == old_name and token.start in positions:
                token = tokenize.TokenInfo(token.type, new_name, token.start, token.end, token.line)
                changed += 1
            tokens.append(token)
    except (IndentationError, tokenize.TokenError) as exc:
        return {"success": False, "message": f"I could not safely tokenize this code: {exc}."}
    if not changed:
        return {"success": False, "message": f"I could not find a local variable named {old_name}."}
    updated_code = tokenize.untokenize(tokens)
    return {"success": True, "code": updated_code, "count": changed,
            "message": f"Renamed {old_name} to {new_name} in {changed} places."}


_SHADOWED_BUILTINS = frozenset({"list", "dict", "str", "int", "sum", "input", "print"})


def name_conflicts(code: str) -> str:
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    assert tree is not None
    definitions: Dict[Tuple[str, str], List[int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.setdefault(("Function", node.name), []).append(node.lineno)
        elif isinstance(node, ast.ClassDef):
            definitions.setdefault(("Class", node.name), []).append(node.lineno)
    for (kind, name), lines in definitions.items():
        if len(lines) > 1:
            return f"{kind} {name} is defined twice, on lines {lines[0]} and {lines[1]}."
    shadows = sorted(
        [
            *[(node.lineno, node.id) for node in ast.walk(tree)
              if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
              and node.id in _SHADOWED_BUILTINS],
            *[(node.lineno, node.arg) for node in ast.walk(tree)
              if isinstance(node, ast.arg) and node.arg in _SHADOWED_BUILTINS],
        ],
        key=lambda item: item[0],
    )
    if shadows:
        line, name = shadows[0]
        return f"Variable {name} shadows a Python builtin on line {line}."
    return "I do not see obvious name conflicts."


_CONTAINING_BLOCKS = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.For, ast.AsyncFor,
    ast.While, ast.If, ast.With, ast.AsyncWith, ast.Try,
)


def current_block(code: str, cursor_line: Optional[int]) -> Dict[str, Any]:
    tree, error = _parse(code)
    if error:
        return {"found": False, "line": None, "message": _syntax_message(error)}
    if not isinstance(cursor_line, int) or cursor_line < 1:
        return {"found": False, "line": None,
                "message": "Place the cursor in a block, then ask again."}
    assert tree is not None
    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, _CONTAINING_BLOCKS):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= cursor_line <= end:
                candidates.append((end - node.lineno, -node.lineno, node, end))
    if not candidates:
        return {"found": False, "line": None,
                "message": f"Line {cursor_line} is not inside a Python block."}
    _, _, node, end = min(candidates, key=lambda item: (item[0], item[1]))
    if isinstance(node, (ast.For, ast.AsyncFor)):
        kind = "for loop"
    elif isinstance(node, ast.While):
        kind = "while loop"
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        kind = f"function {node.name}"
    elif isinstance(node, ast.ClassDef):
        kind = f"class {node.name}"
    elif isinstance(node, ast.If):
        kind = "conditional block"
    elif isinstance(node, ast.Try):
        kind = "try block"
    else:
        kind = "with block"
    indented = max(0, end - node.lineno)
    message = (f"Current block starts on line {node.lineno} and ends on line {end}. "
               f"It is a {kind} with {indented} indented "
               f"line{'s' if indented != 1 else ''}.")
    return {"found": True, "line": node.lineno, "end_line": end,
            "message": message, "kind": kind}


def adjacent_symbol(code: str, cursor_line: Optional[int], kind: str, direction: str) -> Dict[str, Any]:
    tree, error = _parse(code)
    if error:
        return {"found": False, "line": None, "message": _syntax_message(error)}
    assert tree is not None
    ref_line = cursor_line if isinstance(cursor_line, int) and cursor_line >= 1 else 1
    node_type = (ast.FunctionDef, ast.AsyncFunctionDef) if kind == "function" else (ast.ClassDef,)
    symbols = sorted((node.lineno, node.name) for node in ast.walk(tree) if isinstance(node, node_type))
    if direction == "next":
        matches = [(line, name) for line, name in symbols if line > ref_line]
        target = matches[0] if matches else None
    else:
        matches = [(line, name) for line, name in symbols if line < ref_line]
        target = matches[-1] if matches else None
    if target is None:
        return {"found": False, "line": None,
                "message": f"There is no {direction} {kind}."}
    line, name = target
    message = f"{direction.title()} {kind} is {name} on line {line}."
    return {"found": True, "line": line, "end_line": line,
            "message": message, "name": name, "kind": kind}


def check_brackets(code: str) -> str:
    opening = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in opening.items()}
    names = {"(": "parenthesis", "[": "square bracket", "{": "brace"}
    stack = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(code or "").readline)
        for token in tokens:
            if token.type != tokenize.OP:
                continue
            if token.string in opening:
                stack.append((token.string, token.start[0]))
            elif token.string in closing:
                if not stack or stack[-1][0] != closing[token.string]:
                    return f"There is an unmatched closing {names[closing[token.string]]} on line {token.start[0]}."
                stack.pop()
    except (IndentationError, tokenize.TokenError):
        pass
    if stack:
        bracket, line = stack[-1]
        return f"There is an unmatched opening {names[bracket]} on line {line}."
    return "Brackets look balanced."


def check_strings(code: str) -> str:
    try:
        for token in tokenize.generate_tokens(io.StringIO(code or "").readline):
            if token.type == tokenize.ERRORTOKEN and token.string in {"'", '"'}:
                return f"There may be an unclosed string near line {token.start[0]}."
    except (IndentationError, tokenize.TokenError) as exc:
        match = re.search(r"\((\d+),\s*\d+\)", str(exc))
        line = int(match.group(1)) if match else 1
        if "string" in str(exc).lower() or "eof" in str(exc).lower():
            return f"There may be an unclosed string near line {line}."
    return "Strings look closed."


def check_long_lines(code: str, threshold: int = 100) -> str:
    long_lines = [(index, len(line)) for index, line in enumerate((code or "").splitlines(), 1)
                  if len(line) > threshold]
    if not long_lines:
        return "No long lines found."
    line, length = long_lines[0]
    more = f" There are {len(long_lines) - 1} more long lines." if len(long_lines) > 1 else ""
    return f"Line {line} is long at {length} characters.{more}"


def _line_edit(code: str, cursor_line: Optional[int], operation: str) -> Dict[str, Any]:
    lines = (code or "").splitlines(keepends=True)
    if not isinstance(cursor_line, int) or cursor_line < 1 or cursor_line > len(lines):
        return {"success": False, "message": "Place the cursor on a code line, then ask again."}
    index = cursor_line - 1
    line = lines[index]
    content = line.rstrip("\r\n")
    ending = line[len(content):]
    if operation == "comment":
        if not content.strip():
            return {"success": False, "message": f"Line {cursor_line} is blank, so I did not comment it."}
        indent = content[:len(content) - len(content.lstrip())]
        remainder = content[len(indent):]
        if remainder.startswith("#"):
            return {"success": False, "message": f"Line {cursor_line} is already commented."}
        lines[index] = f"{indent}# {remainder}{ending}"
        message = f"Commented line {cursor_line}."
    elif operation == "uncomment":
        match = re.match(r"^(\s*)# ?(.*)$", content)
        if not match:
            return {"success": False, "message": f"Line {cursor_line} is not commented."}
        lines[index] = f"{match.group(1)}{match.group(2)}{ending}"
        message = f"Uncommented line {cursor_line}."
    else:
        lines.insert(index + 1, line)
        message = f"Duplicated line {cursor_line}."
    return {"success": True, "code": "".join(lines), "message": message}


def comment_line(code: str, cursor_line: Optional[int]) -> Dict[str, Any]:
    return _line_edit(code, cursor_line, "comment")


def uncomment_line(code: str, cursor_line: Optional[int]) -> Dict[str, Any]:
    return _line_edit(code, cursor_line, "uncomment")


def duplicate_line(code: str, cursor_line: Optional[int]) -> Dict[str, Any]:
    return _line_edit(code, cursor_line, "duplicate")


def delete_extra_blank_lines(code: str) -> Dict[str, Any]:
    lines = (code or "").splitlines(keepends=True)
    result = []
    previous_blank = False
    removed = 0
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            removed += 1
            continue
        result.append(line)
        previous_blank = blank
    if not removed:
        return {"success": False, "message": "No extra blank lines found."}
    return {"success": True, "code": "".join(result),
            "message": f"Removed {removed} extra blank line{'s' if removed != 1 else ''}."}


def code_stats(code: str) -> str:
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    assert tree is not None
    lines = (code or "").splitlines()
    functions = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
    classes = sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
    imports = sum(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
    loops = sum(isinstance(node, (ast.For, ast.AsyncFor, ast.While)) for node in ast.walk(tree))
    variables = {node.id for node in ast.walk(tree)
                 if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    def count_label(count: int, singular: str, plural: str = "") -> str:
        return f"{count} {singular if count == 1 else (plural or singular + 's')}"

    return (f"This file has {len(lines)} lines, {sum(bool(line.strip()) for line in lines)} nonblank lines, "
            f"{count_label(functions, 'function')}, {count_label(classes, 'class', 'classes')}, "
            f"{count_label(imports, 'import')}, {count_label(loops, 'loop')}, "
            f"and {count_label(len(variables), 'variable')}.")


def nesting_depth(code: str) -> str:
    tree, error = _parse(code)
    if error:
        return _syntax_message(error)
    assert tree is not None
    nesting_nodes = _CONTAINING_BLOCKS

    def visit(node: ast.AST, depth: int = 0) -> int:
        current = depth + 1 if isinstance(node, nesting_nodes) else depth
        return max([current, *(visit(child, current) for child in ast.iter_child_nodes(node))])

    return f"Maximum nesting depth is {visit(tree)}."


def todo_comments(code: str) -> str:
    notes = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(code or "").readline):
            if token.type != tokenize.COMMENT:
                continue
            match = re.search(r"\b(TODO|FIXME|NOTE)\b[:\s-]*(.*)", token.string, re.IGNORECASE)
            if match:
                text = f"{match.group(1).upper()} {match.group(2).strip()}".strip()
                notes.append((token.start[0], text[:100]))
    except (IndentationError, tokenize.TokenError):
        pass
    if not notes:
        return "I found no TODO, FIXME, or NOTE comments."
    details = " ".join(f"Line {line}: {text}." for line, text in notes[:4])
    more = f" There are {len(notes) - 4} more notes." if len(notes) > 4 else ""
    return f"I found {len(notes)} note{'s' if len(notes) != 1 else ''}. {details}{more}"


def requirements_summary(project_state: Dict[str, Any]) -> str:
    files = project_state.get("files") or {}
    requirements = []
    req_path = next((path for path in files if path.lower().endswith("requirements.txt")), None)
    if req_path:
        requirements = [re.split(r"[<>=!~]", line.strip(), maxsplit=1)[0]
                        for line in str(files[req_path]).splitlines()
                        if line.strip() and not line.lstrip().startswith("#")]
        if requirements:
            return f"requirements.txt lists {', '.join(requirements)}."
        return "requirements.txt does not list any packages."
    manifest_requirements = project_state.get("requirements") or []
    if manifest_requirements:
        return f"The project requires {', '.join(str(item) for item in manifest_requirements)}."
    code = project_state.get("code") or ""
    tree, _ = _parse(code)
    inferred = []
    if tree is not None:
        for name, _ in _imports(tree):
            top = name.split(".", 1)[0]
            if top in THIRD_PARTY_MODULES and top not in inferred:
                inferred.append(top)
    if inferred:
        return f"This file appears to require {', '.join(inferred)}."
    return "No requirements file found."


def missing_project_files(project_state: Dict[str, Any]) -> str:
    if not project_state.get("is_project"):
        return "No multi-file project is active."
    files = project_state.get("files") or {}
    local = {path[:-3].replace("/", ".").split(".", 1)[0]
             for path in files if path.endswith(".py")}
    declared = {str(item).split(".", 1)[0] for item in project_state.get("requirements") or []}
    for path, content in files.items():
        if not path.endswith(".py"):
            continue
        tree, _ = _parse(str(content or ""))
        if tree is None:
            continue
        for name, _ in _imports(tree):
            top = name.split(".", 1)[0]
            if top not in SAFE_STDLIB_MODULES and top not in THIRD_PARTY_MODULES \
                    and top not in declared and top not in local:
                return f"{path} imports {top}.py, but {top}.py is missing."
    return "No missing local project files found."


def csv_preview(project_state: Dict[str, Any], requested_path: str = "") -> str:
    if not project_state.get("is_project"):
        return "No multi-file project is active."
    files = project_state.get("files") or {}
    csv_paths = sorted(path for path in files if path.lower().endswith(".csv"))
    if requested_path:
        requested = requested_path.lower().replace("\\", "/")
        path = next((item for item in csv_paths
                     if item.lower() == requested or item.lower().endswith("/" + requested)), None)
    else:
        path = csv_paths[0] if csv_paths else None
    if not path:
        return "I could not find a CSV file in this project."
    raw_text = str(files[path] or "")
    stream = io.StringIO(raw_text.lstrip("\ufeff"))
    sample = "".join(stream.readline() for _ in range(6))
    try:
        rows = list(csv.reader(io.StringIO(sample)))
    except csv.Error:
        return f"I could not read a safe preview of {path}."
    if not rows:
        return f"{path} is empty."
    columns = ", ".join((rows[0] or [])) or "none"
    data_rows = [row for row in rows[1:] if row]
    shown_rows = min(len(data_rows), 5)
    message = f"{path} has columns {columns}. Previewing {shown_rows} row{'s' if shown_rows != 1 else ''}."
    if data_rows:
        message += f" First row: {', '.join(data_rows[0])}."
    return message


def import_policy_summary(module: str = "", explain_blocked: bool = False) -> str:
    module = str(module or "").strip().lower()
    allowed = "math, random, statistics, datetime, json, csv, pathlib, typing, collections, and itertools"
    if not module and explain_blocked:
        return "Imports outside the safe list are blocked to protect the lesson sandbox and project files."
    if not module:
        return f"Allowed beginner imports include {allowed}."
    if module in ALLOWED_MODULES:
        return f"{module} is allowed in the lesson sandbox."
    if module == "os":
        return "os is blocked because it can access the operating system and files outside the lesson sandbox."
    return f"{module} is blocked because it is not on the lesson sandbox's safe import list."
