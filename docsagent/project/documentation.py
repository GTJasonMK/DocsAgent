from __future__ import annotations

import os
import hashlib
import re
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .binding import EXCLUDED_DIRS, Binding, ensure_inside, is_sensitive_file, relative_source, scan_sources, source_to_doc
from .models import CodeFacts, Draft, Symbol
from .parsing import parse_source, resolve_project_calls, symbol_anchor, symbol_index
from .files import read_excerpt, read_text


def generate_drafts(binding: Binding, model=None) -> list[Draft]:
    return list(iter_drafts(binding, model))


def iter_drafts(
    binding: Binding,
    model=None,
    sources: list[Path] | None = None,
    on_file_started: Callable[[Path], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> Iterator[Draft]:
    sources = sources if sources is not None else scan_sources(binding)
    facts = []
    source_digests = {}
    for path in sources:
        source = read_text(path, binding.root)
        facts.append(parse_source(path, source))
        source_digests[path] = hashlib.sha256(source.encode("utf-8")).hexdigest()
    resolve_project_calls(facts)
    facts = _dependency_order(binding, facts)
    for item in facts:
        if cancel_check:
            cancel_check()
        if on_file_started:
            on_file_started(item.path)
        document = source_to_doc(binding, item.path)
        base = document.read_text(encoding="utf-8") if document.is_file() else None
        summary = {}
        warnings = []
        if model is not None and getattr(model, "available", False) and getattr(model, "allow_source_upload", False):
            try:
                source, truncated = read_excerpt(item.path, binding.root, 12000)
                summary = model.summarize_code(str(relative_source(binding, item.path)), source, [{"id": symbol.symbol_id, "name": symbol.name, "kind": symbol.kind, "signature": symbol.signature} for symbol in item.symbols])
                summary, summary_warning = _sanitize_summary(summary, item)
                if summary_warning:
                    warnings.append(summary_warning)
                if truncated:
                    warnings.append("模型摘要仅基于文件前 12000 字符；定义结构来自完整本地解析。")
            except Exception as exc:
                warnings.append(f"模型摘要失败，已使用静态解析：{exc}")
                summary = {}
        elif model is not None and getattr(model, "available", False):
            warnings.append("未启用源码发送，已使用本地静态解析。")
        if cancel_check:
            cancel_check()
        content = render_document(binding, item, facts, summary)
        errors = validate_code_document(binding, item, facts, content)
        try:
            current_digest = hashlib.sha256(ensure_inside(binding.root, item.path).read_bytes()).hexdigest()
            if current_digest != source_digests[item.path]:
                errors.append("source changed while generating; regenerate this document")
        except OSError as exc:
            errors.append(f"source could not be rechecked after generation: {exc}")
        yield Draft(item.path, document, content, item, base, warnings, errors, source_digests[item.path])


def _dependency_order(binding: Binding, facts: list[CodeFacts]) -> list[CodeFacts]:
    facts_by_path = {item.path: item for item in facts}
    module_paths: dict[tuple[str, ...], Path] = {}
    package_paths: dict[Path, bool] = {}
    for item in facts:
        relative = item.path.relative_to(binding.source_root).with_suffix("")
        is_package = relative.name == "__init__"
        if is_package:
            relative = relative.parent
        key = tuple(part for part in relative.parts if part != ".")
        module_paths[key] = item.path
        package_paths[item.path] = is_package

    dependencies: dict[Path, set[Path]] = {item.path: set() for item in facts}
    for item in facts:
        relative = item.path.relative_to(binding.source_root).with_suffix("")
        current_parts = tuple(part for part in relative.parts if part != ".")
        current_package = current_parts if package_paths[item.path] else current_parts[:-1]
        for imported in item.imports:
            level = len(imported) - len(imported.lstrip("."))
            module = imported[level:]
            if level:
                prefix_length = max(0, len(current_package) - level + 1)
                parts = current_package[:prefix_length]
                if module:
                    parts += tuple(module.split("."))
            else:
                parts = tuple(module.split(".")) if module else ()
            target = None
            while parts:
                target = module_paths.get(parts)
                if target:
                    break
                parts = parts[:-1]
            if target and target != item.path:
                dependencies[item.path].add(target)

    ordered: list[CodeFacts] = []
    completed: set[Path] = set()
    remaining = set(dependencies)
    while remaining:
        ready = sorted((path for path in remaining if dependencies[path] <= completed), key=lambda path: path.as_posix())
        if not ready:
            ready = sorted(remaining, key=lambda path: path.as_posix())
        for path in ready:
            ordered.append(facts_by_path[path])
            completed.add(path)
            remaining.remove(path)
    return ordered


def _sanitize_summary(summary: object, facts: CodeFacts) -> tuple[dict, str | None]:
    if not isinstance(summary, dict):
        return {}, "模型摘要格式无效，已使用本地静态解析。"
    cleaned: dict = {}
    warning = False
    language_warning = False
    purpose = summary.get("file_purpose")
    if isinstance(purpose, str) and purpose.strip():
        purpose = purpose.strip().replace("\n", " ")[:4000]
        if _contains_chinese(purpose):
            cleaned["file_purpose"] = purpose
        else:
            warning = True
            language_warning = True
    elif purpose is not None:
        warning = True
    symbols = summary.get("symbols")
    if symbols is not None:
        if isinstance(symbols, dict):
            allowed = {symbol.symbol_id for symbol in facts.symbols}
            cleaned_symbols = {}
            for symbol_id, description in symbols.items():
                if symbol_id not in allowed or not isinstance(description, str) or not description.strip():
                    warning = True
                    continue
                description = description.strip().replace("\n", " ")[:2000]
                if not _contains_chinese(description):
                    warning = True
                    language_warning = True
                    continue
                cleaned_symbols[symbol_id] = description
            if cleaned_symbols:
                cleaned["symbols"] = cleaned_symbols
        else:
            warning = True
    if language_warning:
        return cleaned, "模型摘要未使用简体中文，已回退到本地中文说明。"
    return cleaned, "模型摘要包含无法验证的字段，已丢弃这些字段。" if warning else None


def _contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def render_document(binding: Binding, facts: CodeFacts, all_facts: list[CodeFacts], summary: dict | None = None) -> str:
    summary = summary or {}
    index = symbol_index(all_facts)
    lines = [f"# `{relative_source(binding, facts.path).as_posix()}`", "", "## 1. 文件职责", "", summary.get("file_purpose") or _file_purpose(facts), "", "## 2. 依赖", ""]
    if facts.imports:
        lines.extend(["| 名称 | 类型 | 说明 |", "| --- | --- | --- |"])
        lines.extend(f"| `{item}` | import | 静态导入 |" for item in facts.imports)
    else:
        lines.append("无静态导入。")
    lines.extend(["", "## 3. 变量", ""])
    module_variables = [symbol for symbol in facts.symbols if symbol.kind == "variable" and symbol.scope == "module"]
    if module_variables:
        lines.extend(["### 模块变量", "", "| 名称 | 初始值 |", "| --- | --- |"])
        lines.extend(f"| <a id=\"{symbol_anchor(item.symbol_id)}\"></a>[`{item.name}`](#{symbol_anchor(item.symbol_id)}) | `{item.result or '未静态确定'}` |" for item in module_variables)
    else:
        lines.append("无模块级变量。")
    classes = [symbol for symbol in facts.symbols if symbol.kind == "class"]
    if classes:
        lines.extend(["", "## 4. 类", ""])
        for item in classes:
            anchor = symbol_anchor(item.symbol_id)
            lines.extend([f'<a id="{anchor}"></a>', f"### [`{item.name}`](#{anchor})", "", item.docstring or "类职责待确认。", ""])
            attributes = [symbol for symbol in facts.symbols if symbol.kind == "variable" and symbol.scope == item.qualname]
            if attributes:
                lines.extend(["#### 类属性", "", "| 名称 | 初始值 |", "| --- | --- |"])
                lines.extend(f"| <a id=\"{symbol_anchor(attribute.symbol_id)}\"></a>[`{attribute.name}`](#{symbol_anchor(attribute.symbol_id)}) | `{attribute.result or '未静态确定'}` |" for attribute in attributes)
                lines.append("")
    functions = [symbol for symbol in facts.symbols if symbol.kind in {"function", "method"}]
    if functions:
        lines.extend(["## 5. 函数与方法", ""])
        for item in functions:
            description = summary.get("symbols", {}).get(item.symbol_id) if isinstance(summary.get("symbols"), dict) else None
            result = "未发现显式返回语句" if item.result == "no explicit return" else f"静态返回表达式：`{item.result}`"
            exceptions = "、".join(f"`{name}`" for name in item.exceptions) if item.exceptions else "未发现显式 raise；调用产生的异常无法仅由当前文件确定。"
            anchor = symbol_anchor(item.symbol_id)
            lines.extend([
                f'<a id="{anchor}"></a>',
                f"### [`{item.signature}`](#{anchor})",
                "",
                f"- **功能与结果**：{description or item.docstring or _function_purpose(item)}；{result}。",
                f"- **参数**：{_parameters(item)}",
                f"- **局部变量**：{_variables(item)}",
                f"- **异常**：{exceptions}",
                f"- **实现方式**：{_implementation(item, facts, index, binding, all_facts)}",
                "",
            ])
    ui_symbols = [symbol for symbol in facts.symbols if symbol.kind == "ui"]
    if ui_symbols:
        lines.extend(["## 6. UI 绑定", ""])
        for item in ui_symbols:
            edges = [edge for edge in facts.calls if edge.source == item.symbol_id]
            targets = []
            for edge in edges:
                target = index.get(edge.target) if edge.target else None
                targets.append(_symbol_link(target, facts, binding) if target else f"`{edge.target_name}`（未解析）")
            anchor = symbol_anchor(item.symbol_id)
            lines.extend([
                f'<a id="{anchor}"></a>',
                f"### [`{item.signature or item.name}`](#{anchor})",
                "",
                f"- **事件目标**：{'、'.join(targets) if targets else '静态解析未找到处理函数。'}",
                "",
            ])
    section = 7 if ui_symbols else 6
    lines.extend([f"## {section}. 静态分析限制", "", "无额外限制。" if not facts.diagnostics else "；".join(facts.diagnostics)])
    return "\n".join(lines).rstrip() + "\n"


def write_drafts(binding: Binding, drafts: Iterable[Draft]) -> list[str]:
    drafts = list(drafts)
    targets = []
    all_facts = [draft.facts for draft in drafts if draft.facts is not None]
    for draft in drafts:
        target = ensure_inside(binding.root, draft.document)
        if binding.docs_root not in target.parents or target.suffix.lower() != ".md" or is_sensitive_file(target):
            raise PermissionError("generated document target must be a non-sensitive Markdown file inside the bound documentation directory")
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current != draft.base_content:
            raise RuntimeError(f"document changed during generation: {target.relative_to(binding.root)}")
        errors = validate_code_document(binding, draft.facts, all_facts, draft.content) if draft.facts is not None else validate_markdown(draft.content)
        if errors:
            raise RuntimeError(f"invalid generated document {target.relative_to(binding.root)}: {'; '.join(errors)}")
        if draft.source_digest and hashlib.sha256(ensure_inside(binding.root, draft.source).read_bytes()).hexdigest() != draft.source_digest:
            raise RuntimeError(f"source changed during generation: {draft.source.relative_to(binding.root)}")
        targets.append((draft, target))

    written = []
    for draft, target in targets:
        _atomic_write_text(target, draft.content)
        written.append(str(target.relative_to(binding.root)))
    return written


def write_project_markdown(root: Path, target: Path, content: str, base_content: str | None) -> str:
    root = root.resolve()
    target = ensure_inside(root, target)
    relative = target.relative_to(root)
    if target.suffix.lower() != ".md" or is_sensitive_file(target):
        raise ValueError("proposed document must be a non-sensitive Markdown file")
    if any(part.startswith(".") or part in EXCLUDED_DIRS for part in relative.parts):
        raise PermissionError("document target cannot be inside a hidden, dependency, or generated directory")
    current = target.read_text(encoding="utf-8") if target.is_file() else None
    if current != base_content:
        raise RuntimeError(f"document changed after proposal: {relative}")
    errors = validate_markdown(content)
    if errors:
        raise RuntimeError("invalid proposed document: " + "; ".join(errors))
    _atomic_write_text(target, content)
    return relative.as_posix()


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    if target.read_text(encoding="utf-8") != content:
        raise RuntimeError(f"document verification failed after write: {target}")


def _visible_markdown_lines(text: str) -> list[str]:
    visible_lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence is None:
            if match:
                fence = match.group(1)
            else:
                visible_lines.append(line)
        elif match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) and not match.group(2).strip():
            fence = None
    return visible_lines


def validate_markdown(text: str) -> list[str]:
    errors = []
    visible_lines = _visible_markdown_lines(text)
    if sum(bool(re.match(r"^#\s+", line)) for line in visible_lines) != 1:
        errors.append("document must contain exactly one H1")
    if not text.endswith("\n"):
        errors.append("document must end with a newline")
    return errors


def validate_code_document(binding: Binding, facts: CodeFacts, all_facts: list[CodeFacts], text: str) -> list[str]:
    errors = validate_markdown(text)
    source_name = relative_source(binding, facts.path).as_posix()
    expected_title = f"# `{source_name}`"
    if not text.startswith(expected_title + "\n"):
        errors.append(f"document title does not match source path: {source_name}")

    current_document = source_to_doc(binding, facts.path).resolve()
    anchors_by_document = {
        source_to_doc(binding, item.path).resolve(): {symbol_anchor(symbol.symbol_id) for symbol in item.symbols}
        for item in all_facts
    }
    for symbol in facts.symbols:
        anchor = symbol_anchor(symbol.symbol_id)
        if f'<a id="{anchor}"></a>' not in text:
            errors.append(f"missing documentation anchor for {symbol.qualname}")
        if symbol.kind in {"function", "method"}:
            documented_names = [item["name"] for item in symbol.parameters + symbol.variables]
            for name in documented_names:
                if f"`{name}`" not in text:
                    errors.append(f"missing parameter or local variable {name} in {symbol.qualname}")

    # Markdown-looking text inside code spans is source evidence, not a link.
    visible_lines = _visible_markdown_lines(text)
    link_text = re.sub(r"(?<!\[)`[^`\n]*`(?!\])", "", "\n".join(visible_lines))
    for href in re.findall(r"\[[^\]]+\]\(([^)\s]+)\)", link_text):
        target_path, separator, anchor = href.partition("#")
        if not separator:
            continue
        document = (current_document.parent / target_path).resolve() if target_path else current_document
        if document not in anchors_by_document:
            errors.append(f"symbol link points outside the generated document set: {href}")
        elif anchor not in anchors_by_document[document]:
            errors.append(f"symbol link target does not exist: {href}")
    return errors


def _file_purpose(facts: CodeFacts) -> str:
    if facts.path.name == "__init__.py":
        return "包初始化文件；具体职责需要结合导出内容确认。"
    return f"该文件包含 {len(facts.symbols)} 个可解析定义，负责实现与 `{facts.path.name}` 相关的项目逻辑。"


def _function_purpose(symbol: Symbol) -> str:
    return f"执行 `{symbol.name}` 相关逻辑，并产生 `{symbol.result}` 结果。"


def _variables(symbol: Symbol) -> str:
    return "未发现可静态提取的局部变量。" if not symbol.variables else "、".join(f"`{item['name']}`（{item['kind']}）" for item in symbol.variables)


def _parameters(symbol: Symbol) -> str:
    if not symbol.parameters:
        return "无显式参数。"
    values = []
    for item in symbol.parameters:
        details = [item["kind"]]
        if item.get("annotation"):
            details.append(f"类型 `{item['annotation']}`")
        if "default" in item:
            details.append(f"默认值 `{item['default']}`")
        values.append(f"`{item['name']}`（{'，'.join(details)}）")
    return "、".join(values)


def _implementation(symbol: Symbol, facts: CodeFacts, index: dict[str, Symbol], binding: Binding, all_facts: list[CodeFacts]) -> str:
    flow = "、".join(symbol.flow)
    calls = []
    for edge in (edge for item in all_facts for edge in item.calls):
        if edge.source != symbol.symbol_id:
            continue
        if edge.target and edge.target in index:
            target = index[edge.target]
            calls.append(_symbol_link(target, facts, binding))
        else:
            calls.append(f"`{edge.target_name}`（未解析）")
    parts = []
    if flow:
        parts.append(f"包含{flow}")
    if calls:
        parts.append("调用 " + "、".join(calls))
    if parts:
        return "，并".join(parts) + "。"
    return "通过函数体中的静态语句执行；更细的动态分支无法仅由静态解析确定。"


def _symbol_link(symbol: Symbol, facts: CodeFacts, binding: Binding) -> str:
    target_path = Path(symbol.symbol_id.split("::", 1)[0])
    target_doc = source_to_doc(binding, target_path)
    relative = os.path.relpath(target_doc, start=source_to_doc(binding, facts.path).parent).replace(os.sep, "/")
    return f"[{symbol.name}]({relative}#{symbol_anchor(symbol.symbol_id)})"
