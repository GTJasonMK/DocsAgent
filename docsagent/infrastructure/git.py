from __future__ import annotations

import difflib
import hashlib
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Callable

from ..project.binding import ensure_inside, is_sensitive_file
from ..project.feature_plans import (
    FEATURE_DOCUMENTS,
    extract_base_commit,
    extract_change_range,
    feature_directory,
    feature_document_paths,
    validate_feature_bundle,
)
from ..project.files import is_planning_path


class GitError(RuntimeError):
    pass


class GitBaselineError(GitError):
    pass


class GitAdapter:
    READ_COMMANDS = {"status", "diff", "log", "branch", "rev-parse"}

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _run(self, args: list[str]) -> str:
        command_index = 1 if args and args[0] == "--literal-pathspecs" else 0
        if len(args) <= command_index or args[command_index] not in self.READ_COMMANDS:
            raise GitError(f"unsupported git command: {args[:1]}")
        try:
            result = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, timeout=20)
        except FileNotFoundError as exc:
            raise GitError("git executable not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git command timed out: {args[0]}") from exc
        if result.returncode:
            raise GitError(result.stderr.strip() or "git command failed")
        return result.stdout

    def is_repo(self) -> bool:
        try:
            self._run(["rev-parse", "--show-toplevel"])
            return True
        except GitError as exc:
            if "not a git repository" in str(exc).lower():
                return False
            raise

    def head_commit(self) -> str:
        if not self.is_repo():
            raise GitError("not a git repository")
        head = self._read_head_commit()
        if head is None:
            raise GitBaselineError("repository needs a baseline commit before feature planning")
        return head

    def _read_head_commit(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
                cwd=self.root,
                text=True,
                capture_output=True,
                timeout=20,
            )
        except FileNotFoundError as exc:
            raise GitError("git executable not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError("git command timed out: rev-parse") from exc
        if result.returncode == 1 and not result.stderr.strip():
            return None
        if result.returncode:
            raise GitError(result.stderr.strip() or "git command failed: rev-parse")
        return result.stdout.strip()

    def _has_head(self) -> bool:
        if not self.is_repo():
            return False
        return self._read_head_commit() is not None

    def project_history(self, limit: int = 100) -> dict:
        if not 1 <= limit <= 200:
            raise ValueError("history limit must be between 1 and 200")
        if not self.is_repo():
            raise GitError("not a git repository")
        head = self._read_head_commit()
        if head is None:
            return {"head_commit": "", "items": []}
        items = []
        for line in self._run([
            "log", "-n", str(limit), "--format=%H%x09%ad%x09%s", "--date=short", head,
        ]).splitlines():
            if "\t" not in line:
                continue
            commit, date, subject = line.split("\t", 2)
            items.append({"commit": commit, "date": date, "subject": subject})
        return {"head_commit": head, "items": items}

    def compare_to_head(self, from_commit: str, limit: int = 50000) -> dict:
        if limit < 0:
            raise ValueError("diff limit must be non-negative")
        if not self.is_repo():
            raise GitError("not a git repository")
        head = self.head_commit()
        source, target = self.validate_commit_range(from_commit, head)

        names = self._run([
            "--literal-pathspecs", "diff", "--name-only", "--no-renames", "-z", source, head, "--",
        ]).split("\0")
        numstats = self._run([
            "--literal-pathspecs", "diff", "--numstat", "--no-renames", "-z", source, head, "--",
        ]).split("\0")
        text_paths = set()
        for record in numstats:
            if not record:
                continue
            parts = record.split("\t", 2)
            if len(parts) != 3 or parts[0] == "-" or parts[1] == "-":
                continue
            text_paths.add(parts[2])
        paths = sorted({
            path for path in names
            if path and path in text_paths and self._is_safe_planning_path(path)
        })
        diff = ""
        truncated = False
        if paths:
            diff, truncated = self._limited_diff([
                "--literal-pathspecs", "diff", "--no-ext-diff", "--unified=3", source, head, "--", *paths,
            ], limit)
        return {
            "from_commit": source,
            "to_commit": target,
            "files": paths,
            "diff": diff,
            "truncated": truncated,
        }

    def validate_commit_range(self, from_commit: str, to_commit: str) -> tuple[str, str]:
        source = self._resolve_commit(from_commit)
        target = self._resolve_commit(to_commit)
        if source == target:
            raise ValueError("choose two different commits for the range")
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", source, target],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if ancestry.returncode == 1:
            raise ValueError("the range start commit must be an ancestor of its end commit")
        if ancestry.returncode:
            raise GitError(ancestry.stderr.strip() or "cannot verify commit ancestry")
        return source, target

    def _resolve_commit(self, commit: str) -> str:
        from ..project.feature_plans import COMMIT_PATTERN

        if not isinstance(commit, str) or not COMMIT_PATTERN.fullmatch(commit):
            raise ValueError("commit must be a full Git hash")
        resolved = self._run(["rev-parse", "--verify", f"{commit}^{{commit}}"])
        if resolved.strip() != commit:
            raise ValueError("commit hash does not resolve to the selected commit")
        return commit

    def _is_safe_planning_path(self, relative: str) -> bool:
        path = PurePosixPath(relative)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            return False
        if not is_planning_path(relative):
            return False
        candidate = self.root.joinpath(*path.parts)
        if candidate.is_symlink():
            return False
        try:
            ensure_inside(self.root, candidate)
        except ValueError:
            return False
        return not candidate.exists() or (candidate.is_file() and candidate.stat().st_size <= 1_000_000)

    def _limited_diff(self, args: list[str], limit: int) -> tuple[str, bool]:
        chunks = []
        size = 0
        truncated = False
        for line in self._run(args).splitlines(keepends=True):
            remaining = limit - size
            if len(line) > remaining:
                chunks.append(line[:max(0, remaining)])
                truncated = True
                break
            chunks.append(line)
            size += len(line)
        return "".join(chunks), truncated

    def _safe_markdown_path(self, relative: str) -> bool:
        path = PurePosixPath(relative)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            return False
        if path.suffix.lower() != ".md" or any(part.startswith(".") for part in path.parts):
            return False
        if any(part in {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"} for part in path.parts):
            return False
        if path.parts[:2] == ("docs", "features"):
            return False
        candidate = self.root.joinpath(*path.parts)
        if is_sensitive_file(candidate) or candidate.is_symlink():
            return False
        try:
            ensure_inside(self.root, candidate)
        except ValueError:
            return False
        return not candidate.exists() or (candidate.is_file() and candidate.stat().st_size <= 1_000_000)

    def markdown_status(self) -> dict:
        status = self.status()
        entries = []
        for entry in status["entries"]:
            path = entry["path"]
            if not self._safe_markdown_path(path) or not self._has_text_change(entry):
                continue
            if "??" in entry["code"]:
                try:
                    (self.root / path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
            entries.append(entry)
        head = (self._read_head_commit() or "") if status["is_repo"] else ""
        return {
            "is_repo": status["is_repo"],
            "branch": status["branch"],
            "head_commit": head,
            "entries": entries[:500],
            "entries_truncated": len(entries) > 500,
            "changed_documents": len(entries),
        }

    def diff_markdown(
        self,
        paths: list[str],
        limit: int = 50000,
        staged_untracked_paths: set[str] | None = None,
    ) -> tuple[str, bool]:
        if not isinstance(paths, list) or not paths:
            raise ValueError("at least one Markdown path is required")
        if any(not isinstance(path, str) for path in paths):
            raise ValueError("Markdown paths must be strings")
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate Markdown paths are not allowed")
        if limit < 0:
            raise ValueError("diff limit must be non-negative")
        entries = self.status().get("entries", [])
        changed = {
            item["path"] for item in entries
            if self._safe_markdown_path(item["path"]) and self._has_text_change(item)
        }
        for path in paths:
            if not isinstance(path, str) or not self._safe_markdown_path(path):
                raise GitError("selected path is not a safe project Markdown file")
            if path not in changed:
                raise GitError("selected Markdown file has no pending Git change: " + path)

        untracked = {item["path"] for item in entries if "??" in item["code"]}
        staged_untracked_paths = staged_untracked_paths or set()
        chunks = []
        size = 0
        truncated = False
        has_head = self._has_head()
        for path in sorted(paths):
            if path in staged_untracked_paths:
                result = subprocess.run(
                    ["git", "--literal-pathspecs", "show", ":" + path],
                    cwd=self.root,
                    capture_output=True,
                    timeout=20,
                )
                if result.returncode:
                    raise GitError(result.stderr.decode("utf-8", errors="replace").strip() or "staged Markdown file could not be read")
                try:
                    staged_text = result.stdout.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise GitError("staged Markdown file is not readable UTF-8: " + path) from exc
                diff = "".join(difflib.unified_diff([], staged_text.splitlines(keepends=True), fromfile="/dev/null", tofile=path))
            elif path in untracked or not has_head:
                target = ensure_inside(self.root, self.root / path)
                try:
                    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
                except (OSError, UnicodeDecodeError):
                    continue
                diff = "".join(difflib.unified_diff([], lines, fromfile="/dev/null", tofile=path))
            else:
                diff = self._run(["--literal-pathspecs", "diff", "--no-ext-diff", "--unified=3", "HEAD", "--", path])
            for line in diff.splitlines(keepends=True):
                remaining = limit - size
                if len(line) > remaining:
                    chunks.append(line[:max(0, remaining)])
                    truncated = True
                    break
                chunks.append(line)
                size += len(line)
            if truncated:
                break
        return "".join(chunks), truncated

    def status(self) -> dict:
        if not self.is_repo():
            return {"is_repo": False, "branch": "", "entries": []}
        branch = self._run(["branch", "--show-current"]).strip()
        entries = []
        records = self._run(["status", "--short", "--untracked-files=all", "-z"]).split("\0")
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if len(record) < 4:
                continue
            code, path = record[:2], record[3:]
            entries.append({"code": code, "path": path})
            if "R" in code or "C" in code:
                if index < len(records) and records[index]:
                    entries.append({"code": code, "path": records[index]})
                index += 1
        return {"is_repo": True, "branch": branch, "entries": entries}

    def document_history(self, docs_root: str, limit: int = 20) -> list[dict[str, str]]:
        if not self.is_repo():
            raise GitError("not a git repository")
        if not 1 <= limit <= 100:
            raise ValueError("history limit must be between 1 and 100")
        docs_path = ensure_inside(self.root, self.root / docs_root)
        if docs_path == self.root or not docs_path.is_dir():
            raise GitError("documentation root must be an existing directory inside the project")
        history = []
        commits = self._run([
            "--literal-pathspecs",
            "log",
            "--format=%H%x09%ad%x09%s",
            "--date=short",
            "--",
            docs_root,
        ]).splitlines()
        for line in commits:
            if "\t" not in line:
                continue
            commit, date, subject = line.split("\t", 2)
            result = subprocess.run(["git", "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", commit], cwd=self.root, text=True, capture_output=True, timeout=20)
            if result.returncode:
                raise GitError(result.stderr.strip() or "git history file list failed")
            changed = result.stdout.split("\0")
            if not any(PurePosixPath(path).is_relative_to(PurePosixPath(docs_root)) and path.lower().endswith(".md") and not is_sensitive_file(Path(path)) and not any(part.startswith(".") for part in PurePosixPath(path).parts) for path in changed):
                continue
            history.append({"commit": commit, "date": date, "subject": subject})
            if len(history) == limit:
                break
        return history

    def document_status(self, docs_root: str) -> dict:
        status = self.status()
        ensure_inside(self.root, self.root / docs_root)
        if Path(self.root / docs_root).resolve() == self.root:
            return {"is_repo": status["is_repo"], "branch": status["branch"], "entries": [], "entries_truncated": False, "changed_documents": 0}
        changed = self._changed_documents(status["entries"], docs_root)
        entries = [item for item in status["entries"] if item["path"] in changed and self._has_text_change(item)]
        return {
            "is_repo": status["is_repo"],
            "branch": status["branch"],
            "entries": entries[:100],
            "entries_truncated": len(entries) > 100,
            "changed_documents": len(entries),
        }

    def _has_text_change(self, entry: dict) -> bool:
        if "??" in entry["code"]:
            return True
        totals = 0
        for args in (
            ["--literal-pathspecs", "diff", "--numstat", "--", entry["path"]],
            ["--literal-pathspecs", "diff", "--cached", "--numstat", "--", entry["path"]],
        ):
            numbers = self._run(args).split("\t", 2)
            if len(numbers) >= 2 and numbers[0] != "-" and numbers[1] != "-":
                totals += int(numbers[0]) + int(numbers[1])
        return totals > 0

    def _changed_documents(self, entries: list[dict], docs_root: str, include_unchanged: bool = False) -> set[str]:
        allowed = set()
        tracked = {item["path"] for item in entries if "??" not in item["code"]}
        docs_path = PurePosixPath(docs_root)
        for entry in entries:
            path = entry["path"]
            relative_path = PurePosixPath(path)
            if not relative_path.is_relative_to(docs_path):
                continue
            relative = relative_path.relative_to(docs_path)
            candidate = self.root / path
            safe = relative.suffix.lower() == ".md" and not any(part.startswith(".") for part in relative.parts) and not is_sensitive_file(candidate)
            if safe and (include_unchanged or candidate.is_file() or path in tracked):
                allowed.add(path)
        return allowed

    def diff_documents(
        self,
        docs_root: str,
        limit: int = 8000,
        staged_untracked_paths: set[str] | None = None,
    ) -> tuple[str, bool]:
        if limit < 0:
            raise ValueError("diff limit must be non-negative")
        docs_path = ensure_inside(self.root, self.root / docs_root)
        if docs_path == self.root or not docs_path.is_dir():
            raise GitError("documentation root must be an existing directory inside the project")
        entries = self.status().get("entries", [])
        paths = sorted(self._changed_documents(entries, docs_root, include_unchanged=True))
        has_head = self._has_head()
        chunks = []
        size = 0
        truncated = False
        untracked = {entry["path"] for entry in entries if "??" in entry["code"]}
        staged_untracked_paths = staged_untracked_paths or set()
        for path in paths:
            if path in staged_untracked_paths:
                result = subprocess.run(
                    ["git", "--literal-pathspecs", "show", ":" + path],
                    cwd=self.root,
                    capture_output=True,
                    timeout=20,
                )
                if result.returncode:
                    raise GitError(result.stderr.decode("utf-8", errors="replace").strip() or "staged Markdown file could not be read")
                try:
                    staged_text = result.stdout.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                diff = "".join(difflib.unified_diff([], staged_text.splitlines(keepends=True), fromfile="/dev/null", tofile=path))
            elif path in untracked or not has_head:
                target = ensure_inside(self.root, self.root / path)
                if not target.is_file() or target.stat().st_size > 1_000_000:
                    continue
                try:
                    diff = "".join(difflib.unified_diff([], target.read_text(encoding="utf-8").splitlines(keepends=True), fromfile="/dev/null", tofile=path))
                except UnicodeDecodeError:
                    continue
            else:
                diff = self._run(["--literal-pathspecs", "diff", "--no-ext-diff", "--unified=3", "HEAD", "--", path])
            for line in diff.splitlines(keepends=True):
                remaining = limit - size
                if len(line) > remaining:
                    chunks.append(line[:max(0, remaining)])
                    truncated = True
                    break
                chunks.append(line)
                size += len(line)
            if truncated:
                break
        return "".join(chunks), truncated

    def feature_plan_diff(self, feature_id: str, limit: int = 50000) -> dict:
        directory = feature_directory(feature_id).as_posix()
        if not self.is_repo():
            raise GitError("not a git repository")
        lexical_feature_path = self.root / directory
        if lexical_feature_path.is_symlink():
            raise GitError("feature plan directory cannot be a symlink")
        feature_path = ensure_inside(self.root, lexical_feature_path)
        if not feature_path.is_dir():
            raise GitError("feature plan directory does not exist")
        package_entries = list(feature_path.iterdir())
        head = self.head_commit()
        entries = self._feature_plan_worktree(feature_id)
        expected = set(feature_document_paths(feature_id))
        changed = self._changed_documents(entries, directory)
        unexpected = changed - expected
        if unexpected:
            raise GitError("feature plan directory contains unexpected Markdown changes: " + ", ".join(sorted(unexpected)))
        contents = {}
        errors = []
        if any(item.is_symlink() or not item.is_file() for item in package_entries) or {item.name for item in package_entries} != set(FEATURE_DOCUMENTS):
            errors.append("feature plan must contain exactly requirements.md, architecture.md and plan.md")
        for filename, relative in zip(FEATURE_DOCUMENTS, feature_document_paths(feature_id)):
            lexical = self.root / relative
            if lexical.is_symlink():
                raise GitError("feature plan files cannot be symlinks: " + relative)
            candidate = ensure_inside(self.root, lexical)
            if candidate.is_file() and not is_sensitive_file(candidate):
                try:
                    contents[filename] = candidate.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    errors.append("feature plan file is not readable text: " + relative)
        plan_content = contents.get("plan.md", "")
        base_commit = extract_base_commit(plan_content) or ""
        try:
            change_range = extract_change_range(plan_content)
            if change_range:
                self.validate_commit_range(*change_range)
        except ValueError as exc:
            errors.append(str(exc))
            change_range = None
        if not base_commit:
            errors.append("plan.md must record its full Base commit hash")
        elif set(contents) == set(FEATURE_DOCUMENTS):
            errors.extend(validate_feature_bundle(contents, feature_id, base_commit, self.root, change_range))
        else:
            errors.append("feature package is incomplete; regenerate all three feature documents before committing")
        diff, truncated = self.diff_documents(directory, limit)
        return {
            "feature_id": feature_id,
            "base_commit": base_commit,
            "baseline_matches_head": base_commit == head,
            "valid": not errors,
            "errors": errors,
            "files": sorted(changed),
            "diff": diff,
            "digest": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "truncated": truncated,
        }

    def commit_feature_plan(
        self,
        feature_id: str,
        message: str,
        confirm: bool = False,
        expected_diff_digest: str | None = None,
    ) -> str:
        if not confirm:
            raise GitError("explicit confirmation is required")
        message = message.strip()
        if not message or len(message) > 200:
            raise ValueError("commit message must contain 1 to 200 characters")
        review = self.feature_plan_diff(feature_id)
        if not review["valid"]:
            raise ValueError("feature plan validation failed: " + "; ".join(review["errors"]))
        if not review["baseline_matches_head"]:
            raise GitError("feature plan must be committed before the implementation commit")
        if review["truncated"]:
            raise GitError("feature-plan Diff is truncated; reduce the changes before committing")
        if not isinstance(expected_diff_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_diff_digest):
            raise ValueError("a reviewed feature-plan Diff digest is required")
        if review["digest"] != expected_diff_digest:
            raise GitError("feature plan changed after review; reload the Diff before committing")
        status_entries = self._feature_plan_worktree(feature_id)
        changed = set(review["files"])
        if not changed:
            raise GitError("feature plan has no changes to commit")
        untracked = {entry["path"] for entry in status_entries if "??" in entry["code"]}
        directory = feature_directory(feature_id).as_posix()
        return self._commit_paths(
            sorted(changed),
            message,
            expected_diff_digest,
            untracked,
            lambda staged: self.diff_documents(directory, limit=50000, staged_untracked_paths=staged),
        )

    def _feature_plan_worktree(self, feature_id: str) -> list[dict]:
        expected = set(feature_document_paths(feature_id))
        entries = self.status().get("entries", [])
        for entry in entries:
            path = entry["path"]
            if path not in expected:
                raise GitError("finish other worktree changes before reviewing the feature plan: " + path)
            if "D" in entry["code"] or "U" in entry["code"]:
                raise GitError("feature plan files cannot be deleted or conflicted: " + path)
        return entries

    def commit_markdown(
        self,
        paths: list[str],
        message: str,
        expected_diff_digest: str | None = None,
        confirm: bool = False,
    ) -> str:
        if not confirm:
            raise GitError("explicit confirmation is required")
        if not isinstance(paths, list) or not paths:
            raise GitError("explicit confirmation and Markdown paths are required")
        if any(not isinstance(path, str) for path in paths):
            raise ValueError("Markdown paths must be strings")
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate Markdown paths are not allowed")
        if not isinstance(expected_diff_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_diff_digest):
            raise ValueError("a reviewed Markdown Diff digest is required")
        if not isinstance(message, str):
            raise ValueError("commit message must be a string")
        message = message.strip()
        if not message or len(message) > 200:
            raise ValueError("commit message must contain 1 to 200 characters")
        diff, truncated = self.diff_markdown(paths)
        if truncated:
            raise GitError("Markdown Diff is truncated; select fewer files before committing")
        actual = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        if actual != expected_diff_digest:
            raise GitError("Markdown files changed after review; reload the Diff before committing")
        entries = self.status().get("entries", [])
        untracked = {item["path"] for item in entries if "??" in item["code"]}
        return self._commit_paths(sorted(paths), message, expected_diff_digest, untracked, lambda staged: self.diff_markdown(paths, staged_untracked_paths=staged))

    def _commit_paths(
        self,
        allowed: list[str],
        message: str,
        expected_diff_digest: str,
        reviewed_untracked_paths: set[str],
        reviewed_diff: Callable[[set[str]], tuple[str, bool]],
    ) -> str:
        if not self.is_repo():
            raise GitError("not a git repository")
        try:
            staged = subprocess.run(["git", "--literal-pathspecs", "add", "-A", "--", *allowed], cwd=self.root, text=True, capture_output=True, timeout=20)
        except FileNotFoundError as exc:
            raise GitError("git executable not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError("git add timed out") from exc
        if staged.returncode:
            raise GitError(staged.stderr.strip() or "git add failed")
        unstaged = subprocess.run(
            ["git", "--literal-pathspecs", "diff", "--quiet", "--", *allowed],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if unstaged.returncode == 1:
            raise GitError("Markdown files changed after review; reload the Diff before committing")
        if unstaged.returncode:
            raise GitError(unstaged.stderr.strip() or "git could not verify reviewed Markdown files")
        diff, truncated = reviewed_diff(reviewed_untracked_paths)
        actual = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        if truncated or actual != expected_diff_digest:
            raise GitError("Markdown files changed after review; reload the Diff before committing")
        try:
            commit = subprocess.run(["git", "--literal-pathspecs", "commit", "--only", "-m", message, "--", *allowed], cwd=self.root, text=True, capture_output=True, timeout=30)
        except FileNotFoundError as exc:
            raise GitError("git executable not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError("git commit timed out") from exc
        if commit.returncode:
            raise GitError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")
        return commit.stdout.strip()
