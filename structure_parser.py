import ast


class CodeAnalyzer:
    def analyze(self, code: str):
        """
        Parse code and return structural metadata.
        Safe and correct for Python 3.8+.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return {"error": f"Syntax error: {e}"}

        result = {
            "imports": [],
            "functions": [],
            "classes": [],
            "loops": []
        }

        for node in ast.walk(tree):

            # -------- IMPORTS --------
            if isinstance(node, ast.Import):
                for alias in node.names:
                    result["imports"].append(alias.name)

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    result["imports"].append(node.module)

            # -------- FUNCTIONS --------
            elif isinstance(node, ast.FunctionDef):
                params = []
                for arg in node.args.args:
                    param = {"name": arg.arg}
                    # Extract type annotation if available
                    if arg.annotation:
                        if isinstance(arg.annotation, ast.Name):
                            param["type"] = arg.annotation.id
                        elif isinstance(arg.annotation, ast.Constant):
                            param["type"] = str(arg.annotation.value)
                        else:
                            # For complex annotations, try to reconstruct from AST
                            param["type"] = ast.unparse(arg.annotation) if hasattr(ast, 'unparse') else "complex type"
                    params.append(param)
                
                result["functions"].append({
                    "name": node.name,
                    "line": node.lineno,
                    "params": params,
                    "end": getattr(node, "end_lineno", node.lineno)
                })

            # -------- CLASSES --------
            elif isinstance(node, ast.ClassDef):
                result["classes"].append({
                    "name": node.name,
                    "line": node.lineno,
                    "end": getattr(node, "end_lineno", node.lineno)
                })

            # -------- LOOPS --------
            elif isinstance(node, (ast.For, ast.While)):
                result["loops"].append({
                    "line": node.lineno,
                    "end": getattr(node, "end_lineno", node.lineno)
                })

        return result