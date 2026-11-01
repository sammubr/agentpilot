import ast
from pathlib import Path


def test_package_code_quality():
    package = Path(__file__).parents[2] / "src" / "ai_development_orchestrator"
    failures = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if node.end_lineno - node.lineno + 1 > 50:
                    failures.append(f"{path.name}:{node.lineno} {name} exceeds 50 lines")
                if name != "__init__":
                    body = node.body[1:] if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str) else node.body
                    if len(body) == 1 and isinstance(body[0], ast.Pass):
                        failures.append(f"{path.name}:{node.lineno} {name} is a pass stub")
                    if len(body) == 1 and isinstance(body[0], ast.Return):
                        value = body[0].value
                        if value is None or isinstance(value, ast.Constant):
                            failures.append(f"{path.name}:{node.lineno} {name} is a constant-return stub")
            if isinstance(node, ast.ExceptHandler) and (
                isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
                or node.type is None
            ):
                failures.append(f"{path.name}:{node.lineno} broad exception handler")

        engine = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine"), None)
        if engine:
            referenced = {
                n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
            }
            for method in engine.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("_") and not method.name.startswith("__") and method.name != "_on_signal" and method.name not in referenced:
                    failures.append(f"{path.name}:{method.lineno} Engine.{method.name} is not referenced")

    assert not failures, "\n".join(failures)
