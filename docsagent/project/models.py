from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Symbol:
    symbol_id: str
    kind: str
    name: str
    qualname: str
    scope: str
    signature: str = ""
    result: str = ""
    docstring: str = ""
    variables: list[dict[str, str]] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0
    parameters: list[dict[str, str]] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    flow: list[str] = field(default_factory=list)


@dataclass
class CallEdge:
    source: str
    target_name: str
    kind: str = "calls"
    target: str | None = None
    evidence: str = "static"


@dataclass
class CodeFacts:
    path: Path
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    calls: list[CallEdge] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class Binding:
    root: Path
    source_root: Path
    docs_root: Path


@dataclass
class Draft:
    source: Path
    document: Path
    content: str
    facts: CodeFacts | None
    base_content: str | None = None
    warnings: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    source_digest: str | None = None
