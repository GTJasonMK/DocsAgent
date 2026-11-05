from __future__ import annotations

import tomllib
import json
import os
import tempfile
from pathlib import Path

from .models import Binding

SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
}
EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
SENSITIVE_NAMES = {".env", ".env.local", ".env.production"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt", ".cer"}


def is_sensitive_file(path: Path) -> bool:
    name = path.name.lower()
    return name in SENSITIVE_NAMES or name.startswith(".env.") or path.suffix.lower() in SENSITIVE_SUFFIXES


def ensure_inside(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {candidate}") from exc
    return candidate


def load_binding(root: Path, source: str | None = None, docs_root: str | None = None) -> Binding:
    root = Path(root).resolve()
    values = tomllib.loads((root / ".docsagent.toml").read_text(encoding="utf-8")) if (root / ".docsagent.toml").is_file() else {}
    binding = values.get("binding", {})
    if not isinstance(binding, dict):
        raise ValueError("[binding] must be a TOML table")
    source_value = source or binding.get("source_root") or ("src" if (root / "src").is_dir() else ".")
    docs_value = docs_root or binding.get("docs_root") or "docs/code-reference"
    if not isinstance(source_value, str) or not isinstance(docs_value, str):
        raise ValueError("source_root and docs_root must be strings")
    source_path = ensure_inside(root, root / source_value)
    docs_path = ensure_inside(root, root / docs_value)
    if source_path == docs_path or (source_path != root and (source_path in docs_path.parents or docs_path in source_path.parents)):
        raise ValueError("source_root and docs_root cannot overlap")
    source_relative = source_path.relative_to(root)
    docs_relative = docs_path.relative_to(root)
    if source_path != root and any(part in EXCLUDED_DIRS for part in source_relative.parts):
        raise ValueError("source_root cannot be inside an excluded dependency or generated directory")
    if any(part.startswith(".") or part in EXCLUDED_DIRS for part in docs_relative.parts):
        raise ValueError("docs_root cannot be hidden or inside an excluded dependency or generated directory")
    return Binding(root=root, source_root=source_path, docs_root=docs_path)


def save_binding(root: Path, source: str, docs_root: str) -> Binding:
    binding = load_binding(root, source, docs_root)
    config = binding.root / ".docsagent.toml"
    existing = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    lines = ["[binding]", f"source_root = {json.dumps(source)}", f"docs_root = {json.dumps(docs_root)}"]
    if existing.get("agent"):
        lines.append("\n[agent]")
        for key, value in existing["agent"].items():
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    _atomic_write_config(config, "\n".join(lines) + "\n")
    return binding


def load_agent_settings(root: Path) -> dict:
    config = Path(root).resolve() / ".docsagent.toml"
    if not config.is_file():
        return {}
    return tomllib.loads(config.read_text(encoding="utf-8")).get("agent", {})


def save_agent_settings(root: Path, settings: dict) -> None:
    root = Path(root).resolve()
    config = root / ".docsagent.toml"
    values = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    binding = values.get("binding", {})
    lines = ["[binding]"]
    for key in ("source_root", "docs_root"):
        if key in binding:
            lines.append(f"{key} = {json.dumps(str(binding[key]))}")
    lines.append("\n[agent]")
    for key in ("model_url", "model", "request_format", "endpoint", "temperature", "max_tokens", "instructions", "allow_source_upload", "validation_commands", "memory_enabled"):
        if key not in settings or settings[key] is None:
            continue
        value = settings[key]
        lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    _atomic_write_config(config, "\n".join(lines) + "\n")


def _atomic_write_config(target: Path, content: str) -> None:
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
        raise RuntimeError(f"configuration verification failed after write: {target.name}")


def relative_source(binding: Binding, source: Path) -> Path:
    return ensure_inside(binding.root, source).relative_to(binding.root)


def source_to_doc(binding: Binding, source: Path) -> Path:
    resolved = ensure_inside(binding.root, source)
    if binding.source_root != binding.root and resolved != binding.source_root and binding.source_root not in resolved.parents:
        raise ValueError(f"source file is outside the bound source tree: {resolved}")
    relative = resolved.relative_to(binding.root)
    return binding.docs_root / relative.parent / f"{relative.name}.md"


def is_code_file(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def scan_sources(binding: Binding) -> list[Path]:
    if not binding.source_root.exists():
        return []
    files = []
    for directory, names, filenames in os.walk(binding.source_root, topdown=True, followlinks=False):
        current = Path(directory)
        names[:] = [name for name in names if name not in EXCLUDED_DIRS]
        names[:] = [
            name for name in names
            if not _inside_directory(binding.docs_root, current / name, binding.root)
        ]
        for filename in filenames:
            path = current / filename
            if not path.is_file() or not is_code_file(path) or is_sensitive_file(path):
                continue
            try:
                resolved = ensure_inside(binding.root, path)
            except ValueError:
                continue
            if binding.docs_root != resolved and binding.docs_root not in resolved.parents:
                files.append(path)
    return sorted(files)


def _inside_directory(target: Path, candidate: Path, root: Path) -> bool:
    try:
        resolved = ensure_inside(root, candidate)
    except ValueError:
        return True
    return target == resolved or target in resolved.parents


def is_document_file(root: Path, candidate: Path) -> bool:
    try:
        path = ensure_inside(root, candidate)
        relative = path.relative_to(Path(root).resolve())
    except ValueError:
        return False
    return (
        path.is_file()
        and path.suffix.lower() == ".md"
        and not is_sensitive_file(path)
        and not any(part.startswith(".") or part in EXCLUDED_DIRS for part in relative.parts)
    )


def scan_documents(root: Path) -> list[Path]:
    root = Path(root).resolve()
    documents = []
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        names[:] = [name for name in names if not name.startswith(".") and name not in EXCLUDED_DIRS]
        for filename in filenames:
            if Path(filename).suffix.lower() != ".md":
                continue
            candidate = Path(directory) / filename
            if is_document_file(root, candidate):
                documents.append(candidate.resolve())
    return sorted(set(documents))
