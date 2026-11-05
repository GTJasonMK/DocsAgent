from __future__ import annotations

import json

from ...agent.command_runner import run_project_command
from ...project.binding import ensure_inside
from ...project.changes import edit_project_file
from ...project.files import read_lines, search_text
from ...project.feature_plans import FEATURE_DOCUMENTS, feature_directory
from ...agent.web_access import read_web_page, search_web
from ...agent.browser_access import BrowserSession
from ...attachments import inspect_attachment
from .schemas import ATTACHMENT_TOOLS, CODE_TOOLS, CHAT_TOOLS, DOCUMENT_TOOLS, FEATURE_TOOLS, REPOSITORY_TOOLS, WEB_TOOLS


class ToolDispatcher:
    """Validate tool requests and route them to scoped project capabilities."""

    def __init__(self, app) -> None:
        self.app = app

    def run(
        self,
        name: str,
        arguments: dict,
        session_id: str = "default",
        allow_document_proposal: bool = False,
        active_document_task: dict | None = None,
        allow_feature_plan: bool = False,
        feature_plan_options: dict | None = None,
        approval_handler=None,
        cancel_check=None,
    ) -> dict:
        all_tools = CHAT_TOOLS + CODE_TOOLS + WEB_TOOLS + ATTACHMENT_TOOLS + REPOSITORY_TOOLS + FEATURE_TOOLS + DOCUMENT_TOOLS
        if name.startswith("mcp."):
            if approval_handler is None or not approval_handler({
                "action": "mcp_tool",
                "summary": "调用 MCP 工具（服务器描述和只读标记均不可信）",
                "target": name,
                "risk": "external_write",
            }):
                raise PermissionError("MCP tool call was not approved")
            return self.app.mcp.call(name, arguments)
        if name not in {item["function"]["name"] for item in all_tools}:
            raise ValueError(f"unknown tool: {name}")
        if name in {"edit_project_file", "run_project_command"}:
            if not self.app._model_snapshot().allow_source_upload:
                raise PermissionError("project editing and command execution require enabling project-content access")
            if name == "edit_project_file":
                return edit_project_file(
                    self.app.binding.root,
                    str(arguments.get("path", "")),
                    arguments.get("content"),
                    arguments.get("expected_sha256"),
                )
            argv = arguments.get("argv")
            if not isinstance(argv, list):
                raise ValueError("argv must be an array")
            allowed = [list(item) for item in getattr(self.app, "validation_commands", [])]
            if argv not in allowed:
                if approval_handler is None or not approval_handler({
                    "action": "run_project_command",
                    "summary": "执行未加入项目白名单的命令",
                    "target": json.dumps(argv, ensure_ascii=False),
                    "risk": "command",
                }):
                    raise PermissionError("command was not approved")
                allowed.append(argv)
            return run_project_command(
                self.app.binding.root,
                argv,
                allowed,
                arguments.get("timeout_seconds", 120),
                cancel_check,
            )
        if name in {"search_web", "read_web_page"}:
            if name == "search_web":
                return search_web(str(arguments.get("query", "")), int(arguments.get("limit", 5)))
            return read_web_page(str(arguments.get("url", "")))
        if name == "browser_read_page":
            browser = getattr(self.app, "browser", None)
            if browser is None:
                browser = BrowserSession()
                self.app.browser = browser
            return browser.read_page(str(arguments.get("url", "")), int(arguments.get("max_chars", 16000)))
        if name == "inspect_project_attachment":
            if not self.app._model_snapshot().allow_source_upload:
                raise PermissionError("attachment inspection requires enabling project-content access")
            relative = str(arguments.get("path", ""))
            target = ensure_inside(self.app.binding.root, self.app.binding.root / relative)
            return inspect_attachment(target)
        repository_tools = {"list_repository_files", "search_repository", "read_repository_file"}
        if name in repository_tools:
            if not allow_feature_plan:
                raise PermissionError("repository-wide reads require an explicit feature-planning request")
            if not self.app._model_snapshot().allow_source_upload:
                raise PermissionError("feature planning requires enabling project-content access in Agent settings")
            paths = self.app.context.repository_paths()
            if name == "list_repository_files":
                limit = int(arguments.get("limit", 50))
                offset = int(arguments.get("offset", 0))
                if not 1 <= limit <= 100 or offset < 0:
                    raise ValueError("file list limit must be between 1 and 100 and offset cannot be negative")
                items = [
                    {"path": path.relative_to(self.app.binding.root).as_posix(), "kind": path.suffix.lower().lstrip(".")}
                    for path in paths
                ]
                selected = items[offset : offset + limit]
                next_offset = offset + len(selected) if offset + len(selected) < len(items) else None
                return {"items": selected, "offset": offset, "shown": len(selected), "total": len(items), "truncated": next_offset is not None, "next_offset": next_offset}
            if name == "search_repository":
                query = str(arguments.get("query", "")).strip()
                limit = int(arguments.get("limit", 20))
                offset = int(arguments.get("offset", 0))
                if not 1 <= len(query) <= 200 or not 1 <= limit <= 20 or offset < 0:
                    raise ValueError("search query, limit or offset is invalid")
                return search_text(self.app.binding.root, query, paths, offset, limit)
            relative = str(arguments.get("path", ""))
            target = ensure_inside(self.app.binding.root, self.app.binding.root / relative)
            allowed = {path.relative_to(self.app.binding.root).as_posix() for path in paths}
            if target.relative_to(self.app.binding.root).as_posix() not in allowed:
                raise PermissionError("file is outside the safe repository-planning scope")
            offset = int(arguments.get("offset", 1))
            limit = int(arguments.get("limit", 120))
            if offset < 1 or not 1 <= limit <= 200:
                raise ValueError("line offset must be positive and limit must be between 1 and 200")
            result = read_lines(target, self.app.binding.root, offset, limit)
            return {"path": target.relative_to(self.app.binding.root).as_posix(), **result}
        document_tools = {"list_document_paths", "create_document_plan", "create_feature_plan", "document_task_file", "read_document_sections", "patch_markdown_documents", "complete_document_task"}
        if name in document_tools:
            if not allow_document_proposal:
                raise PermissionError("document tasks require explicit user documentation intent")
            if name in {"list_document_paths", "read_document_sections", "patch_markdown_documents", "create_feature_plan"} and not self.app._model_snapshot().allow_source_upload:
                raise PermissionError("document editing requires enabling project-content access in Agent settings")
            if name in {"create_document_plan", "create_feature_plan"}:
                if name == "create_document_plan" and allow_feature_plan:
                    raise PermissionError("use create_feature_plan for this feature-planning request")
                if active_document_task is None or active_document_task.get("id"):
                    raise PermissionError("create_document_plan can only be called once at the start of a document task")
                if name == "create_feature_plan":
                    if not allow_feature_plan:
                        raise PermissionError("feature plans require an explicit feature-planning request")
                    feature_id = arguments.get("feature_id")
                    action = arguments.get("action")
                    if feature_plan_options:
                        if feature_id != feature_plan_options["feature_id"] or action != feature_plan_options["action"]:
                            raise ValueError("feature plan must use the ID and action selected in the plan form")
                    task_arguments = {
                        "goal": arguments.get("goal"),
                        "mode": "feature",
                        "target_directory": feature_directory(feature_id).as_posix(),
                        "files": list(FEATURE_DOCUMENTS),
                        "expected_base_commit": feature_plan_options.get("base_commit") if feature_plan_options else None,
                        "change_range": (
                            (feature_plan_options["from_commit"], feature_plan_options["to_commit"])
                            if feature_plan_options and feature_plan_options.get("from_commit")
                            else None
                        ),
                    }
                else:
                    task_arguments = arguments
                task = self.app.documents._create_document_task(task_arguments, session_id)
                self.app.documents._activate_document_task(active_document_task, session_id, task["document_task_id"])
                return task
            task_id = str(arguments.get("document_task_id", ""))
            if active_document_task and active_document_task.get("mode") == "feature" and name not in {"document_task_file", "complete_document_task"}:
                raise PermissionError("feature-plan tasks only accept the three planned feature documents")
            if name == "list_document_paths":
                return self.app.documents._list_document_paths(arguments.get("include_code_reference") is True, int(arguments.get("offset", 0)), int(arguments.get("limit", 30)))
            if not active_document_task or task_id != active_document_task.get("id"):
                raise PermissionError("document task ID does not match the task created in this conversation turn")
            if name == "document_task_file":
                draft = self.app.documents._proposed_draft(arguments, session_id, task_id, active_document_task)
                return {"path": str(draft.document.relative_to(self.app.binding.root)), "proposal_ready": True, "written": False}
            if name == "read_document_sections":
                return self.app.documents._read_document_sections(arguments, session_id, task_id, active_document_task)
            if name == "patch_markdown_documents":
                return self.app.documents._propose_document_patches(arguments, session_id, task_id, active_document_task)
            return self.app.documents._complete_document_task(arguments, session_id, active_document_task)
        kind = str(arguments.get("kind", "all"))
        if kind not in {"source", "document", "all"}:
            raise ValueError("kind must be source, document, or all")
        if name == "list_project_files":
            limit = int(arguments.get("limit", 50))
            offset = int(arguments.get("offset", 0))
            if not 1 <= limit <= 100 or offset < 0:
                raise ValueError("file list limit must be between 1 and 100 and offset cannot be negative")
            tree = self.app.tree()
            allowed_paths = {path.relative_to(self.app.binding.root).as_posix() for path in self.app.context.tool_paths(kind)}
            items = []
            if kind in {"source", "all"}:
                items.extend(item for item in tree["sources"] if item["path"] in allowed_paths)
            if kind in {"document", "all"}:
                items.extend(item for item in tree["documents"] if item["path"] in allowed_paths)
            total = len(items)
            shown = items[offset : offset + limit]
            next_offset = offset + len(shown) if offset + len(shown) < total else None
            return {"items": shown, "offset": offset, "shown": len(shown), "total": total, "truncated": next_offset is not None, "next_offset": next_offset}
        if name == "search_project":
            query = str(arguments.get("query", "")).strip()
            if not 1 <= len(query) <= 200:
                raise ValueError("query must contain 1 to 200 characters")
            limit = int(arguments.get("limit", 20))
            offset = int(arguments.get("offset", 0))
            if not 1 <= limit <= 20 or offset < 0:
                raise ValueError("search limit must be between 1 and 20 and offset cannot be negative")
            return search_text(self.app.binding.root, query, self.app.context.tool_paths(kind), offset, limit)
        if name == "read_project_file":
            relative = str(arguments.get("path", ""))
            target = ensure_inside(self.app.binding.root, self.app.binding.root / relative)
            allowed = {path.relative_to(self.app.binding.root).as_posix() for path in self.app.context.tool_paths(kind)}
            if target.relative_to(self.app.binding.root).as_posix() not in allowed:
                raise PermissionError("file is outside the bound source and documentation trees")
            offset = int(arguments.get("offset", 1))
            limit = int(arguments.get("limit", 120))
            if offset < 1 or not 1 <= limit <= 200:
                raise ValueError("line offset must be positive and limit must be between 1 and 200")
            return {"path": target.relative_to(self.app.binding.root).as_posix(), **read_lines(target, self.app.binding.root, offset, limit)}
        raise ValueError(f"unknown tool: {name}")
