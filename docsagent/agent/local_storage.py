from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path


def user_data_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "DocsAgent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "DocsAgent"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "docsagent"


def project_database(project_root: Path, category: str) -> Path:
    root = Path(project_root).resolve()
    base = user_data_root()
    if base.is_symlink():
        raise PermissionError("DocsAgent data directory cannot be a symbolic link")
    base.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name != "nt":
        base.chmod(0o700)
    directory = base / category
    if directory.is_symlink():
        raise PermissionError("DocsAgent data directory cannot be a symbolic link")
    directory.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    path = directory / (hashlib.sha256(str(root).encode("utf-8")).hexdigest() + ".sqlite3")
    if path.is_symlink():
        raise PermissionError("DocsAgent database cannot be a symbolic link")
    return path


def connect_database(path: Path) -> sqlite3.Connection:
    if path.is_symlink():
        raise PermissionError("DocsAgent database cannot be a symbolic link")
    connection = sqlite3.connect(path)
    if os.name != "nt":
        path.chmod(0o600)
    return connection
