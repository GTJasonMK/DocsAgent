from __future__ import annotations

from pathlib import Path

from .binding import Binding, source_to_doc
from .parsing import resolve_project_calls, symbol_index, symbol_anchor
from .models import CodeFacts


def build_graph(
    binding: Binding,
    facts_list: list[CodeFacts],
    root_symbol: str | None = None,
    root_path: str | None = None,
    max_depth: int | None = None,
    max_nodes: int = 300,
) -> dict:
    resolve_project_calls(facts_list)
    index = symbol_index(facts_list)
    edges = [
        {
            "from": edge.source,
            "to": edge.target or f"unresolved::{edge.target_name}",
            "label": edge.target_name,
            "kind": edge.kind,
            "evidence": edge.evidence,
            "status": "resolved" if edge.target else "unresolved",
        }
        for facts in facts_list
        for edge in facts.calls
    ]
    if max_nodes < 1:
        raise ValueError("graph node limit must be positive")
    if root_symbol is not None and root_symbol not in index:
        raise ValueError("graph symbol was not found in the scanned source files")
    roots = [root_symbol] if root_symbol is not None else []
    scoped_path = False
    if not roots and root_path:
        scoped_path = True
        matching_facts = [facts for facts in facts_list if facts.path.relative_to(binding.root).as_posix() == root_path]
        if not matching_facts:
            raise ValueError("graph source path was not found in the scanned source files")
        path_symbols = [
            symbol.symbol_id
            for facts in matching_facts
            for symbol in facts.symbols
        ]
        roots = [value for value in path_symbols if index[value].kind == "ui"] or [
            value for value in path_symbols if index[value].kind in {"function", "method"}
        ] or path_symbols
    if roots:
        selected = set(roots[:max_nodes])
        truncated = False
        truncated |= len(roots) > len(selected)
    elif scoped_path:
        selected = set()
        truncated = False
    else:
        selected = set(sorted(index)[:max_nodes])
        truncated = len(index) > max_nodes
    frontier = set(selected)
    depth = 0
    while frontier and (max_depth is None or depth < max_depth):
        candidates = sorted({edge["to"] for edge in edges if edge["from"] in frontier and edge["to"] in index} - selected)
        remaining = max_nodes - len(selected)
        next_frontier = set(candidates[:remaining])
        selected.update(next_frontier)
        truncated |= len(candidates) > len(next_frontier)
        frontier = next_frontier
        depth += 1
    if frontier and max_depth is not None and depth >= max_depth:
        truncated |= any(edge["from"] in frontier and edge["to"] in index and edge["to"] not in selected for edge in edges)
    nodes = []
    for value in sorted(selected):
        symbol = index[value]
        source = Path(value.split("::", 1)[0])
        nodes.append({"id": value, "label": symbol.name, "kind": symbol.kind, "document": str(source_to_doc(binding, source).relative_to(binding.root)), "anchor": symbol_anchor(value), "status": "resolved"})
    unresolved = sorted({edge["to"] for edge in edges if edge["from"] in selected and edge["to"] not in index})
    unresolved_visible = set(unresolved[:max(0, max_nodes - len(selected))])
    truncated |= len(unresolved_visible) < len(unresolved)
    nodes.extend({"id": value, "label": value.split("::", 1)[-1], "kind": "unresolved", "document": "", "anchor": "", "status": "unresolved"} for value in sorted(unresolved_visible))
    relevant_paths = {Path(value.split("::", 1)[0]).as_posix() for value in selected}
    if scoped_path and root_path:
        relevant_paths.add(root_path)
    diagnostics = [
        {"path": facts.path.relative_to(binding.root).as_posix(), "language": facts.language, "messages": facts.diagnostics}
        for facts in facts_list
        if facts.diagnostics and ((not roots and not scoped_path) or facts.path.relative_to(binding.root).as_posix() in relevant_paths)
    ]
    edge_scope = {edge["from"] for edge in edges if edge["from"] in selected}
    visible_edges = [edge for edge in edges if edge["from"] in edge_scope and (edge["to"] in selected or edge["to"] in unresolved_visible)]
    status = "partial" if truncated or diagnostics or any(edge["status"] == "unresolved" for edge in visible_edges) else "complete"
    return {
        "nodes": nodes,
        "edges": visible_edges,
        "roots": roots,
        "diagnostics": diagnostics,
        "status": status,
        "truncated": truncated,
        "summary": {
            "resolved_calls": sum(edge["status"] == "resolved" for edge in visible_edges),
            "unresolved_calls": sum(edge["status"] == "unresolved" for edge in visible_edges),
            "diagnostic_files": len(diagnostics),
            "analyzed_files": len(relevant_paths),
            "total_symbols": len(index),
            "visible_symbols": len(selected),
            "total_calls": len(edges),
            "visible_calls": len(visible_edges),
        },
    }
