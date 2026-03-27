import ast


class CodeAnalyzer:
    # AST parsing bounds to prevent DoS attacks
    MAX_AST_DEPTH = 50
    MAX_AST_NODES = 10000
    
    def analyze(self, code: str):
        """
        Parse code and return structural metadata.
        Safe and correct for Python 3.8+.
        """
        try:
            tree = ast.parse(code)
            
            # Check AST bounds to prevent DoS
            depth = self._get_ast_depth(tree)
            node_count = self._count_ast_nodes(tree)
            
            if depth > self.MAX_AST_DEPTH:
                return {"error": f"AST too deep (max {self.MAX_AST_DEPTH} levels)"}
            if node_count > self.MAX_AST_NODES:
                return {"error": f"AST too complex (max {self.MAX_AST_NODES} nodes)"}
                
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
    
    def _get_ast_depth(self, node, current_depth=0):
        """Calculate the maximum depth of an AST node."""
        if current_depth > self.MAX_AST_DEPTH:
            return current_depth
        
        max_depth = current_depth
        for child in ast.iter_child_nodes(node):
            child_depth = self._get_ast_depth(child, current_depth + 1)
            max_depth = max(max_depth, child_depth)
        
        return max_depth
    
    def _count_ast_nodes(self, node):
        """Count the total number of nodes in an AST."""
        count = 1  # Count this node
        for child in ast.iter_child_nodes(node):
            count += self._count_ast_nodes(child)
            if count > self.MAX_AST_NODES:
                return count
        return count