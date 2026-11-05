from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from ..project.binding import ensure_inside


MAX_OUTPUT_CHARS = 20000
MAX_TIMEOUT_SECONDS = 300


def run_project_command(root: Path, argv: list[str], allowed_commands: list[list[str]], timeout: int = 120, cancel_check=None) -> dict:
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("argv must be a non-empty array of non-empty strings")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds")
    if argv not in allowed_commands:
        raise PermissionError("command is not in the project validation command allowlist")
    root = Path(root).resolve()
    safe_env = {
        name: value
        for name, value in os.environ.items()
        if name in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}
    }
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        process = subprocess.Popen(
            argv,
            cwd=ensure_inside(root, root),
            env=safe_env,
            shell=False,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if cancel_check:
                try:
                    cancel_check()
                except Exception:
                    process.kill()
                    process.wait()
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                stdout = _read_bounded(stdout_file)
                stderr = _read_bounded(stderr_file)
                raise TimeoutError(f"project command timed out after {timeout}s\n{stdout}\n{stderr}")
            try:
                process.wait(timeout=min(0.2, remaining))
            except subprocess.TimeoutExpired:
                continue
        stdout = _read_bounded(stdout_file)
        stderr = _read_bounded(stderr_file)
        truncated = _truncated(stdout_file) or _truncated(stderr_file)
    return {
        "argv": argv,
        "returncode": process.returncode,
        "ok": process.returncode == 0,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
    }


def _read_bounded(stream) -> str:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    marker = b"\n...[output truncated]...\n"
    available = MAX_OUTPUT_CHARS - len(marker) if size > MAX_OUTPUT_CHARS else MAX_OUTPUT_CHARS
    head_size = min(size, available // 2)
    tail_size = min(size - head_size, available - head_size)
    stream.seek(0)
    head = stream.read(head_size)
    if tail_size:
        stream.seek(-tail_size, os.SEEK_END)
        tail = stream.read(tail_size)
    else:
        tail = b""
    value = (head + (marker if size > MAX_OUTPUT_CHARS else b"") + tail).decode("utf-8", errors="replace")
    return value


def _truncated(stream) -> bool:
    stream.seek(0, os.SEEK_END)
    return stream.tell() > MAX_OUTPUT_CHARS
