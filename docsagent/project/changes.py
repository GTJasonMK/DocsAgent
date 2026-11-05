from __future__ import annotations

import difflib
import hashlib
import os
import stat
import tempfile
from pathlib import Path

from .binding import EXCLUDED_DIRS, ensure_inside, is_sensitive_file


MAX_EDIT_CHARS = 1_000_000


def _editable_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("path is required")
    candidate = ensure_inside(root, Path(root) / relative)
    parts = candidate.relative_to(Path(root).resolve()).parts
    if any(part in EXCLUDED_DIRS or part.startswith(".") for part in parts):
        raise PermissionError("hidden, generated, or dependency paths cannot be edited")
    if is_sensitive_file(candidate):
        raise PermissionError("sensitive files cannot be edited by the Agent")
    if candidate.is_file() and candidate.stat().st_size > MAX_EDIT_CHARS:
        raise ValueError("file is too large for an inline edit; use a smaller patch")
    return candidate


def edit_project_file(root: Path, relative: str, content: str, expected_sha256: str | None) -> dict:
    if not isinstance(content, str) or len(content) > MAX_EDIT_CHARS:
        raise ValueError("content must be a string no larger than 1000000 characters")
    path = _editable_path(root, relative)
    exists = path.exists()
    if exists and expected_sha256 is None:
        raise ValueError("expected_sha256 is required when replacing an existing file")
    if not exists and expected_sha256 is not None:
        raise RuntimeError("file was removed after it was read; refusing to recreate it")
    base_bytes = path.read_bytes() if exists else b""
    base = base_bytes.decode("utf-8")
    base_digest = hashlib.sha256(base_bytes).hexdigest() if exists else None
    base_mode = stat.S_IMODE(path.stat().st_mode) if exists else None
    if exists and base_digest != expected_sha256:
        raise RuntimeError("file changed since it was read; reread and replan the edit")
    diff = "".join(difflib.unified_diff(
        base.splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=f"a/{path.relative_to(Path(root).resolve()).as_posix()}",
        tofile=f"b/{path.relative_to(Path(root).resolve()).as_posix()}",
    ))
    if exists and base == content:
        return {"path": path.relative_to(Path(root).resolve()).as_posix(), "changed": False, "sha256": base_digest, "diff": ""}

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        if base_mode is not None:
            temporary_path.chmod(base_mode)
        if exists:
            latest = path.read_bytes()
            if hashlib.sha256(latest).hexdigest() != expected_sha256:
                raise RuntimeError("file changed during the edit; refusing to overwrite it")
            os.replace(temporary_path, path)
        else:
            os.link(temporary_path, path)
            temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    written = path.read_bytes()
    return {
        "path": path.relative_to(Path(root).resolve()).as_posix(),
        "changed": True,
        "base_sha256": base_digest,
        "sha256": hashlib.sha256(written).hexdigest(),
        "diff": diff,
    }
