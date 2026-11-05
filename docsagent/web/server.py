from __future__ import annotations

import hashlib
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..agent.application import App
from ..infrastructure.git import GitAdapter, GitBaselineError, GitError
from ..project.binding import ensure_inside, is_document_file, scan_sources
from ..project.documentation import write_drafts
from ..project.files import read_text
from ..project.graph import build_graph
from ..project.parsing import parse_file


class Handler(BaseHTTPRequestHandler):
    server: "DocsServer"

    def log_message(self, format: str, *args) -> None:
        return

    @property
    def app(self) -> App:
        return self.server.app

    def _json(self, value, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            return

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length < 0 or length > 1_000_000:
            raise ValueError("request body exceeds the 1 MB limit")
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/index.html"} or parsed.path.startswith("/assets/"):
                self._static(parsed.path)
            elif parsed.path == "/api/tree":
                self._json(self.app.tree())
            elif parsed.path == "/api/document":
                self._document(parse_qs(parsed.query).get("path", [""])[0], source=False)
            elif parsed.path == "/api/source":
                self._document(parse_qs(parsed.query).get("path", [""])[0], source=True)
            elif parsed.path == "/api/git/status":
                docs_root = self.app.binding.docs_root.relative_to(self.app.binding.root).as_posix()
                adapter = GitAdapter(self.app.binding.root)
                status = adapter.document_status(docs_root)
                worktree = adapter.status()
                head_commit = ""
                if worktree["is_repo"]:
                    try:
                        head_commit = adapter.head_commit()
                    except GitBaselineError:
                        pass
                self._json({**status, "head_commit": head_commit, "worktree_entries": worktree["entries"]})
            elif parsed.path == "/api/git/markdown/status":
                self._json(GitAdapter(self.app.binding.root).markdown_status())
            elif parsed.path == "/api/git/markdown/diff":
                query = parse_qs(parsed.query)
                paths = query.get("path", [])
                if not paths:
                    paths = [item["path"] for item in GitAdapter(self.app.binding.root).markdown_status()["entries"]]
                diff, truncated = GitAdapter(self.app.binding.root).diff_markdown(paths, 50000)
                digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
                self._json({"paths": paths, "diff": diff, "digest": digest, "truncated": truncated})
            elif parsed.path == "/api/git/commits":
                self._json(GitAdapter(self.app.binding.root).project_history())
            elif parsed.path == "/api/git/compare":
                query = parse_qs(parsed.query)
                from_commit = query.get("from", [""])[0]
                to_commit = query.get("to", [""])[0]
                data = GitAdapter(self.app.binding.root).compare_to_head(from_commit)
                if to_commit and to_commit != data["to_commit"]:
                    raise ValueError("the selected range must end at the current Git HEAD")
                self._json(data)
            elif parsed.path == "/api/git/diff":
                query = parse_qs(parsed.query)
                docs_root = str(self.app.binding.docs_root.relative_to(self.app.binding.root))
                path = query.get("path", [docs_root])[0] or docs_root
                root = self.app.binding.root.resolve()
                candidate = (root / path).resolve(strict=False)
                try:
                    candidate.relative_to(root)
                except ValueError as exc:
                    raise PermissionError("Git Diff path is outside the project root") from exc
                if not candidate.is_dir():
                    raise ValueError("Git Diff path must be a directory")
                docs_path = self.app.binding.docs_root.resolve()
                try:
                    candidate.relative_to(docs_path)
                except ValueError as exc:
                    raise PermissionError("Git Diff is limited to the bound documentation tree") from exc
                diff, truncated = GitAdapter(root).diff_documents(candidate.relative_to(root).as_posix(), 50000)
                self._json({"diff": diff, "truncated": truncated})
            elif parsed.path == "/api/git/feature-plan/diff":
                feature_id = parse_qs(parsed.query).get("feature_id", [""])[0]
                self._json(GitAdapter(self.app.binding.root).feature_plan_diff(feature_id))
            elif parsed.path == "/api/git/log":
                docs_root = self.app.binding.docs_root.relative_to(self.app.binding.root).as_posix()
                self._json({"items": GitAdapter(self.app.binding.root).document_history(docs_root)})
            elif parsed.path == "/api/graph":
                query = parse_qs(parsed.query)
                facts = [parse_file(path, self.app.binding.root) for path in scan_sources(self.app.binding)]
                symbol = query.get("symbol", [None])[0]
                root_path = query.get("path", [None])[0]
                depth = int(query.get("depth", ["3"])[0])
                if not 1 <= depth <= 8:
                    raise ValueError("graph depth must be between 1 and 8")
                self._json(build_graph(self.app.binding, facts, symbol, root_path, depth))
            elif parsed.path == "/api/settings":
                self._json(self.app.agent_settings())
            elif parsed.path == "/api/mcp":
                self._json({"servers": self.app.mcp.list()})
            elif parsed.path == "/api/chat/sessions":
                self._json({"sessions": self.app.list_sessions()})
            elif parsed.path == "/api/chat/history":
                session_id = parse_qs(parsed.query).get("session_id", ["default"])[0]
                self._json({"messages": self.app.conversation(session_id)})
            elif parsed.path == "/api/chat/proposals":
                query = parse_qs(parsed.query)
                session_id = "*" if query.get("all_sessions", ["false"])[0].lower() == "true" else query.get("session_id", ["default"])[0]
                self._json({"proposals": self.app.pending_proposals(session_id)})
            elif parsed.path == "/api/workspace/pending":
                self._json(self.app.pending_work())
            elif parsed.path == "/api/chat/events":
                self._chat_events(parse_qs(parsed.query).get("id", [""])[0])
            elif parsed.path == "/api/chat/runs":
                run_id = parse_qs(parsed.query).get("id", [None])[0]
                result = self.app.persisted_runs(run_id)
                self._json(result if run_id is not None else {"runs": result})
            elif parsed.path == "/api/memory":
                self._json({"enabled": self.app.memory_enabled, "memories": self.app.list_memories() if self.app.memory_enabled else []})
            elif parsed.path == "/api/events":
                self._events(parse_qs(parsed.query).get("id", [""])[0])
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, FileNotFoundError, PermissionError, GitError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            body = self._body()
            if parsed.path == "/api/sync":
                task = self.app.start_sync()
                self._json({"task_id": task.state.task_id}, HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/sync/cancel":
                task_id = str(body.get("task_id", ""))
                if not self.app.cancel_sync(task_id):
                    raise ValueError("sync task is not running")
                self._json({"cancelled": True})
            elif parsed.path == "/api/write":
                task = self.app.tasks.get(body.get("task_id", ""))
                if not task or task.state.status != "ready":
                    raise ValueError("task is not ready for writing")
                task_binding = task.binding
                if task_binding != self.app.binding:
                    raise ValueError("project binding changed after generation; regenerate documents before writing")
                written = write_drafts(task_binding, task.state.drafts.values())
                task.state.status = "written"
                task.emit("write", status="written", message=f"wrote {len(written)} documents", written=written)
                task.compact_after_write()
                self._json({"written": written})
            elif parsed.path == "/api/chat/sessions":
                action = str(body.get("action", "create")).strip().lower()
                session_id = str(body.get("session_id", "default"))
                if action == "create":
                    title = body.get("title")
                    if title is not None and not isinstance(title, str):
                        raise ValueError("title must be a string")
                    self._json({"session": self.app.create_session(title)})
                elif action == "rename":
                    title = body.get("title")
                    if not isinstance(title, str):
                        raise ValueError("title must be a string")
                    self._json({"session": self.app.rename_session(session_id, title)})
                elif action == "delete":
                    self.app.delete_session(session_id)
                    self._json({"deleted": True})
                else:
                    raise ValueError("action must be create, rename, or delete")
            elif parsed.path == "/api/chat/clear":
                session_id = body.get("session_id", "default")
                if not isinstance(session_id, str):
                    raise ValueError("session_id must be a string")
                self.app.clear_conversation(session_id)
                self._json({"cleared": True})
            elif parsed.path == "/api/chat/start":
                prompt = str(body.get("message", "")).strip()
                session_id = str(body.get("session_id", "default"))
                context_path = str(body.get("context_path", ""))[:512] or None
                workflow = body.get("workflow")
                feature_plan = body.get("feature_plan")
                if workflow is not None and (not isinstance(workflow, str) or workflow not in {"document", "feature_plan"}):
                    raise ValueError("workflow must be document or feature_plan")
                if feature_plan is not None and not isinstance(feature_plan, dict):
                    raise ValueError("feature_plan must be a JSON object")
                task = self.app.start_chat(prompt, session_id, context_path, workflow, feature_plan)
                self._json({"task_id": task.task_id}, HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/mcp":
                action = str(body.get("action", "configure"))
                name = str(body.get("name", ""))
                if action == "remove":
                    self.app.mcp.remove(name)
                    self._json({"removed": True})
                elif action == "replace":
                    self._json({"servers": self.app.mcp.replace(body.get("servers"))})
                elif action == "configure":
                    command = body.get("command", [])
                    if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
                        raise ValueError("command must be a string array")
                    result = self.app.mcp.configure(name, body.get("transport", "stdio"), command, body.get("url", ""), body.get("enabled", False))
                    self._json({"server": result})
                else:
                    raise ValueError("MCP action must be configure or remove")
            elif parsed.path == "/api/memory":
                action = str(body.get("action", "add"))
                if action == "add":
                    self._json({"memory": self.app.add_memory(str(body.get("text", "")), str(body.get("source", "user confirmation")))})
                elif action == "delete":
                    self.app.delete_memory(body.get("id"))
                    self._json({"deleted": True})
                else:
                    raise ValueError("memory action must be add or delete")
            elif parsed.path == "/api/chat/approval":
                task_id = str(body.get("task_id", ""))
                approval_id = str(body.get("approval_id", ""))
                approved = body.get("approved")
                if not isinstance(approved, bool):
                    raise ValueError("approved must be a boolean")
                task = self.app.chat_task(task_id)
                if task is None:
                    raise ValueError("chat task not found")
                task.resolve_approval(approval_id, approved)
                self._json({"resolved": True, "approved": approved})
            elif parsed.path == "/api/chat/runs/delete":
                if body.get("confirm") is not True:
                    raise ValueError("explicit confirmation is required")
                run_id = body.get("run_id")
                if not isinstance(run_id, str) or not run_id:
                    raise ValueError("run_id is required")
                self.app.delete_persisted_run(run_id)
                self._json({"deleted": True})
            elif parsed.path == "/api/chat/cancel":
                task_id = str(body.get("task_id", ""))
                if not self.app.cancel_chat(task_id):
                    raise ValueError("chat task is not running")
                self._json({"cancelled": True})
            elif parsed.path == "/api/chat/proposal":
                session_id = str(body.get("session_id", "default"))
                action = str(body.get("action", ""))
                proposal_id = str(body.get("proposal_id", ""))
                if action == "preview":
                    content = body.get("content")
                    if not isinstance(content, str):
                        raise ValueError("content must be a string")
                    self._json(self.app.preview_proposal(proposal_id, session_id, content))
                elif action == "accept":
                    content = body.get("content")
                    if content is not None and not isinstance(content, str):
                        raise ValueError("content must be a string")
                    self._json({"written": self.app.accept_proposal(proposal_id, session_id, content)})
                elif action == "reject":
                    self.app.reject_proposal(proposal_id, session_id)
                    self._json({"rejected": True})
                    return
                else:
                    raise ValueError("action must be preview, accept, or reject")
            elif parsed.path == "/api/git/commit":
                if body.get("confirm") is not True:
                    raise ValueError("explicit confirmation is required")
                paths = body.get("paths")
                if not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths):
                    raise ValueError("documentation paths are required")
                expected_diff_digest = body.get("expected_diff_digest")
                if not isinstance(expected_diff_digest, str):
                    raise ValueError("a reviewed Markdown Diff digest is required")
                message = body.get("message", "docs: update generated documentation")
                if not isinstance(message, str):
                    raise ValueError("commit message must be a string")
                result = GitAdapter(self.app.binding.root).commit_markdown(
                    paths,
                    message,
                    confirm=True,
                    expected_diff_digest=expected_diff_digest,
                )
                self._json({"result": result})
                return
            elif parsed.path == "/api/git/feature-plan/commit":
                if body.get("confirm") is not True:
                    raise ValueError("explicit confirmation is required")
                feature_id = body.get("feature_id", "")
                if not isinstance(feature_id, str):
                    raise ValueError("feature_id must be a string")
                message = body.get("message", f"docs(feature): plan {feature_id}")
                if not isinstance(message, str):
                    raise ValueError("commit message must be a string")
                expected_diff_digest = body.get("expected_diff_digest")
                if not isinstance(expected_diff_digest, str):
                    raise ValueError("expected_diff_digest must be a string")
                result = GitAdapter(self.app.binding.root).commit_feature_plan(
                    feature_id,
                    message,
                    confirm=True,
                    expected_diff_digest=expected_diff_digest,
                )
                self._json({"result": result, "feature_id": feature_id})
                return
            elif parsed.path == "/api/binding/preview":
                source_value = body.get("source_root", "")
                docs_value = body.get("docs_root", "")
                if not isinstance(source_value, str) or not isinstance(docs_value, str):
                    raise ValueError("source_root and docs_root are required")
                source = source_value.strip()
                docs_root = docs_value.strip()
                if not source or not docs_root:
                    raise ValueError("source_root and docs_root are required")
                preview = self.app.preview_binding(source, docs_root)
                preview["worktree_entries"] = GitAdapter(self.app.binding.root).status()["entries"]
                self._json(preview)
            elif parsed.path == "/api/binding":
                source_value = body.get("source_root", "")
                docs_value = body.get("docs_root", "")
                if not isinstance(source_value, str) or not isinstance(docs_value, str):
                    raise ValueError("source_root and docs_root are required")
                source = source_value.strip()
                docs_root = docs_value.strip()
                if not source or not docs_root:
                    raise ValueError("source_root and docs_root are required")
                discard_pending = body.get("discard_pending", False)
                if not isinstance(discard_pending, bool):
                    raise ValueError("discard_pending must be a boolean")
                self._json(self.app.rebind(source, docs_root, discard_pending))
            elif parsed.path == "/api/settings":
                self._json(self.app.configure_agent(body))
            elif parsed.path == "/api/settings/test":
                settings = body.get("settings", body)
                if not isinstance(settings, dict):
                    raise ValueError("settings must be a JSON object")
                self._json({"reply": self.app.test_agent(settings)})
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, FileNotFoundError, PermissionError, GitError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _static(self, path: str) -> None:
        filename = "index.html" if path == "/" else path.lstrip("/")
        file = ensure_inside(self.app.web_root, self.app.web_root / filename)
        if not file.is_file():
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(file))[0] or "text/plain")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _document(self, relative: str, source: bool) -> None:
        path = ensure_inside(self.app.binding.root, self.app.binding.root / relative)
        if source:
            allowed = {item.relative_to(self.app.binding.root) for item in scan_sources(self.app.binding)}
            if path.relative_to(self.app.binding.root) not in allowed:
                raise PermissionError("source file is outside the bound source tree")
        else:
            if not is_document_file(self.app.binding.root, path):
                raise PermissionError("document must be a readable Markdown file inside the project")
        self._json({"path": relative, "content": read_text(path, self.app.binding.root, limit=1_000_000)})

    def _events(self, task_id: str) -> None:
        task = self.app.tasks.get(task_id)
        if not task:
            self._json({"error": "task not found"}, HTTPStatus.NOT_FOUND)
            return
        self._stream_events(task.state.events, task.condition, lambda: task.done)

    def _chat_events(self, task_id: str) -> None:
        task = self.app.chat_task(task_id)
        if not task:
            self._json({"error": "chat task not found"}, HTTPStatus.NOT_FOUND)
            return
        self._stream_events(task.events, task.stream_condition, lambda: task.done)

    def _stream_events(self, event_list, condition, is_done) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            after = int(self.headers.get("Last-Event-ID", "0") or 0)
        except ValueError:
            after = 0
        while True:
            with condition:
                events = [event for event in event_list if event["id"] > after]
                if not events and not is_done():
                    condition.wait(timeout=0.5)
                    continue
                if not events and is_done():
                    return
            try:
                for event in events:
                    self.wfile.write(f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                    after = event["id"]
            except BrokenPipeError:
                return


class DocsServer(ThreadingHTTPServer):
    def __init__(self, address, app: App):
        self.app = app
        super().__init__(address, Handler)

    def server_close(self) -> None:
        try:
            browser = getattr(self.app, "browser", None)
            if browser is not None:
                browser.close()
            mcp = getattr(self.app, "mcp", None)
            if mcp is not None:
                mcp.close()
        finally:
            super().server_close()
