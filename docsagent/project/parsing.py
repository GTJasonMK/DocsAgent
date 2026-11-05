from __future__ import annotations

import ast
import re
from pathlib import Path

from .binding import SUPPORTED_EXTENSIONS
from .models import CallEdge, CodeFacts, Symbol
from .files import read_text


def symbol_id(path: Path, qualname: str) -> str:
    return f"{path.as_posix()}::{qualname}"


def symbol_anchor(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return f"symbol-{slug[-160:]}"


def _unparse(node: ast.AST | None) -> str:
    return ast.unparse(node) if node is not None else ""


class _FunctionFacts(ast.NodeVisitor):
    def __init__(self) -> None:
        self.locals: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()
        self.calls: list[str] = []
        self.returns: list[str] = []
        self.exceptions: list[str] = []
        self.flow: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.locals.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.locals.add(node.name)

    def visit_Import(self, node: ast.Import) -> None:
        self.locals.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.locals.update(alias.asname or alias.name for alias in node.names)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.locals.add(node.id)

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.locals.add(node.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(PythonParser._call_name(node.func))
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self.returns.append(_unparse(node.value) or "None")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is None:
            name = "re-raise"
        elif isinstance(node.exc, ast.Call):
            name = PythonParser._call_name(node.exc.func)
        elif isinstance(node.exc, ast.Name):
            name = node.exc.id
        else:
            name = _unparse(node.exc) or "dynamic exception"
        self.exceptions.append(name)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.flow.append("条件分支")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.flow.append("循环遍历")
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.flow.append("条件循环")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.flow.append("异常处理")
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self.flow.append("上下文管理")
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_Match(self, node: ast.Match) -> None:
        self.flow.append("模式匹配")
        self.generic_visit(node)


class PythonParser:
    def parse(self, path: Path, text: str) -> CodeFacts:
        facts = CodeFacts(path=path, language="python")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            facts.diagnostics.append(f"syntax error at line {exc.lineno}: {exc.msg}")
            return facts
        facts.imports = self._imports(tree)
        nodes: dict[str, ast.AST] = {}
        variables: dict[str, Symbol] = {}
        self._walk_scope(tree.body, path, facts, nodes, variables, "module", class_body=False, collect_bindings=True)
        self._calls(facts, nodes)
        return facts

    @staticmethod
    def _imports(tree: ast.Module) -> list[str]:
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append("." * node.level + (node.module or ""))
        return sorted(set(imports))

    def _walk_scope(
        self,
        statements: list[ast.stmt],
        path: Path,
        facts: CodeFacts,
        nodes: dict[str, ast.AST],
        variables: dict[str, Symbol],
        scope: str,
        *,
        class_body: bool,
        collect_bindings: bool,
    ) -> None:
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = self._qualname(scope, node.name)
                kind = "method" if class_body else "function"
                symbol = self._function(node, path, scope, kind)
                facts.symbols.append(symbol)
                nodes[symbol.symbol_id] = node
                self._walk_scope(node.body, path, facts, nodes, variables, qualname, class_body=False, collect_bindings=False)
                continue
            if isinstance(node, ast.ClassDef):
                qualname = self._qualname(scope, node.name)
                bases = [_unparse(base) for base in node.bases]
                bases.extend(f"{keyword.arg}={_unparse(keyword.value)}" for keyword in node.keywords)
                symbol = Symbol(
                    symbol_id(path, qualname),
                    "class",
                    node.name,
                    qualname,
                    scope,
                    signature=f"class {node.name}" + (f"({', '.join(bases)})" if bases else ""),
                    docstring=ast.get_docstring(node) or "",
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                )
                facts.symbols.append(symbol)
                nodes[symbol.symbol_id] = node
                self._walk_scope(node.body, path, facts, nodes, variables, qualname, class_body=True, collect_bindings=True)
                continue
            targets = self._binding_targets(node) if collect_bindings else []
            if targets:
                result = _unparse(getattr(node, "value", None))
                annotation = _unparse(getattr(node, "annotation", None))
                for name in targets:
                    qualname = self._qualname(scope, name)
                    symbol = variables.get(qualname)
                    if symbol:
                        if result and result not in symbol.result.split("; then "):
                            symbol.result = f"{symbol.result}; then {result}" if symbol.result else result
                        if annotation:
                            symbol.signature = f"{name}: {annotation}"
                        symbol.end_line = getattr(node, "end_lineno", node.lineno)
                    else:
                        symbol = Symbol(
                            symbol_id(path, qualname),
                            "variable",
                            name,
                            qualname,
                            scope,
                            signature=f"{name}: {annotation}" if annotation else name,
                            result=result,
                            start_line=node.lineno,
                            end_line=getattr(node, "end_lineno", node.lineno),
                        )
                        variables[qualname] = symbol
                        facts.symbols.append(symbol)
                continue
            for child in self._nested_statements(node):
                self._walk_scope([child], path, facts, nodes, variables, scope, class_body=class_body, collect_bindings=collect_bindings)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, path: Path, scope: str, kind: str) -> Symbol:
        qualname = self._qualname(scope, node.name)
        signature, parameters = self._signature(node)
        visitor = _FunctionFacts()
        for statement in node.body:
            visitor.visit(statement)
        parameters_by_name = {item["name"] for item in parameters}
        variables = [{"name": name, "kind": "local"} for name in sorted(visitor.locals - parameters_by_name - visitor.globals - visitor.nonlocals)]
        returns = list(dict.fromkeys(visitor.returns))
        return Symbol(
            symbol_id(path, qualname), kind, node.name, qualname, scope,
            signature=signature,
            result="; ".join(returns) if returns else "no explicit return",
            docstring=ast.get_docstring(node) or "",
            variables=variables,
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            parameters=parameters,
            exceptions=list(dict.fromkeys(visitor.exceptions)),
            flow=list(dict.fromkeys(visitor.flow)),
        )

    @classmethod
    def _signature(cls, node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, list[dict[str, str]]]:
        args = node.args
        positional = [*args.posonlyargs, *args.args]
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        parts: list[str] = []
        parameters: list[dict[str, str]] = []

        def add(arg: ast.arg, kind: str, default: ast.expr | None = None, prefix: str = "") -> None:
            annotation = _unparse(arg.annotation)
            value = f"{prefix}{arg.arg}"
            if annotation:
                value += f": {annotation}"
            default_text = _unparse(default)
            if default is not None:
                value += f" = {default_text}"
            parts.append(value)
            parameter = {"name": arg.arg, "kind": kind}
            if annotation:
                parameter["annotation"] = annotation
            if default is not None:
                parameter["default"] = default_text
            parameters.append(parameter)

        for index, (arg, default) in enumerate(zip(positional, defaults)):
            kind = "positional-only" if index < len(args.posonlyargs) else "positional"
            add(arg, kind, default)
            if args.posonlyargs and index == len(args.posonlyargs) - 1:
                parts.append("/")
        if args.vararg:
            add(args.vararg, "variadic positional", prefix="*")
        elif args.kwonlyargs:
            parts.append("*")
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            add(arg, "keyword-only", default)
        if args.kwarg:
            add(args.kwarg, "variadic keyword", prefix="**")
        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        signature = f"{prefix}{node.name}({', '.join(parts)})"
        result_annotation = _unparse(node.returns)
        if result_annotation:
            signature += f" -> {result_annotation}"
        return signature, parameters

    def _calls(self, facts: CodeFacts, nodes: dict[str, ast.AST]) -> None:
        symbols = {item.symbol_id: item for item in facts.symbols}
        by_name: dict[str, list[Symbol]] = {}
        for symbol in facts.symbols:
            by_name.setdefault(symbol.name, []).append(symbol)
        for symbol_id_value, node in nodes.items():
            visitor = _FunctionFacts()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = node.body
                for statement in body:
                    visitor.visit(statement)
            for target_name in visitor.calls:
                target = self._resolve_call(target_name, symbols[symbol_id_value], by_name)
                facts.calls.append(CallEdge(symbol_id_value, target_name, target=target.symbol_id if target else None))

    @staticmethod
    def _resolve_call(target_name: str, source: Symbol, by_name: dict[str, list[Symbol]]) -> Symbol | None:
        if "." in target_name and not re.match(r"^(?:self|cls)\.[^.]+$", target_name):
            return None
        candidates = by_name.get(target_name.rsplit(".", 1)[-1], [])
        scope = source.scope
        scopes = []
        while scope and scope != "module":
            scopes.append(scope)
            scope = scope.rpartition(".")[0]
        scopes.append("module")
        for candidate_scope in scopes:
            matches = [candidate for candidate in candidates if candidate.scope == candidate_scope]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _qualname(scope: str, name: str) -> str:
        return name if scope == "module" else f"{scope}.{name}"

    @staticmethod
    def _binding_targets(node: ast.stmt) -> list[str]:
        if isinstance(node, ast.Assign):
            targets = [name for target in node.targets for name in PythonParser._target_names(target)]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = PythonParser._target_names(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = PythonParser._target_names(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets = [name for item in node.items if item.optional_vars for name in PythonParser._target_names(item.optional_vars)]
        else:
            targets = []
        return sorted(set(targets))

    @staticmethod
    def _target_names(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, (ast.Tuple, ast.List)):
            return [name for item in node.elts for name in PythonParser._target_names(item)]
        if isinstance(node, ast.Starred):
            return PythonParser._target_names(node.value)
        return []

    @staticmethod
    def _nested_statements(node: ast.AST) -> list[ast.stmt]:
        statements = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                statements.append(child)
            elif isinstance(child, ast.ExceptHandler):
                statements.extend(child.body)
            elif isinstance(child, ast.match_case):
                statements.extend(child.body)
        return statements

    @staticmethod
    def _call_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return _unparse(node) or node.attr
        return "dynamic_call"


class GenericParser:
    class_pattern = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")
    function_patterns = (
        re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"\bfunc\s+([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"\bfn\s+([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"\bdef\s+([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),
    )
    variable_pattern = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)")
    event_patterns = (
        re.compile(r"\bon[A-Z][\w$]*\s*=\s*\{\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"\bon[A-Z][\w$]*\s*=\s*\{\s*([A-Za-z_$][\w$]*)\s*\}"),
        re.compile(r"addEventListener\([^,]+,\s*\(\s*([A-Za-z_$][\w$]*)\s*(?:,|\))\s*=>\s*([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"addEventListener\([^,]+,\s*([A-Za-z_$][\w$]*)\s*[),]"),
        re.compile(r"(?:onClick|@click|on:click|\(click\))\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"(?:onClick|@click|on:click|\(click\))\s*=\s*\{\s*([A-Za-z_$][\w$]*)\s*\}"),
        re.compile(r"\.on(?:click|submit|change|input|keydown|keyup)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"\.on(?:click|submit|change|input|keydown|keyup)\s*=\s*([A-Za-z_$][\w$]*)\s*(?:[;,}]|$)"),
    )
    event_marker = re.compile(r"\bon[A-Z][\w$]*\s*=|@click\s*=|on:click\s*=|\(click\)\s*=|addEventListener\s*\(|\.on(?:click|submit|change|input|keydown|keyup)\s*=")
    event_calls = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\((?=[^()]*\b(?:this|event|e|target|value)\b)")
    call_pattern = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")

    def parse(self, path: Path, text: str) -> CodeFacts:
        facts = CodeFacts(path=path, language=SUPPORTED_EXTENSIONS.get(path.suffix.lower(), "generic"))
        facts.diagnostics.append("heuristic parser: declarations and UI bindings are best-effort; imports, parameters, local variables, and full call bodies may be incomplete")
        brace_depth = 0
        scopes: list[tuple[int, Symbol]] = []
        call_sites: list[tuple[str, str]] = []
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            class_match = self.class_pattern.search(line)
            function_match = next((pattern.search(line) for pattern in self.function_patterns if pattern.search(line)), None)
            owner = None
            if class_match:
                name = class_match.group(1)
                facts.symbols.append(Symbol(symbol_id(path, name), "class", name, name, "module", signature=stripped, start_line=number, end_line=number))
            elif function_match:
                name = function_match.group(1)
                owner = Symbol(symbol_id(path, name), "function", name, name, "module", signature=stripped, start_line=number, end_line=number)
                facts.symbols.append(owner)
            elif brace_depth == 0 and ".onclick" not in line:
                variable_match = self.variable_pattern.search(line)
                if variable_match:
                    name = variable_match.group(1)
                    facts.symbols.append(Symbol(symbol_id(path, name), "variable", name, name, "module", signature=stripped, start_line=number, end_line=number))
            if self.event_marker.search(line):
                ui_name = f"ui:{path.name}:{number}"
                owner = Symbol(symbol_id(path, ui_name), "ui", ui_name, ui_name, "module", signature=stripped, start_line=number, end_line=number)
                facts.symbols.append(owner)
                event = next((match for pattern in self.event_patterns if (match := pattern.search(line))), None)
                target = next((value for value in reversed(event.groups()) if value), None) if event else None
                if event and stripped.startswith(("<", "onClick", "@click", "on:click", "(click)")):
                    target = next((match.group(1) for match in self.event_calls.finditer(line)), target)
                if target:
                    call_sites.append((owner.symbol_id, target))
            elif owner is None and scopes:
                owner = scopes[-1][1]
            if owner:
                targets = [match.group(1) for match in self.call_pattern.finditer(line) if (not function_match or match.group(1) != owner.name) and match.group(1) not in {"if", "for", "while", "switch", "catch", "function", "return", "throw", "new", "typeof", "await", "async"}]
                call_sites.extend((owner.symbol_id, target) for target in targets)
            clean_line = re.sub(r"(['\"]).*?\1", "", line)
            previous_depth = brace_depth
            brace_depth = max(0, brace_depth + clean_line.count("{") - clean_line.count("}"))
            if owner and brace_depth > previous_depth:
                scopes.append((brace_depth, owner))
            while scopes and brace_depth < scopes[-1][0]:
                scopes.pop()
        by_name: dict[str, list[Symbol]] = {}
        for symbol in facts.symbols:
            by_name.setdefault(symbol.name, []).append(symbol)
        ui_symbols = {symbol.symbol_id for symbol in facts.symbols if symbol.kind == "ui"}
        for source, target_name in dict.fromkeys(call_sites):
            matches = by_name.get(target_name, [])
            if len(matches) == 1:
                facts.calls.append(CallEdge(source, target_name, kind="bind" if source in ui_symbols else "calls", target=matches[0].symbol_id, evidence="heuristic"))
            else:
                facts.calls.append(CallEdge(source, target_name, kind="bind" if source in ui_symbols else "calls", evidence="heuristic"))
        if not facts.symbols:
            facts.diagnostics.append("generic parser found no declarations")
        return facts


def parse_file(path: Path, root: Path) -> CodeFacts:
    return parse_source(path, read_text(path, root))


def parse_source(path: Path, text: str) -> CodeFacts:
    return (PythonParser() if path.suffix.lower() == ".py" else GenericParser()).parse(path, text)


def symbol_index(facts_list: list[CodeFacts]) -> dict[str, Symbol]:
    return {symbol.symbol_id: symbol for facts in facts_list for symbol in facts.symbols}


def resolve_project_calls(facts_list: list[CodeFacts]) -> None:
    """Resolve Python call names only when one project definition matches."""
    by_name: dict[str, list[Symbol]] = {}
    for facts in facts_list:
        if facts.language == "python":
            for symbol in facts.symbols:
                by_name.setdefault(symbol.name, []).append(symbol)
    for facts in facts_list:
        if facts.language != "python":
            continue
        for edge in facts.calls:
            if edge.target or edge.evidence != "static" or "." in edge.target_name:
                continue
            matches = by_name.get(edge.target_name, [])
            if len(matches) == 1:
                edge.target = matches[0].symbol_id
                edge.evidence = "unique-project-name"
