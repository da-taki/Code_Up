from __future__ import annotations

import ast
import builtins
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Diagnostic:
    id: str
    category: str
    severity: str
    file: str
    line: int
    column: Optional[int]
    message: str
    detail: str
    fix: str
    source: str

    def to_dict(self) -> Dict:
        return asdict(self)


_BUILTIN_NAMES = set(dir(builtins)) | {"True", "False", "None"}
_IGNORE_LOAD_NAMES = {
    "__name__",
}


def _diag_id(category: str, source: str, file_name: str, line: int, column: Optional[int], message: str) -> str:
    raw = f"{category}|{source}|{file_name}|{line}|{column or ''}|{message}"
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]


def make_diagnostic(
    *,
    category: str,
    severity: str = "error",
    file: str = "<user>",
    line: int = 1,
    column: Optional[int] = None,
    message: str,
    detail: str = "",
    fix: str = "",
    source: str,
) -> Diagnostic:
    line = max(1, int(line or 1))
    did = _diag_id(category, source, file, line, column, message)
    return Diagnostic(
        id=did,
        category=category,
        severity=severity,
        file=file,
        line=line,
        column=column,
        message=message.strip(),
        detail=(detail or message).strip(),
        fix=fix.strip(),
        source=source,
    )


def from_syntax_error(error: SyntaxError, code: str, *, file: str = "<user>") -> Diagnostic:
    err_type = type(error).__name__
    message = str(error.msg or "Python syntax error").strip()
    line = error.lineno or 1
    column = error.offset
    fix = "Check this line for a missing colon, bracket, quote, or indentation."
    if isinstance(error, IndentationError):
        if "expected an indented block" in message.lower():
            fix = "Indent the line that belongs inside the block, usually with four spaces."
        elif "unexpected indent" in message.lower():
            fix = "Remove extra spaces at the start of this line or align it with the block."
    return make_diagnostic(
        category="syntax",
        severity="error",
        file=file,
        line=line,
        column=column,
        message=f"{err_type}: {message}",
        detail=f"Python could not parse the code at line {line}.",
        fix=fix,
        source="python_compile",
    )


def from_runtime_error(error_text: str, *, file: str = "<user>") -> Diagnostic:
    text = str(error_text or "").strip()
    line = 1
    for match in re.finditer(r"\bline\s+(\d+)\b", text, flags=re.IGNORECASE):
        line = int(match.group(1))
    kind = "PythonError"
    message = text.splitlines()[0] if text else "Python could not run this code."
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*Error|Exception|SystemExit|KeyboardInterrupt):\s*(.*)", text)
    if match:
        kind = match.group(1)
        message = f"{kind}: {match.group(2).strip() or 'Python could not run this code.'}"
    fix = "Read the line named in the error, fix that part, then run again."
    if kind == "NameError":
        fix = "Check the spelling, or create this variable before using it."
    elif kind == "TypeError":
        fix = "Check whether the values used together are the right types."
    elif kind == "ZeroDivisionError":
        fix = "Make sure the divisor is not zero before dividing."
    return make_diagnostic(
        category="runtime",
        severity="error",
        file=file,
        line=line,
        column=None,
        message=message,
        detail=text or message,
        fix=fix,
        source="python_runtime",
    )


class _ScopeAnalyzer(ast.NodeVisitor):
    def __init__(self) -> None:
        self.assigned_stack: List[set[str]] = [set()]
        self.global_assigned: set[str] = set()
        self.diagnostics: List[Diagnostic] = []

    def _assigned(self) -> set[str]:
        names: set[str] = set()
        for scope in self.assigned_stack:
            names.update(scope)
        names.update(_BUILTIN_NAMES)
        names.update(_IGNORE_LOAD_NAMES)
        return names

    def _store(self, name: str) -> None:
        self.assigned_stack[-1].add(name)
        if len(self.assigned_stack) == 1:
            self.global_assigned.add(name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._store((alias.asname or alias.name.split(".")[0]))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self._store(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._store(node.name)
        child_names = {arg.arg for arg in node.args.args + node.args.kwonlyargs}
        if node.args.vararg:
            child_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            child_names.add(node.args.kwarg.arg)
        self.assigned_stack.append(set(child_names))
        for stmt in node.body:
            self.visit(stmt)
        self.assigned_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._store(node.name)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._collect_targets(node.target)
        for stmt in node.body + node.orelse:
            self.visit(stmt)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._collect_targets(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self.visit(node.value)
        self._collect_targets(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)

    def _collect_targets(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._store(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                self._collect_targets(child)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id not in self._assigned():
            self.diagnostics.append(make_diagnostic(
                category="static",
                severity="warning",
                file="<user>",
                line=getattr(node, "lineno", 1),
                column=getattr(node, "col_offset", None),
                message=f"Name '{node.id}' is used before CodeUp can see a value for it.",
                detail=f"The name {node.id} is read here, but no earlier assignment or import was found.",
                fix=f"Create {node.id} before this line, check the spelling, or pass it into the function.",
                source="codeup_static_analyzer",
            ))


def static_diagnostics(code: str) -> List[Diagnostic]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        return [from_syntax_error(exc, code or "")]
    analyzer = _ScopeAnalyzer()
    analyzer.visit(tree)
    seen: set[str] = set()
    unique: List[Diagnostic] = []
    for diag in sorted(analyzer.diagnostics, key=lambda d: (d.line, d.column or 0, d.message)):
        key = (diag.line, diag.column, diag.message)
        if str(key) in seen:
            continue
        seen.add(str(key))
        unique.append(diag)
    return unique


def diagnostics_for_code(code: str, *, include_static: bool = True) -> List[Diagnostic]:
    try:
        compile(code or "", "<user>", "exec")
    except SyntaxError as exc:
        return [from_syntax_error(exc, code or "")]
    return static_diagnostics(code) if include_static else []


def summarize(diagnostics: Iterable[Diagnostic], selected_index: int = 0) -> str:
    items = list(diagnostics)
    if not items:
        return "CodeUp found no problems."
    selected_index = max(0, min(selected_index, len(items) - 1))
    first = items[selected_index]
    count = len(items)
    noun = "problem" if count == 1 else "problems"
    prefix = f"CodeUp found {count} {noun}. "
    if count == 1:
        return prefix + f"On line {first.line}: {first.message}"
    return prefix + f"The selected problem is {selected_index + 1} of {count}, on line {first.line}: {first.message} Say next error to continue."

