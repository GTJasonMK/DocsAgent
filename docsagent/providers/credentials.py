from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path


def _config_directory() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "DocsAgent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "DocsAgent"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "docsagent"


def _credential_path(root: Path) -> Path:
    project_id = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()
    return _config_directory() / f"{project_id}.key"


def _prepare_directory(directory: Path) -> None:
    if directory.is_symlink():
        raise PermissionError("DocsAgent credential directory cannot be a symbolic link")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)


def load_api_key(root: Path) -> str:
    path = _credential_path(root)
    if not path.exists():
        return ""
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise PermissionError("DocsAgent credential directory must be a regular directory")
    if os.name != "nt":
        path.parent.chmod(0o700)
    if path.is_symlink() or not path.is_file():
        raise PermissionError("DocsAgent credential file must be a regular file")
    if os.name != "nt":
        path.chmod(0o600)
    return path.read_text(encoding="utf-8")


def save_api_key(root: Path, api_key: str) -> None:
    path = _credential_path(root)
    if not isinstance(api_key, str):
        raise ValueError("api_key must be a string")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("api_key cannot contain line breaks")
    if not api_key:
        if path.parent.is_symlink() or (path.parent.exists() and not path.parent.is_dir()):
            raise PermissionError("DocsAgent credential directory must be a regular directory")
        if path.is_symlink():
            raise PermissionError("DocsAgent credential file cannot be a symbolic link")
        path.unlink(missing_ok=True)
        return

    _prepare_directory(path.parent)
    if path.is_symlink():
        raise PermissionError("DocsAgent credential file cannot be a symbolic link")
    temporary_path = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".api-key-", dir=path.parent)
        temporary_path = Path(temporary_name)
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as temporary:
            temporary.write(api_key)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
