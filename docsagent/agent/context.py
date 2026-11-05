from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..attachments import SUPPORTED_EXTENSIONS, inspect_attachment
from ..infrastructure.git import GitAdapter
from ..project.binding import Binding, ensure_inside, is_document_file, scan_sources
from ..project.files import planning_files, read_excerpt
from ..providers.model_client import ModelClient


class AgentContext:
    """Build bounded project and Git context for an agent request."""

    def __init__(
        self,
        binding: Binding,
        tree_provider: Callable[[], dict],
        model_snapshot: Callable[[], ModelClient],
    ) -> None:
        self.binding = binding
        self._tree_provider = tree_provider
        self._model_snapshot = model_snapshot

    def git_context(self) -> dict:
        adapter = GitAdapter(self.binding.root)
        docs_root = str(self.binding.docs_root.relative_to(self.binding.root))
        diff, diff_truncated = adapter.diff_documents(docs_root, 8000)
        return {
            "status": adapter.document_status(docs_root),
            "diff": diff,
            "diff_truncated": diff_truncated,
            "history": adapter.document_history(docs_root),
        }

    def workspace_context(
        self,
        context_path: str | None = None,
        model: ModelClient | None = None,
        context_paths: list[str] | None = None,
    ) -> dict:
        model = model or self._model_snapshot()
        payload = {"project_context_enabled": model.allow_source_upload}
        if model.allow_source_upload:
            payload["binding"] = {
                "source_root": self.binding.source_root.relative_to(self.binding.root).as_posix(),
                "docs_root": self.binding.docs_root.relative_to(self.binding.root).as_posix(),
            }
        selected_paths = [str(path) for path in (context_paths or []) if str(path).strip()]
        if context_path:
            selected_paths.insert(0, context_path)
        selected_documents = []
        selected_sources = []
        selected_attachments = []
        errors = []
        if len(selected_paths) > 4:
            errors.append("only the first 4 selected paths were included to stay within the context budget")
        seen = set()
        for raw_path in selected_paths[:4]:
            try:
                selected_path = ensure_inside(self.binding.root, self.binding.root / raw_path)
                relative = selected_path.relative_to(self.binding.root).as_posix()
                if relative in seen:
                    continue
                seen.add(relative)
                is_document = is_document_file(self.binding.root, selected_path)
                is_source = model.allow_source_upload and selected_path in scan_sources(self.binding)
                if is_document:
                    content, truncated = read_excerpt(selected_path, self.binding.root, 12000)
                    selected_documents.append({"path": relative, "content": content, "truncated": truncated})
                elif is_source:
                    content, truncated = read_excerpt(selected_path, self.binding.root, 12000)
                    selected_sources.append({"path": relative, "content": content, "truncated": truncated})
                elif model.allow_source_upload and selected_path.is_file() and selected_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    attachment = inspect_attachment(selected_path)
                    attachment["path"] = relative
                    attachment["text"] = str(attachment.get("text", ""))[:24000]
                    selected_attachments.append(attachment)
                else:
                    errors.append(f"{relative}: selected path is outside the readable document and source trees")
            except (ValueError, OSError, RuntimeError) as exc:
                errors.append(f"{raw_path}: {exc}")
        if selected_documents:
            payload["user_selected_document"] = selected_documents[0]
            if len(selected_documents) > 1:
                payload["user_selected_documents"] = selected_documents
        if selected_sources:
            payload["user_selected_source"] = selected_sources[0]
            if len(selected_sources) > 1:
                payload["user_selected_sources"] = selected_sources
        if selected_attachments:
            payload["user_selected_attachment"] = selected_attachments[0]
            if len(selected_attachments) > 1:
                payload["user_selected_attachments"] = selected_attachments
        if errors:
            payload["selected_context_errors"] = errors
            if len(errors) == 1:
                payload["selected_context_error"] = errors[0]
        return payload

    def tool_paths(self, kind: str) -> list[Path]:
        tree = self._tree_provider()
        source_paths = {item["path"] for item in tree["sources"]}
        document_paths = {item["path"] for item in tree["documents"]}
        allowed = source_paths if kind == "source" else document_paths if kind == "document" else source_paths | document_paths
        paths = []
        for relative in sorted(allowed):
            try:
                paths.append(ensure_inside(self.binding.root, self.binding.root / relative))
            except ValueError:
                continue
        return paths

    def repository_paths(self) -> list[Path]:
        return planning_files(self.binding.root)
