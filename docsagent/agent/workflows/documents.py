from __future__ import annotations

import difflib
import re
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from ...infrastructure.git import GitAdapter, GitError
from ...project.binding import ensure_inside, is_document_file, is_sensitive_file, scan_documents, scan_sources
from ...project.documentation import validate_code_document, validate_markdown, write_project_markdown
from ...project.feature_plans import (
    FEATURE_DOCUMENTS,
    feature_directory,
    feature_document_paths,
    feature_id_from_directory,
    validate_feature_bundle,
)
from ...project.models import Draft
from ..state import DocumentCandidate, DocumentProposal, DocumentTask

if TYPE_CHECKING:
    from ..application import App


class DocumentWorkflow:
    """Own document tasks and their user-reviewed proposal lifecycle."""

    DENIED_DOCUMENT_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
    MAX_DOCUMENT_SIZE = 250_000
    MAX_DOCUMENT_FILES = 50
    MAX_DOCUMENT_REVIEW_SIZE = 2_000_000

    def __init__(self, app: App) -> None:
        self.app = app
        self.proposals: dict[str, DocumentProposal] = {}
        self.lock = threading.Lock()
        self.tasks: dict[str, DocumentTask] = {}

    def proposal_counts(self) -> dict[str, int]:
        with self.lock:
            counts: dict[str, int] = {}
            for proposal in self.proposals.values():
                counts[proposal.session_id] = counts.get(proposal.session_id, 0) + 1
            return counts

    def has_proposals(self, session_id: str) -> bool:
        with self.lock:
            return any(item.session_id == session_id for item in self.proposals.values())

    def has_incomplete_tasks(self, session_id: str) -> bool:
        with self.lock:
            return any(item.session_id == session_id and not item.complete for item in self.tasks.values())

    def pending_summary(self) -> tuple[int, list[str]]:
        with self.lock:
            proposals = list(self.proposals.values())
        return len(proposals), sorted({str(item.target.relative_to(self.app.binding.root)) for item in proposals})

    def reset(self) -> None:
        with self.lock:
            self.proposals.clear()
            self.tasks.clear()

    def _list_document_paths(self, include_code_reference: bool = False, offset: int = 0, limit: int = 30) -> dict:
        if offset < 0 or not 1 <= limit <= 30:
            raise ValueError("document path limit must be 1 to 30 and offset cannot be negative")
        documents = scan_documents(self.app.binding.root)
        if not include_code_reference:
            documents = [path for path in documents if self.app.binding.docs_root not in path.parents]
        selected = documents[offset : offset + limit]
        items = [{"path": path.relative_to(self.app.binding.root).as_posix(), "characters": len(path.read_text(encoding="utf-8"))} for path in selected]
        next_offset = offset + len(selected) if offset + len(selected) < len(documents) else None
        return {"items": items, "offset": offset, "shown": len(items), "total": len(documents), "truncated": next_offset is not None, "next_offset": next_offset}

    def _create_document_task(self, arguments: dict, session_id: str) -> dict:
        mode = arguments.get("mode", "create")
        if mode not in {"create", "patch", "feature"}:
            raise ValueError("document task mode must be create, patch or feature")
        if mode != "feature" and arguments.get("change_range") is not None:
            raise ValueError("change ranges are only supported by feature plans")
        if mode in {"patch", "feature"} and not self.app._model_snapshot().allow_source_upload:
            raise PermissionError("document editing requires enabling project-content access in Agent settings")
        target_directory = arguments.get("target_directory", "")
        if not isinstance(target_directory, str):
            raise ValueError("target_directory must be a relative path string")
        target_directory = target_directory.strip()
        if target_directory == ".":
            target_directory = "" if mode == "create" else "."
        elif not target_directory:
            if mode == "create":
                raise ValueError("target_directory is required")
        if Path(target_directory).is_absolute() or any(part in {"", ".", ".."} for part in Path(target_directory).parts):
            raise ValueError("target_directory must be a clean relative path such as docs/design")
        goal = arguments.get("goal", "")
        if not isinstance(goal, str):
            raise ValueError("document task goal must be a string")
        goal = goal.strip()
        if not goal:
            raise ValueError("document task goal is required")
        files = arguments.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= self.MAX_DOCUMENT_FILES:
            raise ValueError(f"document plan must list 1 to {self.MAX_DOCUMENT_FILES} Markdown paths")
        planned_files = []
        for relative in files:
            if not isinstance(relative, str) or "\\" in relative:
                raise ValueError("planned document paths must use clean relative paths with forward slashes")
            path = Path(relative)
            if mode == "patch" and path.suffix.lower() != ".md":
                raise PermissionError("patch plan requires existing readable Markdown documents; source-code files remain read-only")
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.suffix.lower() != ".md":
                raise ValueError("planned document paths must be clean relative Markdown paths")
            if relative in planned_files:
                raise ValueError(f"duplicate planned document path: {relative}")
            if mode == "patch":
                candidate = ensure_inside(self.app.binding.root, self.app.binding.root / path)
                if not is_document_file(self.app.binding.root, candidate):
                    raise PermissionError(f"patch target must be an existing readable Markdown document: {relative}")
            else:
                candidate = ensure_inside(self.app.binding.root, (self.app.binding.root / target_directory) / path)
                if self.app.binding.docs_root == candidate or self.app.binding.docs_root in candidate.parents:
                    raise PermissionError("create mode cannot target the source-mirror documentation directory")
            planned_files.append(relative)
        target = ensure_inside(self.app.binding.root, self.app.binding.root / target_directory)
        if target.exists() and not target.is_dir():
            raise ValueError("document task target must be a directory")
        if any(part.startswith(".") or part in self.DENIED_DOCUMENT_DIRS for part in target.relative_to(self.app.binding.root).parts):
            raise PermissionError("document task target cannot be inside a hidden or generated directory")
        if mode == "create" and (self.app.binding.docs_root == target or self.app.binding.docs_root in target.parents):
            raise PermissionError("document task target cannot overwrite the source-mirror documentation directory")
        feature_id = None
        feature_action = None
        base_commit = None
        change_range = arguments.get("change_range")
        if mode == "feature":
            feature_id = feature_id_from_directory(target.relative_to(self.app.binding.root))
            if target.relative_to(self.app.binding.root).as_posix() != feature_directory(feature_id).as_posix():
                raise ValueError("feature plans must be stored under docs/features/<feature-id>")
            feature_action = arguments.get("feature_action", "create")
            if feature_action not in {"create", "modify"}:
                raise ValueError("feature action must be create or modify")
            if feature_action == "create" and target.exists():
                raise FileExistsError("feature package already exists; ask the user to choose a new id or explicitly modify this package")
            if feature_action == "modify":
                if not target.is_dir():
                    raise FileNotFoundError("feature package does not exist; create it with a new feature id")
                package_entries = list(target.iterdir())
                if any(path.is_symlink() or not path.is_file() for path in package_entries) or not {path.name for path in package_entries} <= set(FEATURE_DOCUMENTS):
                    raise ValueError("an existing feature package can contain only requirements.md, architecture.md and plan.md")
            if set(planned_files) != set(FEATURE_DOCUMENTS) or len(planned_files) != len(FEATURE_DOCUMENTS):
                raise ValueError("feature plans must contain exactly requirements.md, architecture.md and plan.md")
            adapter = GitAdapter(self.app.binding.root)
            if not adapter.is_repo():
                raise GitError("feature planning requires Git; initialize the repository and create a baseline commit first")
            base_commit = adapter.head_commit()
            expected_base_commit = arguments.get("expected_base_commit")
            if expected_base_commit is not None and expected_base_commit != base_commit:
                raise RuntimeError("Git HEAD changed after the feature-plan review started; reload the project and try again")
            if change_range is not None:
                if not isinstance(change_range, (tuple, list)) or len(change_range) != 2 or not all(isinstance(item, str) for item in change_range):
                    raise ValueError("change range must contain a start commit and current HEAD")
                preview = adapter.compare_to_head(change_range[0])
                if change_range[1] != preview["to_commit"] or base_commit != preview["to_commit"]:
                    raise ValueError("feature plan range must end at the current Git HEAD")
                change_range = (preview["from_commit"], preview["to_commit"])
            allowed_paths = set(feature_document_paths(feature_id))
            for entry in adapter.status().get("entries", []):
                if entry["path"] not in allowed_paths:
                    raise GitError("feature planning requires a clean worktree outside the current feature documents: " + entry["path"])
                if "D" in entry["code"] or "U" in entry["code"]:
                    raise GitError("feature requirement documents cannot be deleted or conflicted: " + entry["path"])
        session_id = self.app.session_store.normalize_id(session_id)
        self.app.session_store.ensure_session(session_id)
        task = DocumentTask(
            secrets.token_urlsafe(18),
            session_id,
            target,
            goal,
            self.app.binding,
            mode=mode,
            feature_id=feature_id,
            base_commit=base_commit,
            change_range=change_range,
        )
        task.files = planned_files
        if mode == "feature":
            total_size = 0
            for relative in planned_files:
                path = ensure_inside(self.app.binding.root, target / relative)
                content = path.read_text(encoding="utf-8") if path.is_file() else None
                if content is not None:
                    total_size += len(content)
                    if len(content) > self.MAX_DOCUMENT_SIZE or total_size > self.MAX_DOCUMENT_REVIEW_SIZE:
                        raise ValueError("existing feature documents exceed the review size limit")
                task.base_contents[path.relative_to(self.app.binding.root).as_posix()] = content
        if mode == "patch":
            total_size = 0
            for relative in planned_files:
                candidate = ensure_inside(self.app.binding.root, self.app.binding.root / relative)
                content = candidate.read_text(encoding="utf-8")
                if len(content) > self.MAX_DOCUMENT_SIZE:
                    raise ValueError(f"patch target exceeds {self.MAX_DOCUMENT_SIZE} characters: {relative}")
                total_size += len(content)
                if total_size > self.MAX_DOCUMENT_REVIEW_SIZE:
                    raise ValueError(f"patch plan exceeds {self.MAX_DOCUMENT_REVIEW_SIZE} characters")
                task.base_contents[relative] = content
        with self.lock:
            active = [item for item in self.tasks.values() if item.session_id == task.session_id and not item.complete]
            pending = [item for item in self.proposals.values() if item.session_id == task.session_id]
            if active or pending:
                raise RuntimeError("this conversation already has an active document task or pending reviews; resolve it first")
            self.tasks[task.task_id] = task
        planned_paths = planned_files if mode == "patch" else [str(target.relative_to(self.app.binding.root) / relative) for relative in planned_files]
        return {
            "document_task_id": task.task_id,
            "mode": mode,
            "feature_id": feature_id,
            "feature_action": feature_action,
            "base_commit": base_commit,
            "change_range": change_range,
            "target_directory": target.relative_to(self.app.binding.root).as_posix() or ".",
            "goal": task.goal,
            "planned_files": planned_paths,
            "requires_confirmation_before_write": True,
            "requires_complete_patch_for_every_file": mode == "patch",
        }

    def _get_document_task(self, task_id: str, session_id: str) -> DocumentTask:
        with self.lock:
            task = self.tasks.get(task_id)
        if not task or task.session_id != self.app.session_store.normalize_id(session_id):
            raise PermissionError("document task not found for this conversation")
        if task.binding != self.app.binding:
            raise RuntimeError("project binding changed during document task; start a new task")
        return task

    def _document_task_file(self, arguments: dict, session_id: str) -> DocumentTask:
        task_id = str(arguments.get("document_task_id", ""))
        task = self._get_document_task(task_id, session_id)
        if task.complete or task.session_id != self.app.session_store.normalize_id(session_id):
            raise PermissionError("document task not found for this conversation")
        if task.binding != self.app.binding:
            raise RuntimeError("project binding changed during document task; start a new task")
        return task

    def _activate_document_task(self, active_document_task: dict | None, session_id: str, task_id: str) -> None:
        if active_document_task is None or active_document_task.get("id"):
            raise PermissionError("one document task can be activated in a conversation turn")
        task = self._document_task_file({"document_task_id": task_id}, session_id)
        active_document_task["id"] = task.task_id
        active_document_task["mode"] = task.mode
        active_document_task["files"] = []
        active_document_task["expected_files"] = list(task.files) if task.mode == "patch" else [str(task.target_directory.relative_to(task.binding.root) / relative) for relative in task.files]

    def _proposed_draft(self, arguments: dict, session_id: str, task_id: str, active_document_task: dict) -> Draft:
        task = self._document_task_file({"document_task_id": task_id}, session_id)
        if task.mode not in {"create", "feature"}:
            raise PermissionError("document_task_file is only available in create mode")
        relative = arguments.get("path", "")
        if not isinstance(relative, str):
            raise ValueError("document path must be a relative path string")
        relative = relative.strip()
        path = Path(relative)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.suffix.lower() != ".md":
            raise ValueError("document path must be a clean relative Markdown path ending in .md")
        target = ensure_inside(task.binding.root, task.target_directory / path)
        if target.parent != task.target_directory.resolve() and task.target_directory.resolve() not in target.parents:
            raise PermissionError("document path escapes the task target directory")
        relative_parts = target.relative_to(task.binding.root).parts
        if is_sensitive_file(target) or any(part.startswith(".") or part in self.DENIED_DOCUMENT_DIRS for part in relative_parts):
            raise PermissionError("document task target cannot be sensitive or inside a hidden, dependency, or generated directory")
        content = arguments.get("content", "")
        if not isinstance(content, str):
            raise ValueError("proposed Markdown content must be a string")
        if len(content) > self.MAX_DOCUMENT_SIZE:
            raise ValueError(f"proposed Markdown exceeds {self.MAX_DOCUMENT_SIZE} characters")
        errors = validate_markdown(content)
        if errors:
            raise ValueError("proposed Markdown failed validation: " + "; ".join(errors))
        summary = arguments.get("summary", "")
        if not isinstance(summary, str):
            raise ValueError("document summary must be a string")
        relative_target = str(target.relative_to(task.binding.root))
        if relative_target not in active_document_task["expected_files"]:
            raise PermissionError(f"path is not in the agreed document plan: {relative_target}")
        if relative_target in active_document_task.get("drafts", {}):
            raise ValueError(f"document path already has a draft: {relative_target}")
        active_document_task["files"].append(relative_target)
        relative_target = str(target.relative_to(task.binding.root))
        if task.mode == "feature":
            current = target.read_text(encoding="utf-8") if target.is_file() else None
            base = task.base_contents[relative_target]
            if current != base:
                raise RuntimeError(f"feature document changed after planning: {relative_target}")
        else:
            base = target.read_text(encoding="utf-8") if target.is_file() else None
        candidate = DocumentCandidate(target, content, base, summary[:500])
        active_document_task.setdefault("drafts", {})[relative_target] = candidate
        return Draft(target, target, content, None, base, [candidate.summary])

    def _read_document_sections(self, arguments: dict, session_id: str, task_id: str, active_document_task: dict) -> dict:
        task = self._document_task_file({"document_task_id": task_id}, session_id)
        if task.mode != "patch" or active_document_task.get("id") != task.task_id:
            raise PermissionError("read_document_sections requires the active patch-mode document task")
        heading = arguments.get("heading")
        if not isinstance(heading, str) or not heading.strip() or len(heading) > 160:
            raise ValueError("heading must contain 1 to 160 characters")
        heading = heading.strip()
        sections = []
        total_chars = 0
        for relative in active_document_task["expected_files"]:
            candidate = active_document_task.get("drafts", {}).get(relative)
            content = candidate.content if candidate else task.base_contents[relative]
            lines = content.splitlines()
            start = next((index for index, line in enumerate(lines) if self._matches_markdown_heading(line, heading)), None)
            if start is None:
                sections.append({"path": relative, "found": False})
                continue
            level = len(lines[start]) - len(lines[start].lstrip("#"))
            end = next((index for index in range(start + 1, len(lines)) if re.match(r"^#{1,6}\s+", lines[index]) and len(lines[index]) - len(lines[index].lstrip("#")) <= level), len(lines))
            section = "\n".join(lines[start:end]).strip()
            total_chars += len(section)
            if total_chars > 20000:
                raise ValueError("matched document sections exceed 20000 characters; read fewer files per task")
            sections.append({"path": relative, "found": True, "section": section})
        return {"heading": heading, "sections": sections, "found": sum(item["found"] for item in sections)}

    @staticmethod
    def _matches_markdown_heading(line: str, heading: str) -> bool:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not match:
            return False
        title = re.sub(r"^\d+(?:\.\d+)*[.、]?\s*", "", match.group(2)).strip()
        return title.casefold() == heading.casefold()

    def _approve_document_patch(self, path: Path, content: str, base_content: str) -> None:
        binding = self.app.binding
        if binding.docs_root not in path.resolve().parents:
            return
        source = path.relative_to(binding.docs_root).with_suffix("")
        source_path = ensure_inside(binding.root, binding.root / source)
        sources = scan_sources(binding)
        if not source_path.is_file() or source_path not in sources:
            return
        from ...project.parsing import parse_file, resolve_project_calls

        all_facts = [parse_file(source, binding.root) for source in sources]
        resolve_project_calls(all_facts)
        facts = next((item for item in all_facts if item.path == source_path), None)
        if facts is None:
            raise RuntimeError(f"bound source is unavailable for {path.relative_to(binding.root)}")
        previous_errors = validate_code_document(binding, facts, all_facts, base_content)
        errors = validate_code_document(binding, facts, all_facts, content)
        remaining = list(previous_errors)
        introduced = []
        for error in errors:
            if error in remaining:
                remaining.remove(error)
            else:
                introduced.append(error)
        if introduced:
            raise ValueError(f"patched code-reference document introduced structural errors for {path.relative_to(binding.root)}: " + "; ".join(introduced))

    def _propose_document_patches(self, arguments: dict, session_id: str, task_id: str, active_document_task: dict) -> dict:
        task = self._document_task_file({"document_task_id": task_id}, session_id)
        if task.mode != "patch":
            raise PermissionError("patch_markdown_documents is only available in patch mode")
        patches = arguments.get("patches")
        if not isinstance(patches, list) or not patches or len(patches) > 100:
            raise ValueError("patches must contain 1 to 100 exact text replacements")
        candidates = active_document_task.setdefault("drafts", {})
        changed: dict[str, dict] = {}
        reserved_patches = {"path", "find", "replacement", "summary", "_strict_reserved"}
        for patch in patches:
            if not isinstance(patch, dict):
                raise ValueError("each patch must be an object")
            if set(patch) - reserved_patches:
                raise ValueError("patch contains unsupported fields")
            relative = patch.get("path", "")
            if not isinstance(relative, str) or relative not in active_document_task.get("expected_files", []):
                raise PermissionError("patch path must be listed in the active document plan")
            target = ensure_inside(self.app.binding.root, self.app.binding.root / relative)
            if not is_document_file(self.app.binding.root, target):
                raise PermissionError("patch target must be an existing readable Markdown document")
            original = task.base_contents[relative]
            if target.read_text(encoding="utf-8") != original:
                raise RuntimeError(f"document changed after patch planning: {relative}")
            existing = candidates.get(relative)
            current_change = changed.get(relative, {"content": existing.content if existing else original, "summary": [existing.summary] if existing and existing.summary else []})
            current = current_change["content"]
            find = patch.get("find")
            replacement = patch.get("replacement")
            summary = patch.get("summary", "")
            if not isinstance(find, str) or not find:
                raise ValueError("find must be a non-empty exact text span")
            if not isinstance(replacement, str) or not isinstance(summary, str):
                raise ValueError("replacement and summary must be strings")
            if len(find) > 4000 or len(replacement) > 4000:
                raise ValueError("find and replacement must each be at most 4000 characters")
            matches = current.count(find)
            if matches != 1:
                raise ValueError(f"exact text must occur once in {relative}; found {matches} matches")
            content = current.replace(find, replacement, 1)
            if len(content) > self.MAX_DOCUMENT_SIZE:
                raise ValueError(f"patched Markdown exceeds {self.MAX_DOCUMENT_SIZE} characters: {relative}")
            changed[relative] = {"content": content, "summary": [*current_change["summary"], summary.strip()[:500]]}

        drafts = []
        for relative, value in changed.items():
            target = ensure_inside(self.app.binding.root, self.app.binding.root / relative)
            content = value["content"]
            errors = validate_markdown(content)
            if errors:
                raise ValueError(f"patched Markdown failed validation for {relative}: " + "; ".join(errors))
            self._approve_document_patch(target, content, task.base_contents[relative])
            summary = "；".join(value["summary"])
            candidate = DocumentCandidate(target, content, task.base_contents[relative], summary[:500])
            candidates[relative] = candidate
            if relative not in active_document_task["files"]:
                active_document_task["files"].append(relative)
            drafts.append(Draft(target, target, content, None, task.base_contents[relative], [candidate.summary]))
        return {"proposed_files": [draft.document.relative_to(self.app.binding.root).as_posix() for draft in drafts], "proposal_ready": True, "written": False}

    def _complete_document_task(self, arguments: dict, session_id: str, active_document_task: dict | None = None) -> dict:
        task = self._document_task_file(arguments, session_id)
        if active_document_task is None or active_document_task.get("id") != task.task_id:
            raise PermissionError("document task must be created in this user turn before completion")
        candidates = list(active_document_task.get("drafts", {}).values())
        if not candidates:
            raise ValueError("document task cannot complete without proposed files")
        missing = sorted(set(active_document_task["expected_files"]) - set(active_document_task["drafts"]))
        if missing:
            raise ValueError("document plan still has files without drafts: " + ", ".join(missing))
        if len(candidates) > self.MAX_DOCUMENT_FILES or sum(len(item.content) for item in candidates) > self.MAX_DOCUMENT_REVIEW_SIZE:
            raise ValueError(f"document task exceeds the {self.MAX_DOCUMENT_FILES} file / {self.MAX_DOCUMENT_REVIEW_SIZE} character review limit")
        if task.mode == "feature":
            self._assert_feature_baseline(task)
            contents = {item.target.name: item.content for item in candidates}
            errors = validate_feature_bundle(contents, task.feature_id or "", task.base_commit or "", self.app.binding.root, task.change_range)
            if errors:
                raise ValueError("feature plan failed cross-document validation: " + "; ".join(errors))
        summary = arguments.get("summary", "")
        if not isinstance(summary, str):
            raise ValueError("document task summary must be a string")
        proposals = [DocumentProposal(secrets.token_urlsafe(18), task.session_id, item.target, item.content, item.base_content, item.summary, task.task_id) for item in candidates]
        with self.lock:
            other = [item for item in self.proposals.values() if item.session_id == task.session_id and item.document_task_id != task.task_id]
            if len(other) + len(proposals) > self.MAX_DOCUMENT_FILES or sum(len(item.content) for item in other + proposals) > self.MAX_DOCUMENT_REVIEW_SIZE:
                raise RuntimeError(f"document review limit reached: up to {self.MAX_DOCUMENT_FILES} files and {self.MAX_DOCUMENT_REVIEW_SIZE} characters per conversation")
            self.proposals = {key: item for key, item in self.proposals.items() if item.document_task_id != task.task_id}
            self.proposals.update({item.proposal_id: item for item in proposals})
            task.files = sorted(str(item.target.relative_to(task.binding.root)) for item in proposals)
            task.complete = True
        active_document_task["finalized"] = True
        return {
            "document_task_id": task.task_id,
            "mode": task.mode,
            "feature_id": task.feature_id,
            "base_commit": task.base_commit,
            "change_range": task.change_range,
            "summary": summary[:1000],
            "proposed_files": task.files,
            "awaiting_user_review": len(proposals),
            "written": False,
        }

    def _assert_feature_baseline(self, task: DocumentTask) -> None:
        if task.mode != "feature":
            return
        if not task.feature_id or not task.base_commit:
            raise RuntimeError("feature plan is missing its Git baseline")
        current = GitAdapter(self.app.binding.root).head_commit()
        if current != task.base_commit:
            raise RuntimeError("Git HEAD changed during feature planning; regenerate the plan against the new baseline")

    def _feature_bundle_contents(
        self,
        task: DocumentTask,
        override_path: str | None = None,
        override_content: str | None = None,
    ) -> dict[str, str]:
        if not task.feature_id:
            raise RuntimeError("feature plan is missing its feature id")
        contents = {}
        proposals = {str(item.target.relative_to(self.app.binding.root)): item for item in self.proposals.values() if item.document_task_id == task.task_id}
        for relative in feature_document_paths(task.feature_id):
            if relative == override_path:
                contents[Path(relative).name] = override_content or ""
                continue
            proposal = proposals.get(relative)
            if proposal:
                contents[Path(relative).name] = proposal.content
                continue
            target = ensure_inside(self.app.binding.root, self.app.binding.root / relative)
            if target.is_file():
                contents[Path(relative).name] = target.read_text(encoding="utf-8")
        return contents

    def pending_proposals(self, session_id: str = "default") -> list[dict]:
        all_sessions = session_id == "*"
        session_id = self.app.session_store.normalize_id(session_id) if not all_sessions else "*"
        with self.lock:
            proposals = [item for item in self.proposals.values() if all_sessions or item.session_id == session_id]
        items = [{"proposal_id": item.proposal_id, "session_id": item.session_id, "document_task_id": item.document_task_id, "path": str(item.target.relative_to(self.app.binding.root)), "summary": item.summary, "content": item.content, "diff": "".join(difflib.unified_diff((item.base_content or "").splitlines(keepends=True), item.content.splitlines(keepends=True), fromfile=str(item.target.relative_to(self.app.binding.root)), tofile=str(item.target.relative_to(self.app.binding.root))))} for item in proposals]
        return items

    def preview_proposal(self, proposal_id: str, session_id: str, content: str) -> dict:
        session_id = self.app.session_store.normalize_id(session_id)
        if len(content) > self.MAX_DOCUMENT_SIZE:
            raise ValueError(f"edited Markdown exceeds {self.MAX_DOCUMENT_SIZE} characters")
        errors = validate_markdown(content)
        with self.lock:
            proposal = self.proposals.get(proposal_id)
            if not proposal or proposal.session_id != session_id:
                raise ValueError("proposal not found for this conversation")
            task = self.tasks.get(proposal.document_task_id)
            if task and task.mode == "patch":
                self._approve_document_patch(proposal.target, content, proposal.base_content or "")
            elif errors:
                raise ValueError("invalid proposed Markdown: " + "; ".join(errors))
            if task and task.mode == "feature":
                self._assert_feature_baseline(task)
                bundle = self._feature_bundle_contents(task, str(proposal.target.relative_to(self.app.binding.root)), content)
                errors.extend(validate_feature_bundle(bundle, task.feature_id or "", task.base_commit or "", self.app.binding.root, task.change_range))
            relative = str(proposal.target.relative_to(self.app.binding.root))
            diff = "".join(difflib.unified_diff((proposal.base_content or "").splitlines(keepends=True), content.splitlines(keepends=True), fromfile=relative, tofile=relative))
        return {"diff": diff, "errors": list(dict.fromkeys(errors))}

    def accept_proposal(self, proposal_id: str, session_id: str, content: str | None = None) -> str:
        session_id = self.app.session_store.normalize_id(session_id)
        with self.lock:
            proposal = self.proposals.get(proposal_id)
            if not proposal or proposal.session_id != session_id:
                raise ValueError("proposal not found for this conversation")
            task = self.tasks.get(proposal.document_task_id)
            if not task or task.binding != self.app.binding:
                raise RuntimeError("document task is no longer bound to the active project")
            accepted_content = content if content is not None else proposal.content
            if task.mode == "feature":
                self._assert_feature_baseline(task)
                bundle = self._feature_bundle_contents(task, str(proposal.target.relative_to(self.app.binding.root)), accepted_content)
                errors = validate_feature_bundle(bundle, task.feature_id or "", task.base_commit or "", self.app.binding.root, task.change_range)
                if errors:
                    raise ValueError("feature plan failed cross-document validation: " + "; ".join(errors))
            current = proposal.target.read_text(encoding="utf-8") if proposal.target.is_file() else None
            if current != proposal.base_content:
                raise RuntimeError(f"document changed after proposal: {proposal.target.relative_to(task.binding.root)}")
            if len(accepted_content) > self.MAX_DOCUMENT_SIZE:
                raise ValueError(f"edited Markdown exceeds {self.MAX_DOCUMENT_SIZE} characters")
            validation_errors = validate_markdown(accepted_content)
            if validation_errors:
                raise ValueError("invalid proposed Markdown: " + "; ".join(validation_errors))
            if task.mode == "patch":
                self._approve_document_patch(proposal.target, accepted_content, proposal.base_content or "")
            result = write_project_markdown(task.binding.root, proposal.target, accepted_content, proposal.base_content)
            self.proposals.pop(proposal_id, None)
            self.app.session_store.record_outcome(session_id, f"User confirmed and wrote {result}.")
            self._close_finished_document_task(task)
        return result

    def reject_proposal(self, proposal_id: str, session_id: str) -> None:
        session_id = self.app.session_store.normalize_id(session_id)
        with self.lock:
            proposal = self.proposals.get(proposal_id)
            if proposal and proposal.session_id == session_id:
                self.proposals.pop(proposal_id, None)
                task = self.tasks.get(proposal.document_task_id)
                if task:
                    self.app.session_store.record_outcome(session_id, f"User rejected the proposal for {proposal.target.relative_to(task.binding.root)}.")
                    self._close_finished_document_task(task)

    def _close_finished_document_task(self, task: DocumentTask) -> None:
        if task.complete and not any(item.document_task_id == task.task_id for item in self.proposals.values()):
            self.tasks.pop(task.task_id, None)

    def _discard_incomplete_document_task(self, active_document_task: dict, session_id: str) -> None:
        task_id = active_document_task.get("id")
        if not task_id or active_document_task.get("finalized"):
            return
        with self.lock:
            task = self.tasks.get(task_id)
            if task and task.session_id == session_id and not task.complete:
                self.tasks.pop(task_id, None)
