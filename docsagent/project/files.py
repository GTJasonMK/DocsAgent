from __future__ import annotations

import os
import hashlib
from pathlib import Path
from typing import Iterable

from .binding import EXCLUDED_DIRS, SUPPORTED_EXTENSIONS, ensure_inside, is_sensitive_file

PLANNING_EXTENSIONS = set(SUPPORTED_EXTENSIONS) | {
    ".cfg", ".conf", ".ini", ".json", ".lock", ".md", ".properties", ".toml",
    ".txt", ".xml", ".yaml", ".yml",
}
PLANNING_FILENAMES = {"Dockerfile", "GNUmakefile", "Makefile", "Procfile", "justfile"}
PLANNING_HIDDEN_DIRS = {".circleci", ".github"}
PLANNING_MAX_FILE_SIZE = 1_000_000
MAX_LINE_READ_BYTES = 10_000_000


def planning_files(root: Path) -> list[Path]:
    root = root.resolve()
    files = []
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        names[:] = [
            name for name in names
            if (not name.startswith(".") or name in PLANNING_HIDDEN_DIRS) and name not in EXCLUDED_DIRS
        ]
        for filename in filenames:
            candidate = Path(directory) / filename
            if (
                not is_planning_path(candidate.relative_to(root))
                or is_sensitive_file(candidate)
            ):
                continue
            try:
                resolved = ensure_inside(root, candidate)
                if resolved.stat().st_size <= PLANNING_MAX_FILE_SIZE:
                    files.append(resolved)
            except (OSError, ValueError):
                continue
    return sorted(set(files))


def is_planning_path(relative: str | Path) -> bool:
    path = Path(str(relative).replace("\\", "/"))
    parts = path.parts
    if path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        return False
    if any(part in EXCLUDED_DIRS for part in parts):
        return False
    if any(part.startswith(".") and part not in PLANNING_HIDDEN_DIRS for part in parts[:-1]):
        return False
    hidden_project_config = path.as_posix() == ".docsagent.toml"
    if path.name.startswith(".") and not hidden_project_config:
        return False
    if path.suffix.lower() not in PLANNING_EXTENSIONS and path.name not in PLANNING_FILENAMES:
        return False
    return not is_sensitive_file(path)


def read_text(path: Path, root: Path, limit: int | None = None) -> str:
    path = ensure_inside(root, path)
    if is_sensitive_file(path):
        raise PermissionError(f"sensitive file blocked: {path.name}")
    if not path.is_file():
        raise FileNotFoundError(path)
    data = path.read_text(encoding="utf-8")
    if limit is not None and len(data) > limit:
        raise ValueError(f"file exceeds read limit ({limit} characters): {path}")
    return data


def read_excerpt(path: Path, root: Path, limit: int) -> tuple[str, bool]:
    if limit < 1:
        raise ValueError("excerpt character limit must be positive")
    path = ensure_inside(root, path)
    if is_sensitive_file(path):
        raise PermissionError(f"sensitive file blocked: {path.name}")
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as source:
        content = source.read(limit + 1)
    return content[:limit], len(content) > limit


def read_lines(path: Path, root: Path, offset: int, limit: int, max_chars: int = 12000) -> dict:
    path = ensure_inside(root, path)
    if is_sensitive_file(path):
        raise PermissionError(f"sensitive file blocked: {path.name}")
    if not path.is_file():
        raise FileNotFoundError(path)

    if offset < 1 or limit < 1 or max_chars < 1:
        raise ValueError("line offset, limit and character limit must be positive")

    data = path.read_bytes()
    if len(data) > MAX_LINE_READ_BYTES:
        raise ValueError("file exceeds the 10 MB read limit")
    digest = hashlib.sha256(data).hexdigest()
    lines = data.decode("utf-8").splitlines(keepends=True)
    selected = []
    chars = 0
    next_offset = None
    truncated_line = None
    for number, line in enumerate(lines[offset - 1 :], offset):
        rendered = f"{number}: {line.rstrip()}"
        separator_size = 1 if selected else 0
        remaining = max_chars - chars - separator_size
        if len(selected) >= limit or remaining <= 0:
            next_offset = number
            break
        if len(rendered) > remaining:
            if not selected:
                marker = "… [line truncated]"
                if remaining > len(marker):
                    rendered = rendered[: remaining - len(marker)] + marker
                else:
                    rendered = rendered[:remaining]
                selected.append(rendered)
                next_offset = number + 1
                truncated_line = number
            else:
                next_offset = number
            break
        selected.append(rendered)
        chars += len(rendered) + separator_size
    return {"offset": offset, "limit": limit, "shown_lines": len(selected), "truncated": next_offset is not None, "next_offset": next_offset, "truncated_line": truncated_line, "sha256": digest, "content": "\n".join(selected)}


def search_text(root: Path, query: str, paths: Iterable[Path] | None = None, offset: int = 0, limit: int | None = None) -> dict:
    if offset < 0 or (limit is not None and limit < 1):
        raise ValueError("search offset must be non-negative and limit must be positive")
    if not query:
        raise ValueError("search query must not be empty")
    query = query.casefold()
    items = []
    total = 0
    if paths is None:
        candidates = []
        for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
            names[:] = [name for name in names if not name.startswith(".") and name not in EXCLUDED_DIRS]
            candidates.extend(Path(directory) / filename for filename in filenames)
    else:
        candidates = paths
    for path in sorted(candidates):
        if not path.is_file():
            continue
        try:
            path = ensure_inside(root, path)
            relative = path.relative_to(root)
        except ValueError:
            continue
        if is_sensitive_file(path):
            continue
        if any(part.startswith(".") or part in EXCLUDED_DIRS for part in relative.parts):
            continue
        try:
            with path.open("r", encoding="utf-8") as source:
                lines = enumerate(source, 1)
                for number, line in lines:
                    if query not in line.casefold():
                        continue
                    total += 1
                    if total <= offset:
                        continue
                    if limit is not None and len(items) >= limit:
                        return {"items": items, "offset": offset, "shown": len(items), "total": None, "truncated": True, "next_offset": offset + len(items)}
                    items.append({"path": relative.as_posix(), "line": number, "text": line.strip()[:240]})
        except (UnicodeDecodeError, OSError):
            continue
    next_offset = offset + len(items) if limit is not None and offset + len(items) < total else None
    return {"items": items, "offset": offset, "shown": len(items), "total": total, "truncated": next_offset is not None, "next_offset": next_offset}
