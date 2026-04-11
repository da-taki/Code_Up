"""
AST-based code structure extractor for CodeUp.

Provides CodeAnalyzer, which walks a Python source string and returns a
summary of imports, functions (with typed parameter info), classes, and
loops — used by the structure panel and the /analyze endpoint.
"""

import ast
from typing import Any, Dict, List, Optional


class CodeAnalyzer:
    """Extract structural information from Python source using the AST."""

    def analyze(self, code: str) -> Dict[str, Any]:
        """
        Parse *code* and return a structure dict with keys:
            imports   – list of import strings
            functions – list of {name, line, params: [{name, type?}]}
            classes   – list of {name, line, methods: [str]}
            loops     – list of {type, line}

        Returns {"error": <message>} on SyntaxError.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return {"error": f"Syntax error: {e}"}

        return {
            "imports":   self._collect_imports(tree),
            "functions": self._collect_functions(tree),
            "classes":   self._collect_classes(tree),
            "loops":     self._collect_loops(tree),
        }

    # ------------------------------------------------------------------
    # Private collectors
    # ------------------------------------------------------------------

    def _collect_imports(self, tree: ast.AST) -> List[str]:
        imports: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}")
        return imports

    def _collect_functions(self, tree: ast.AST) -> List[Dict[str, Any]]:
        functions: List[Dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = self._extract_params(node.args)
                functions.append({
                    "name":   node.name,
                    "line":   node.lineno,
                    "params": params,
                })
        return functions

    def _collect_classes(self, tree: ast.AST) -> List[Dict[str, Any]]:
        classes: List[Dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [
                    n.name
                    for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                classes.append({
                    "name":    node.name,
                    "line":    node.lineno,
                    "methods": methods,
                })
        return classes

    def _collect_loops(self, tree: ast.AST) -> List[Dict[str, Any]]:
        loops: List[Dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                loops.append({"type": "for",   "line": node.lineno})
            elif isinstance(node, ast.While):
                loops.append({"type": "while", "line": node.lineno})
        return loops

    # ------------------------------------------------------------------
    # Parameter helpers
    # ------------------------------------------------------------------

    def _extract_params(self, args: ast.arguments) -> List[Dict[str, Optional[str]]]:
        params: List[Dict[str, Optional[str]]] = []
        for arg in args.args:
            params.append({
                "name": arg.arg,
                "type": self._annotation_str(arg.annotation),
            })
        return params

    @staticmethod
    def _annotation_str(annotation: Optional[ast.expr]) -> Optional[str]:
        if annotation is None:
            return None
        if isinstance(annotation, ast.Name):
            return annotation.id
        if isinstance(annotation, ast.Attribute):
            return f"{annotation.value.id}.{annotation.attr}"  # type: ignore[attr-defined]
        if isinstance(annotation, ast.Constant):
            return str(annotation.value)
        try:
            return ast.unparse(annotation)
        except Exception:
            return None