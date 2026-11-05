from __future__ import annotations

import secrets
import threading
import difflib
import json
import copy
import re
from pathlib import Path
from typing import Callable

from ..infrastructure.git import GitAdapter, GitError
from .browser_access import BrowserSession
from .mcp_client import MCPManager
from .memory import MemoryStore
from .persistence import RunStore
from ..providers.credentials import load_api_key, save_api_key
from ..providers.model_client import ModelClient
from .loop import AgentLoop
from ..project.binding import (
    Binding,
    load_agent_settings,
    load_binding,
    save_agent_settings,
    save_binding,
    scan_documents,
    scan_sources,
    source_to_doc,
)
from ..project.documentation import iter_drafts, validate_markdown
from ..project.feature_plans import (
    feature_document_paths,
    validate_feature_id,
)
from ..project.files import search_text
from .context import AgentContext
from .prompts import RUNTIME_INSTRUCTIONS
from .sessions import SessionStore
from .tasks import ChatTask, Task, TaskCancelled
from .tools.schemas import ATTACHMENT_TOOLS, CODE_TOOLS, CHAT_TOOLS, DOCUMENT_TOOLS, FEATURE_TOOLS, REPOSITORY_TOOLS, WEB_TOOLS
from .tools.dispatcher import ToolDispatcher
from .workflows.documents import DocumentWorkflow


class App:
    DEFAULT_SESSION_ID = SessionStore.DEFAULT_SESSION_ID
    AGENT_SETTING_FIELDS = frozenset({
        "model_url", "model", "request_format", "endpoint", "temperature", "max_tokens",
        "instructions", "allow_source_upload", "validation_commands", "memory_enabled", "api_key", "clear_api_key",
    })

    def __init__(self, binding: Binding, web_root: Path) -> None:
        self.binding = binding
        self.web_root = web_root
        settings = load_agent_settings(binding.root)
        settings["api_key"] = load_api_key(binding.root)
        self.model = ModelClient(settings)
        self.validation_commands = self._validate_validation_commands(settings.get("validation_commands", []))
        self.memory_enabled = settings.get("memory_enabled", False)
        if not isinstance(self.memory_enabled, bool):
            raise ValueError("memory_enabled must be a boolean")
        self.tasks: dict[str, Task] = {}
        self.lock = threading.RLock()
        self.session_store = SessionStore(binding.root)
        self.run_store = RunStore(binding.root)
        self.documents = DocumentWorkflow(self)
        self.model_lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.chat_task_lock = threading.Lock()
        self.chat_tasks: dict[str, ChatTask] = {}
        self.model_generation = 0
        self.memory = MemoryStore(binding.root)
        self.browser = BrowserSession()
        self.mcp = MCPManager(binding.root)
        self.agent_loop = AgentLoop()
        self.context = AgentContext(self.binding, self.tree, self._model_snapshot)
        self.tool_dispatcher = ToolDispatcher(self)

    def agent_settings(self) -> dict:
        model = self._model_snapshot()
        return {"model_url": model.url, "model": model.model, "request_format": model.request_format, "endpoint": model.endpoint, "temperature": model.temperature, "max_tokens": model.max_tokens, "instructions": model.instructions, "allow_source_upload": model.allow_source_upload, "validation_commands": self.validation_commands, "memory_enabled": self.memory_enabled, "configured": model.available, "api_key_set": bool(model.api_key)}

    def _model_snapshot(self) -> ModelClient:
        with self.model_lock:
            model = copy.copy(self.model)
        model.request_lock = threading.RLock()
        return model

    def configure_agent(self, settings: dict) -> dict:
        with self.config_lock:
            return self._configure_agent(settings)

    @staticmethod
    def _validate_validation_commands(validation_commands) -> list[list[str]]:
        if not isinstance(validation_commands, list) or len(validation_commands) > 32:
            raise ValueError("validation_commands must be an array containing at most 32 commands")
        normalized_commands = []
        for command in validation_commands:
            if not isinstance(command, list) or not command or len(command) > 32:
                raise ValueError("each validation command must be a non-empty argv array")
            if any(not isinstance(part, str) or not part or len(part) > 512 for part in command):
                raise ValueError("validation command arguments must be non-empty strings of at most 512 characters")
            normalized_commands.append(list(command))
        return normalized_commands

    @classmethod
    def _validate_agent_settings(cls, settings: dict) -> tuple[dict, str, bool]:
        if not isinstance(settings, dict):
            raise ValueError("settings must be a JSON object")
        unknown = set(settings) - cls.AGENT_SETTING_FIELDS
        if unknown:
            raise ValueError("unsupported agent setting(s): " + ", ".join(sorted(map(str, unknown))))

        settings = dict(settings)
        for name in ("model_url", "model", "request_format", "endpoint", "instructions"):
            if name in settings and not isinstance(settings[name], str):
                raise ValueError(f"{name} must be a string")
        temperature = settings.get("temperature")
        if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float))):
            raise ValueError("temperature must be a number")
        max_tokens = settings.get("max_tokens")
        if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int)):
            raise ValueError("max_tokens must be an integer")
        if "allow_source_upload" in settings and not isinstance(settings["allow_source_upload"], bool):
            raise ValueError("allow_source_upload must be a boolean")
        if "memory_enabled" in settings and not isinstance(settings["memory_enabled"], bool):
            raise ValueError("memory_enabled must be a boolean")
        validation_commands = settings.get("validation_commands")
        if validation_commands is not None:
            settings["validation_commands"] = cls._validate_validation_commands(validation_commands)

        api_key = settings.get("api_key", "")
        if not isinstance(api_key, str):
            raise ValueError("api_key must be a string")
        api_key = api_key.strip()
        if "\n" in api_key or "\r" in api_key:
            raise ValueError("api_key cannot contain line breaks")
        settings["api_key"] = api_key

        clear_api_key = settings.pop("clear_api_key", False)
        if not isinstance(clear_api_key, bool):
            raise ValueError("clear_api_key must be a boolean")
        if clear_api_key and api_key:
            raise ValueError("provide a new API Key or clear the current one, not both")
        return settings, api_key, clear_api_key

    def _configure_agent(self, settings: dict) -> dict:
        settings, api_key, clear_api_key = self._validate_agent_settings(settings)
        candidate = self._model_snapshot()
        current_url = candidate.url
        next_url = str(settings.get("model_url", current_url)).rstrip("/")
        clear_saved_key = clear_api_key or (next_url != current_url and not api_key)
        if clear_saved_key:
            candidate.api_key = ""
        candidate.configure(settings)
        if clear_saved_key:
            save_api_key(self.binding.root, "")
        elif api_key:
            save_api_key(self.binding.root, api_key)
        save_agent_settings(self.binding.root, {key: value for key, value in settings.items() if key != "api_key"})
        with self.model_lock:
            self.model.api_key = ""
            self.model = candidate
            self.model_generation += 1
        if "validation_commands" in settings:
            self.validation_commands = settings["validation_commands"]
        if "memory_enabled" in settings:
            self.memory_enabled = settings["memory_enabled"]
        return self.agent_settings()

    def test_agent(self, settings: dict) -> str:
        settings, api_key, clear_api_key = self._validate_agent_settings(settings)
        candidate = self._model_snapshot()
        next_url = str(settings.get("model_url", candidate.url)).rstrip("/")
        if clear_api_key or (next_url != candidate.url and not api_key):
            candidate.api_key = ""
        candidate.configure(settings)
        return candidate.test_connection()

    def list_sessions(self) -> list[dict]:
        pending_counts = self.documents.proposal_counts()
        with self.chat_task_lock:
            active_ids = {item.session_id for item in self.chat_tasks.values() if not item.done}
        return self.session_store.list_sessions(pending_counts, active_ids)

    def create_session(self, title: str | None = None) -> dict:
        return self.session_store.create_session(title)

    def rename_session(self, session_id: str, title: str) -> dict:
        return self.session_store.rename_session(session_id, title)

    def delete_session(self, session_id: str) -> None:
        session_id = self.session_store.normalize_id(session_id)
        with self.lock:
            with self.chat_task_lock:
                if any(item.session_id == session_id and not item.done for item in self.chat_tasks.values()):
                    raise RuntimeError("会话正在执行 Agent 请求，请等待完成后再删除")
            if self.documents.has_proposals(session_id):
                raise RuntimeError("请先处理该会话的待审提案，再删除会话")
            if self.documents.has_incomplete_tasks(session_id):
                raise RuntimeError("会话有未完成的文档任务，请等待任务结束后再删除")
            self.session_store.delete_session(session_id)

    def clear_conversation(self, session_id: str = "default") -> None:
        self.session_store.clear_conversation(session_id)

    def new_task(self) -> Task:
        task = Task(secrets.token_hex(8), self.binding)
        with self.lock:
            if any(not existing.done for existing in self.tasks.values()):
                raise RuntimeError("a sync task is already running")
            self.tasks.clear()
            self.tasks[task.state.task_id] = task
        return task

    def start_chat(
        self,
        message: str,
        session_id: str = "default",
        context_path: str | None = None,
        workflow: str | None = None,
        feature_plan: dict | None = None,
    ) -> ChatTask:
        message = str(message or "").strip()
        if not message:
            raise ValueError("message is required")
        if len(message) > 20000:
            raise ValueError("message exceeds 20000 characters")
        if workflow is not None and (not isinstance(workflow, str) or workflow not in {"document", "feature_plan"}):
            raise ValueError("workflow must be document or feature_plan")
        if workflow == "feature_plan":
            if not isinstance(feature_plan, dict):
                raise ValueError("feature-plan details are required")
            feature_plan = {
                "feature_id": validate_feature_id(feature_plan.get("feature_id")),
                "action": feature_plan.get("action"),
                "from_commit": feature_plan.get("from_commit"),
                "to_commit": feature_plan.get("to_commit"),
            }
            if feature_plan["action"] not in {"create", "modify"}:
                raise ValueError("feature action must be create or modify")
            if feature_plan["from_commit"] is not None and not isinstance(feature_plan["from_commit"], str):
                raise ValueError("from_commit must be a full Git hash")
            if feature_plan["to_commit"] is not None and not isinstance(feature_plan["to_commit"], str):
                raise ValueError("to_commit must be a full Git hash")
            if (feature_plan["from_commit"] is None) != (feature_plan["to_commit"] is None):
                raise ValueError("both commit range endpoints are required")
        elif feature_plan is not None:
            raise ValueError("feature-plan details require the feature_plan workflow")
        session_id = self.session_store.normalize_id(session_id)
        task_id = secrets.token_hex(8)
        task = ChatTask(task_id, session_id, checkpoint=lambda event: self.run_store.append(task_id, session_id, event))
        with self.lock:
            self.session_store.ensure_session(session_id)
            with self.chat_task_lock:
                active = next((item for item in self.chat_tasks.values() if item.session_id == session_id and not item.done), None)
                if active:
                    raise RuntimeError("a chat task is already running for this session")
                self.chat_tasks[task.task_id] = task
                completed = [key for key, item in self.chat_tasks.items() if item.done]
                for key in completed[:-32]:
                    self.chat_tasks.pop(key, None)

        def run() -> None:
            task.emit("status", phase="starting", message="已接收请求，正在准备 Agent")
            try:
                answer = self.answer(
                    message,
                    session_id,
                    context_path,
                    on_event=task.emit,
                    workflow=workflow,
                    feature_plan=feature_plan,
                    approval_handler=task.request_approval,
                    cancel_check=task.check_cancelled,
                )
                task.emit("done", status="completed", answer=answer)
            except TaskCancelled:
                task.emit("done", status="cancelled", error="任务已取消")
            except Exception as exc:
                task.emit("done", status="error", error=str(exc))
            finally:
                with task.condition:
                    task.done = True
                    task.condition.notify_all()
                with task.stream_condition:
                    task.stream_condition.notify_all()

        threading.Thread(target=run, daemon=True).start()
        return task

    def chat_task(self, task_id: str) -> ChatTask | None:
        with self.chat_task_lock:
            return self.chat_tasks.get(task_id)

    def persisted_runs(self, run_id: str | None = None) -> list[dict] | dict:
        if run_id is not None:
            return {"run_id": run_id, "events": self.run_store.events(run_id)}
        return self.run_store.list_runs()

    def delete_persisted_run(self, run_id: str) -> None:
        self.run_store.delete(run_id)

    def list_memories(self) -> list[dict]:
        return self.memory.list()

    def add_memory(self, text: str, source: str) -> dict:
        if not self.memory_enabled:
            raise PermissionError("long-term memory is disabled")
        return self.memory.add(text, source)

    def delete_memory(self, memory_id: int) -> None:
        if not self.memory_enabled:
            raise PermissionError("long-term memory is disabled")
        self.memory.delete(memory_id)

    def cancel_chat(self, task_id: str) -> bool:
        task = self.chat_task(task_id)
        if task is None or task.done:
            return False
        task.request_cancel()
        return True

    def start_sync(self) -> Task:
        task = self.new_task()
        binding = task.binding
        model = self._model_snapshot()

        def run() -> None:
            try:
                task.emit("status", status="running", message="正在扫描源码")
                sources = scan_sources(binding)
                task.emit("status", status="running", message=f"发现 {len(sources)} 个源码文件")
                if not sources:
                    task.emit("status", status="empty", message="绑定源码目录中没有可解析的代码文件")
                    return
                validation_error_file = None
                drafts = iter_drafts(
                    binding,
                    model,
                    sources=sources,
                    on_file_started=lambda path: task.emit("status", status="file_started", message="正在解析并生成文档", file=str(source_to_doc(binding, path).relative_to(binding.root))),
                    cancel_check=task.check_cancelled,
                )
                for draft in drafts:
                    task.check_cancelled()
                    relative = str(draft.document.relative_to(binding.root))
                    task.state.drafts[relative] = draft
                    for offset in range(0, len(draft.content), 1200):
                        payload = {"file": relative, "content": draft.content[offset : offset + 1200]}
                        if offset == 0:
                            payload["base_content"] = draft.base_content or ""
                        task.emit("draft", **payload)
                    errors = draft.validation_errors or validate_markdown(draft.content)
                    diff = "".join(difflib.unified_diff((draft.base_content or "").splitlines(keepends=True), draft.content.splitlines(keepends=True), fromfile=relative, tofile=relative))
                    task.emit("status", status="file_ready" if not errors else "file_error", file=relative, errors=errors, warnings=draft.warnings, diff=diff)
                    if errors:
                        validation_error_file = relative
                        break
                if validation_error_file:
                    task.emit("status", status="error", message=f"{validation_error_file} 校验失败，已停止同步；请修复后重新生成")
                else:
                    task.emit("status", status="ready", message=f"已生成 {len(task.state.drafts)}/{len(sources)} 个文档")
            except TaskCancelled:
                task.emit("status", status="cancelled", message="同步已取消")
            except Exception as exc:
                task.emit("status", status="error", message=f"同步失败：{exc}")
            finally:
                task.emit("done", status=task.state.status)
                with task.condition:
                    task.done = True
                    task.condition.notify_all()

        threading.Thread(target=run, daemon=True).start()
        return task

    def cancel_sync(self, task_id: str) -> bool:
        with self.lock:
            task = self.tasks.get(task_id)
        if not task or task.done:
            return False
        task.request_cancel()
        return True

    def pending_work(self) -> dict:
        with self.lock:
            sync_tasks = list(self.tasks.values())
        with self.chat_task_lock:
            chat_tasks = list(self.chat_tasks.values())
        proposal_count, proposal_paths = self.documents.pending_summary()
        message_count = self.session_store.total_message_count()

        sync_files = sorted({
            path
            for task in sync_tasks
            if task.state.drafts
            for path in task.state.drafts
        })
        active_sync = any(not task.done for task in sync_tasks)
        active_agent = any(not task.done for task in chat_tasks)
        return {
            "active_sync": active_sync,
            "active_agent": active_agent,
            "sync_files": sync_files,
            "proposal_paths": proposal_paths,
            "message_count": message_count,
            "has_pending": bool(active_sync or active_agent or sync_files or proposal_count or message_count),
        }

    def preview_binding(self, source_root: str, docs_root: str) -> dict:
        binding = load_binding(self.binding.root, source_root, docs_root)
        pending = self.pending_work()
        tree = self.tree(binding)
        return {"tree": tree, "pending": pending, "can_apply": not pending["active_sync"] and not pending["active_agent"]}

    def rebind(self, source_root: str, docs_root: str, discard_pending: bool = False) -> dict:
        with self.lock:
            candidate = load_binding(self.binding.root, source_root, docs_root)
            if candidate == self.binding:
                return self.tree()
            pending = self.pending_work()
            if pending["active_sync"] or pending["active_agent"]:
                raise RuntimeError("wait for active sync and Agent tasks to finish before changing the binding")
            if pending["has_pending"] and not discard_pending:
                raise RuntimeError("pending sessions, drafts, or proposals require explicit confirmation before changing the binding")
            binding = save_binding(self.binding.root, source_root, docs_root)
            self.binding = binding
            self.tasks.clear()
            self.documents.reset()
            with self.chat_task_lock:
                self.chat_tasks.clear()
            self.session_store.reset()
        return self.tree()

    def tree(self, binding: Binding | None = None) -> dict:
        binding = binding or self.binding
        source_files = scan_sources(binding)
        expected_documents = {str(source_to_doc(binding, path).relative_to(binding.root)) for path in source_files}
        document_files = scan_documents(binding.root)
        document_paths = [str(path.relative_to(binding.root)) for path in document_files]
        feature_root = binding.root / "docs" / "features"
        feature_ids = []
        if feature_root.is_dir() and not feature_root.is_symlink():
            for candidate in feature_root.iterdir():
                if not candidate.is_dir() or candidate.is_symlink():
                    continue
                try:
                    feature_ids.append(validate_feature_id(candidate.name))
                except ValueError:
                    continue
        documents = [
            {
                "path": path,
                "kind": "document",
                "status": "synced" if path in expected_documents else "orphan" if binding.docs_root in (binding.root / path).parents else "manual",
            }
            for path in document_paths
        ]
        sources = [{"path": str(path.relative_to(binding.root)), "kind": "source", "status": "synced" if str(source_to_doc(binding, path).relative_to(binding.root)) in document_paths else "missing"} for path in source_files]
        return {
            "project_name": binding.root.name or str(binding.root),
            "feature_ids": sorted(feature_ids),
            "binding": {"source_root": str(binding.source_root.relative_to(binding.root)), "docs_root": str(binding.docs_root.relative_to(binding.root))},
            "documents": documents,
            "sources": sorted(sources, key=lambda item: item["path"]),
        }


    @staticmethod
    def document_request_allowed(message: str) -> bool:
        action = r"(?:写(?:一份|个|篇|一下)?|撰写|编写|起草|创建|新建|设计|生成|更新|修改|补充|完善|整理|改(?:为|成)?|翻译|write|draft|create|make|design|update|revise|translate|convert|change)"
        target = r"(?:所有(?:项目)?文档|所有文件(?:的)?(?:职责|责任|说明)?|文件(?:职责|责任|说明)|代码(?:文件)?(?:说明|文档)|函数说明|项目文档|文档目录|文档|README|markdown|\.md|docs?|document(?:ation)?)"
        explicit_negation = rf"(?:^|[，。；\s])(?:不要|别|无需|不需要|don't|do not|no need to).{{0,12}}{action}.{{0,16}}{target}(?:[，。；\s]|$)"
        if re.search(explicit_negation, message, re.IGNORECASE):
            return False
        if re.match(r"\s*(?:怎么|如何|为什么)", message) or re.match(r"\s*(?:how|why|what)\b", message, re.IGNORECASE):
            return False
        return bool(re.search(action, message, re.IGNORECASE) and re.search(target, message, re.IGNORECASE))

    @staticmethod
    def feature_plan_request_allowed(message: str) -> bool:
        if re.match(r"\s*(?:怎么|如何|为什么)", message) or re.match(r"\s*(?:how|why|what)\b", message, re.IGNORECASE):
            return False
        if re.search(r"(?:不要|别|无需|不需要|don't|do not|no need to).{0,16}(?:功能计划|需求计划|实现计划|feature plan)", message, re.IGNORECASE):
            return False
        return bool(re.search(
            r"(?:新增需求|添加需求|新增功能|添加功能|功能计划|需求计划|实现计划|实施计划|规划功能|规划需求|feature plan|implementation plan)",
            message,
            re.IGNORECASE,
        ))

    def pending_proposals(self, session_id: str = "default") -> list[dict]:
        return self.documents.pending_proposals(session_id)

    def preview_proposal(self, proposal_id: str, session_id: str, content: str) -> dict:
        return self.documents.preview_proposal(proposal_id, session_id, content)

    def accept_proposal(self, proposal_id: str, session_id: str, content: str | None = None) -> str:
        return self.documents.accept_proposal(proposal_id, session_id, content)

    def reject_proposal(self, proposal_id: str, session_id: str) -> None:
        self.documents.reject_proposal(proposal_id, session_id)

    def answer(
        self,
        message: str,
        session_id: str = "default",
        context_path: str | None = None,
        context_paths: list[str] | None = None,
        on_event: Callable[..., None] | None = None,
        workflow: str | None = None,
        feature_plan: dict | None = None,
        approval_handler: Callable[[dict], bool] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> str:
        def emit(event: str, **payload) -> None:
            if on_event is None:
                return
            on_event(event, **payload)

        session_id = self.session_store.normalize_id(session_id)
        lowered = message.lower().strip()
        if lowered.startswith(("搜索 ", "查找 ", "search ")):
            query = message.split(maxsplit=1)[1] if len(message.split(maxsplit=1)) == 2 else ""
            if not query.strip():
                emit("status", phase="error", message="搜索关键词为空")
                return self._record_local_turn(session_id, message, "请提供要搜索的关键词。")
            emit("status", phase="search", message=f"正在搜索项目：{query[:80]}")
            matches = search_text(self.binding.root, query, self.context.tool_paths("all"), limit=20)
            if not matches["total"]:
                emit("status", phase="completed", message="搜索完成：没有匹配结果")
                return self._record_local_turn(session_id, message, f"没有找到包含“{query}”的文本。")
            result = "\n".join(f"{item['path']}:{item['line']} {item['text']}" for item in matches["items"])
            answer = result + (f"\n\n已显示前 {matches['shown']} 条，仍有更多匹配结果。" if matches["truncated"] else "")
            emit("status", phase="finalizing", message=f"搜索完成：{matches['shown']} 条结果")
            return self._record_local_turn(session_id, message, answer)
        feature_plan_requested = workflow == "feature_plan" or self.feature_plan_request_allowed(message)
        if workflow == "feature_plan" and not feature_plan:
            raise ValueError("feature-plan details are required")
        asks_git = any(word in message.lower() for word in ("git", "变更", "优化方向", "历史"))
        with self.model_lock:
            model = copy.copy(self.model)
            generation = self.model_generation
            model.request_lock = threading.RLock()
        git_context: dict = {}
        git_error = ""
        feature_plan_context = None
        feature_plan_options = feature_plan
        if feature_plan_requested:
            if not model.allow_source_upload:
                emit("status", phase="error", message="未开启项目上下文读取权限")
                return self._record_local_turn(session_id, message, "功能规划需要读取源码、测试和项目配置。请先在 Agent 设置中开启“允许 Agent 读取项目内容”。")
            adapter = GitAdapter(self.binding.root)
            if not adapter.is_repo():
                emit("status", phase="error", message="项目尚未初始化 Git")
                return self._record_local_turn(session_id, message, "功能计划需要 Git 基线。请先在项目目录执行 git init，并创建一次基线提交。DocsAgent 不会自动初始化或提交。")
            try:
                git_status = adapter.status()
                base_commit = adapter.head_commit()
                feature_plan_context = {
                    "base_commit": base_commit,
                    "branch": git_status["branch"],
                    "worktree_changes": git_status["entries"],
                    "code_execution": "DocsAgent may edit project files and run configured validation commands; Git commits remain user-controlled",
                }
                if feature_plan:
                    feature_plan_options = {**feature_plan, "base_commit": base_commit}
                    feature_plan_context["selected_feature"] = {
                        "feature_id": feature_plan["feature_id"],
                        "action": feature_plan["action"],
                    }
                    feature_plan_context["change_range"] = None
                    if feature_plan.get("from_commit"):
                        compared = adapter.compare_to_head(feature_plan["from_commit"])
                        if feature_plan.get("to_commit") != compared["to_commit"]:
                            raise ValueError("selected range must end at the current Git HEAD")
                        if compared["truncated"]:
                            raise ValueError("selected Git diff is too large; choose a narrower commit range")
                        feature_plan_context["change_range"] = compared
                        feature_plan_context["worktree_changes"] = git_status["entries"]
            except GitError as exc:
                emit("status", phase="error", message="无法读取功能计划基线")
                return self._record_local_turn(session_id, message, f"无法读取 Git 基线：{exc}。请先创建有效的基线提交。")
            except ValueError as exc:
                emit("status", phase="error", message="功能计划的 Git 区间无效")
                return self._record_local_turn(session_id, message, str(exc))
            allowed_paths = set(feature_document_paths(feature_plan["feature_id"])) if feature_plan else set()
            unrelated = [entry["path"] for entry in git_status["entries"] if entry["path"] not in allowed_paths]
            if unrelated:
                emit("status", phase="error", message="功能计划要求工作区在功能包外干净")
                return self._record_local_turn(session_id, message, "请先处理这些未提交变更，再制定功能计划：" + ", ".join(unrelated[:20]))
        if asks_git:
            if not GitAdapter(self.binding.root).is_repo():
                emit("status", phase="error", message="项目尚未初始化 Git")
                return self._record_local_turn(session_id, message, "当前项目尚未初始化 Git，无法读取文档历史或变更。请先在项目根目录执行 git init。")
            if model.available and not model.allow_source_upload:
                emit("status", phase="error", message="未开启项目上下文读取权限")
                return self._record_local_turn(session_id, message, "Agent 当前没有项目上下文读取权限。需要查看 Git 状态、差异或历史时，请在设置中开启“允许 Agent 读取项目内容”。")
            try:
                git_context = self.context.git_context()
            except GitError as exc:
                git_context = {"error": str(exc)}
                git_error = str(exc)
        if model.available:
            emit("status", phase="context", message="正在整理项目上下文")
            system = f"{model.instructions.strip()}\n\n{RUNTIME_INSTRUCTIONS}".strip()
            workspace_context = self.context.workspace_context(context_path, model, context_paths)
            request_context = {"request": message, "project_context": workspace_context}
            if git_context:
                request_context["git_context"] = git_context
            if feature_plan_context:
                request_context["feature_plan_context"] = feature_plan_context
            history, pending_outcomes = self.session_store.request_context(session_id, 20)
            if pending_outcomes:
                request_context["workbench_feedback"] = pending_outcomes
            if self.memory_enabled:
                request_context["long_term_memory"] = self.memory.list()
            user_content = json.dumps(request_context, ensure_ascii=False)
            if len(user_content) > 64000:
                emit("status", phase="error", message="项目上下文超过当前请求上限")
                return self._record_local_turn(session_id, message, "当前请求上下文超过 64000 个字符，请缩小所选文件范围或先查看较小的 Git 差异后重试。尚未发送模型请求。")
            messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": user_content}]
            tools = list(CHAT_TOOLS) if model.allow_source_upload else []
            if model.allow_source_upload:
                tools.extend(CODE_TOOLS)
                tools.extend(ATTACHMENT_TOOLS)
            tools.extend(WEB_TOOLS)
            document_allowed = workflow == "document" or self.document_request_allowed(message) or feature_plan_requested
            if document_allowed:
                if feature_plan_requested:
                    tools.extend(
                        item for item in DOCUMENT_TOOLS
                        if item["function"]["name"] in {"document_task_file", "complete_document_task"}
                    )
                else:
                    tools.extend(DOCUMENT_TOOLS)
            if feature_plan_requested:
                tools.extend(REPOSITORY_TOOLS)
                tools.extend(FEATURE_TOOLS)
            mcp_tools = self.mcp.model_tools()
            tools.extend(mcp_tools)
            with self.model_lock:
                if self.model_generation != generation:
                    emit("status", phase="error", message="Agent 设置在请求期间发生变化")
                    return self._record_local_turn(session_id, message, "Agent 设置在本次请求准备期间发生变化；未发送模型请求，请重试。")
            active_document_task = {}
            execute = lambda name, arguments: self.tool_dispatcher.run(
                name,
                arguments,
                session_id,
                document_allowed,
                active_document_task,
                feature_plan_requested,
                feature_plan_options,
                approval_handler,
                cancel_check,
            )
            try:
                answer = self.agent_loop.run(
                    model,
                    messages,
                    tools,
                    execute if tools else None,
                    max_rounds=13 if document_allowed else 8,
                    max_tool_calls=12,
                    max_tool_output_chars=30000,
                    max_context_chars=128000,
                    on_event=on_event,
                    cancel_check=cancel_check,
                )
                if active_document_task.get("id") and not active_document_task.get("finalized"):
                    raise RuntimeError("document task ended before completion; drafts were discarded and nothing was written")
            finally:
                self.documents._discard_incomplete_document_task(active_document_task, session_id)
            with self.model_lock:
                current_generation = self.model_generation
            if current_generation == generation:
                self._append_conversation(
                    session_id,
                    [{"role": "user", "content": message}, {"role": "assistant", "content": answer}],
                    consumed_outcomes=pending_outcomes,
                )
            return answer
        if feature_plan_requested:
            emit("status", phase="error", message="功能规划需要配置模型服务")
            return self._record_local_turn(session_id, message, "功能计划生成需要配置模型服务；项目事实读取和 Git 基线检查已通过。")
        if workflow == "document" or self.document_request_allowed(message) or any(word in lowered for word in ("同步", "生成文档", "代码文档")):
            emit("status", phase="completed", message="未配置模型服务，已返回本地功能说明")
            return self._record_local_turn(session_id, message, "自然语言文档起草需要先配置模型服务。代码说明文档可点击“同步文档”生成本地草稿，再检查 Diff 并确认写入。")
        if git_error:
            emit("status", phase="error", message="Git 上下文读取失败，正在返回错误说明")
            return self._record_local_turn(session_id, message, f"无法读取 Git 上下文：{git_error}。如需 Agent 分析项目变更，请在 Agent 设置中开启项目内容共享。")
        emit("status", phase="completed", message="当前未配置模型服务，已返回本地功能说明")
        return self._record_local_turn(session_id, message, "当前未配置模型服务。你可以使用“搜索 关键词”、同步文档、Git Diff 和静态实现链路功能。")

    def _record_local_turn(self, session_id: str, message: str, answer: str) -> str:
        return self.session_store.record_local_turn(session_id, message, answer)

    def _append_conversation(self, session_id: str, messages: list[dict[str, str]], consumed_outcomes: list[str] | None = None) -> None:
        self.session_store.append(session_id, messages, consumed_outcomes)

    def conversation(self, session_id: str = "default") -> list[dict[str, str]]:
        return self.session_store.conversation(session_id)
