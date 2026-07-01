import ast
from typing import Any, Dict, List, Optional


class CodeAnalyzer:

    def analyze(self, code: str) -> Dict[str, Any]:
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
            if isinstance(node, ast.ClassDef):
                for item in ast.walk(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and item is not node:
                        if item in ast.walk(node) and self._is_direct_child(node, item):
                            functions.append({
                                "name":         item.name,
                                "line":         item.lineno,
                                "params":       self._extract_params(item.args),
                                "is_async":     isinstance(item, ast.AsyncFunctionDef),
                                "parent_class": node.name,
                            })

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({
                    "name":         node.name,
                    "line":         node.lineno,
                    "params":       self._extract_params(node.args),
                    "is_async":     isinstance(node, ast.AsyncFunctionDef),
                    "parent_class": None,
                })

        functions.sort(key=lambda f: f["line"])
        return functions

    def _is_direct_child(self, parent: ast.AST, candidate: ast.AST) -> bool:
        return candidate in ast.iter_child_nodes(parent)

    def _collect_classes(self, tree: ast.AST) -> List[Dict[str, Any]]:
        classes: List[Dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [
                    n.name
                    for n in ast.iter_child_nodes(node)
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