from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from docsagent.agent.application import App
from docsagent.infrastructure.git import GitAdapter, GitError
from docsagent.project.binding import load_binding
from docsagent.project.feature_plans import FEATURE_DOCUMENTS
from docsagent.providers.model_client import ModelClient


class FakeModel:
    available = True
    allow_source_upload = False
    instructions = ""
    api_key = ""

    def __init__(self, handler=None):
        self.handler = handler

    def agent_turn(self, messages, tools, execute, **kwargs):
        self.messages = messages
        self.tools = tools
        if self.handler:
            return self.handler(messages, tools, execute)
        return "ok"


class FakeAgentLoop:
    def run(self, model, messages, tools, execute, **kwargs):
        return model.agent_turn(messages, tools, execute, **kwargs)


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.app = App(load_binding(self.root), Path(__file__).parents[1] / "docsagent" / "web")
        self.app.agent_loop = FakeAgentLoop()

    def test_settings_save_and_connection_test_reject_unknown_fields(self):
        with self.assertRaisesRegex(ValueError, "unsupported agent setting"):
            self.app.configure_agent({"temperatur": 0.4})
        with self.assertRaisesRegex(ValueError, "unsupported agent setting"):
            self.app.test_agent({"temperatur": 0.4})
        with self.assertRaisesRegex(ValueError, "max_tokens must be an integer"):
            self.app.test_agent({"max_tokens": 2.5})

    def test_invalid_persisted_validation_commands_fail_during_startup(self):
        (self.root / ".docsagent.toml").write_text("[agent]\nvalidation_commands = \"python -m unittest\"\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "validation_commands must be an array"):
            App(load_binding(self.root), Path(__file__).parents[1] / "docsagent" / "web")

    @unittest.skipIf(os.name == "nt", "creating symbolic links may require Windows developer mode")
    def test_clearing_api_key_refuses_a_symlinked_credential_directory(self):
        config_home = self.root / "user-config"
        outside = self.root / "outside"
        outside.mkdir()
        project_key = hashlib.sha256(str(self.root.resolve()).encode("utf-8")).hexdigest() + ".key"
        protected_file = outside / project_key
        protected_file.write_text("keep-this-key", encoding="utf-8")
        config_home.mkdir()
        (config_home / "docsagent").symlink_to(outside, target_is_directory=True)

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home), "DOCSAGENT_API_KEY": ""}):
            with self.assertRaisesRegex(PermissionError, "credential directory"):
                self.app.configure_agent({"clear_api_key": True})

        self.assertEqual(protected_file.read_text(encoding="utf-8"), "keep-this-key")

    def test_document_task_is_proposed_then_written_only_after_acceptance(self):
        content = "# Design\n\nA small design document.\n"

        def create_and_finish(messages, tools, execute):
            names = {item["function"]["name"] for item in tools}
            self.assertTrue({"create_document_plan", "document_task_file", "complete_document_task"} <= names)
            context = json.loads(messages[-1]["content"])
            self.assertFalse(context["project_context"]["project_context_enabled"])
            task = execute("create_document_plan", {"goal": "Create a design document", "target_directory": "docs", "files": ["design.md"]})
            execute("document_task_file", {
                "document_task_id": task["document_task_id"],
                "path": "design.md",
                "content": content,
                "summary": "Design overview",
            })
            result = execute("complete_document_task", {
                "document_task_id": task["document_task_id"],
                "summary": "One file is ready for review",
            })
            self.assertFalse(result["written"])
            return "草稿已提交审阅。"

        self.app.model = FakeModel(create_and_finish)
        self.assertEqual(self.app.answer("请帮我写一份设计文档", "session-a"), "草稿已提交审阅。")
        target = self.root / "docs" / "design.md"
        self.assertFalse(target.exists())
        proposals = self.app.pending_proposals("session-a")
        self.assertEqual([item["path"] for item in proposals], ["docs/design.md"])

        proposal = proposals[0]
        preview = self.app.preview_proposal(proposal["proposal_id"], "session-a", content)
        self.assertEqual(preview["errors"], [])
        self.assertTrue(preview["diff"])

        self.app.clear_conversation("session-a")
        self.assertEqual(self.app.conversation("session-a"), [])
        self.assertEqual(len(self.app.pending_proposals("session-a")), 1)
        self.app.accept_proposal(proposal["proposal_id"], "session-a", content)
        self.assertEqual(target.read_text(encoding="utf-8"), content)
        self.assertEqual(self.app.conversation("session-a"), [])

    def test_patch_mode_creates_reviewed_markdown_diff_and_preserves_source(self):
        source = self.root / "src" / "app.py"
        document = self.root / "docs" / "code-reference" / "src" / "app.py.md"
        document.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
        self.app.model.allow_source_upload = True
        original = "# `src/app.py`\n\n## 1. 文件职责\n\nDefines the application entry point.\n"
        document.write_text(original, encoding="utf-8")
        task = self.app.documents._create_document_task({
            "goal": "Translate file responsibilities",
            "mode": "patch",
            "target_directory": ".",
            "files": ["docs/code-reference/src/app.py.md"],
        }, "session-a")
        active = {}
        self.app.documents._activate_document_task(active, "session-a", task["document_task_id"])

        section = self.app.tool_dispatcher.run("read_document_sections", {"document_task_id": task["document_task_id"], "heading": "文件职责"}, "session-a", True, active)
        self.assertIn("Defines the application entry point.", section["sections"][0]["section"])
        result = self.app.tool_dispatcher.run("patch_markdown_documents", {
            "document_task_id": task["document_task_id"],
            "patches": [
                {"path": "docs/code-reference/src/app.py.md", "find": "# `src/app.py`", "replacement": "# `src/app.py`\n\n## 1. 模块变量\n\n| 名称 | 初始值 |\n| --- | --- |\n| [`VALUE`](#symbol-tmp-src-app-py-value) | `1` |", "summary": "补充模块变量"},
                {"path": "docs/code-reference/src/app.py.md", "find": "Defines the application entry point.", "replacement": "负责应用程序入口逻辑。", "summary": "将文件职责改为中文"},
            ],
        }, "session-a", True, active)
        self.assertFalse(result["written"])
        self.app.documents._complete_document_task({"document_task_id": task["document_task_id"], "summary": "将文件职责说明统一为中文"}, "session-a", active)

        proposal = self.app.pending_proposals("session-a")[0]
        self.assertIn("-Defines the application entry point.", proposal["diff"])
        self.assertIn("+负责应用程序入口逻辑。", proposal["diff"])
        self.assertEqual(document.read_text(encoding="utf-8"), original)
        self.assertEqual(source.read_text(encoding="utf-8"), "VALUE = 1\n")

        self.app.accept_proposal(proposal["proposal_id"], "session-a")
        self.assertIn("负责应用程序入口逻辑。", document.read_text(encoding="utf-8"))
        self.assertEqual(source.read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_patch_requires_unique_exact_text_and_unchanged_base(self):
        self.app.model.allow_source_upload = True
        target = self.root / "README.md"
        target.write_text("# Guide\n\n重复词\n重复词\n", encoding="utf-8")
        task = self.app.documents._create_document_task({"goal": "edit", "mode": "patch", "target_directory": ".", "files": ["README.md"]}, "session-a")
        active = {}
        self.app.documents._activate_document_task(active, "session-a", task["document_task_id"])
        with self.assertRaisesRegex(ValueError, "found 2 matches"):
            self.app.documents._propose_document_patches({"document_task_id": task["document_task_id"], "patches": [{"path": "README.md", "find": "重复词", "replacement": "统一"}]}, "session-a", task["document_task_id"], active)

        target.write_text("# Guide\n\nchanged\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "changed after patch planning"):
            self.app.documents._propose_document_patches({"document_task_id": task["document_task_id"], "patches": [{"path": "README.md", "find": "重复词", "replacement": "统一"}]}, "session-a", task["document_task_id"], active)

    def test_patch_plan_rejects_source_code_files(self):
        self.app.model.allow_source_upload = True
        with self.assertRaisesRegex(PermissionError, "existing readable Markdown"):
            self.app.documents._create_document_task({"goal": "edit source", "mode": "patch", "target_directory": ".", "files": ["src/app.py"]}, "session-a")

    def test_selected_project_markdown_is_readable_without_source_sharing(self):
        readme = self.root / "README.md"
        readme.write_text("# Project\n", encoding="utf-8")
        model = FakeModel()

        markdown_context = self.app.context.workspace_context("README.md", model)
        source_context = self.app.context.workspace_context("src/app.py", model)

        self.assertEqual(markdown_context["user_selected_document"]["content"], "# Project\n")
        self.assertNotIn("user_selected_source", source_context)
        self.assertIn("selected_context_error", source_context)

    def test_selected_project_attachment_is_explicit_context(self):
        attachment = self.root / "notes.docx"
        import zipfile
        with zipfile.ZipFile(attachment, "w") as archive:
            archive.writestr("word/document.xml", "<w:document xmlns:w='urn'><w:body><w:p><w:r><w:t>Evidence</w:t></w:r></w:p></w:body></w:document>")
        self.app.model.allow_source_upload = True
        context = self.app.context.workspace_context("notes.docx", self.app.model)
        self.assertEqual(context["user_selected_attachment"]["path"], "notes.docx")
        self.assertIn("Evidence", context["user_selected_attachment"]["text"])

    def test_api_key_is_persisted_per_user_and_can_be_cleared(self):
        config_home = self.root / "user-config"
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home), "DOCSAGENT_API_KEY": ""}):
            self.app.configure_agent({"api_key": "test-secret-key"})
            credential_files = list((config_home / "docsagent").glob("*.key"))
            self.assertEqual(len(credential_files), 1)
            self.assertEqual(stat.S_IMODE(credential_files[0].stat().st_mode), 0o600)
            self.assertNotIn("test-secret-key", (self.root / ".docsagent.toml").read_text(encoding="utf-8"))

            restarted = App(load_binding(self.root), Path(__file__).parents[1] / "docsagent" / "web")
            self.assertEqual(restarted.model.api_key, "test-secret-key")
            self.assertTrue(restarted.agent_settings()["api_key_set"])

            restarted.configure_agent({"clear_api_key": True})
            another_restart = App(load_binding(self.root), Path(__file__).parents[1] / "docsagent" / "web")
            self.assertFalse(another_restart.agent_settings()["api_key_set"])
            self.assertEqual(list((config_home / "docsagent").glob("*.key")), [])

    def test_chat_task_emits_a_terminal_event(self):
        task = self.app.start_chat("当前项目是否配置了模型？", "session-chat")
        with task.condition:
            if not task.done:
                task.condition.wait(timeout=2)

        self.assertTrue(task.done)
        self.assertEqual(task.events[-1]["event"], "done")
        self.assertEqual(task.events[-1]["status"], "completed")
        self.assertTrue(any(item["event"] == "status" for item in task.events))

    def test_model_lock_is_released_while_agent_request_runs(self):
        entered_model = threading.Event()
        release_model = threading.Event()
        lock_acquired = threading.Event()

        def wait_for_release(_messages, _tools, _execute):
            entered_model.set()
            if not release_model.wait(3):
                raise TimeoutError("test did not release the model call")
            return "done"

        self.app.model = FakeModel(wait_for_release)
        answer = []
        request_thread = threading.Thread(target=lambda: answer.append(self.app.answer("hello", "session-a")))
        request_thread.start()

        def probe_model_lock():
            with self.app.model_lock:
                lock_acquired.set()

        try:
            self.assertTrue(entered_model.wait(2))
            probe_thread = threading.Thread(target=probe_model_lock)
            probe_thread.start()
            self.assertTrue(lock_acquired.wait(2))
            probe_thread.join(2)
        finally:
            release_model.set()
            request_thread.join(3)

        self.assertFalse(request_thread.is_alive())
        self.assertEqual(answer, ["done"])

    def test_session_lifecycle_is_isolated_and_default_is_protected(self):
        self.assertEqual([item["id"] for item in self.app.list_sessions()], ["default"])
        first = self.app.create_session("认证设计")
        second = self.app.create_session("同步记录")
        self.app._record_local_turn(first["id"], "问题一", "回答一")
        self.app._record_local_turn(second["id"], "问题二", "回答二")

        sessions = {item["id"]: item for item in self.app.list_sessions()}
        self.assertEqual(sessions[first["id"]]["message_count"], 2)
        self.assertEqual(sessions[second["id"]]["message_count"], 2)
        self.assertEqual(self.app.conversation(first["id"])[0]["content"], "问题一")
        self.assertEqual(self.app.conversation(second["id"])[0]["content"], "问题二")

        renamed = self.app.rename_session(first["id"], "认证方案")
        self.assertEqual(renamed["title"], "认证方案")
        self.app.clear_conversation(first["id"])
        self.assertEqual(self.app.conversation(first["id"]), [])
        self.assertEqual(len(self.app.conversation(second["id"])), 2)

        with self.assertRaisesRegex(ValueError, "默认会话不能删除"):
            self.app.delete_session("default")
        self.app.delete_session(second["id"])
        self.assertNotIn(second["id"], {item["id"] for item in self.app.list_sessions()})

    def test_document_plan_requires_all_declared_files_before_completion(self):
        task = self.app.documents._create_document_task({"goal": "write docs", "target_directory": "docs", "files": ["a.md", "b.md"]}, "session-a")
        active = {}
        self.app.documents._activate_document_task(active, "session-a", task["document_task_id"])
        self.app.documents._proposed_draft({"document_task_id": task["document_task_id"], "path": "a.md", "content": "# A\n", "summary": "A"}, "session-a", task["document_task_id"], active)

        with self.assertRaisesRegex(ValueError, "b.md"):
            self.app.documents._complete_document_task({"document_task_id": task["document_task_id"], "summary": "done"}, "session-a", active)

        with self.assertRaises(PermissionError):
            self.app.documents._proposed_draft({"document_task_id": task["document_task_id"], "path": "other.md", "content": "# Other\n", "summary": "Other"}, "session-a", task["document_task_id"], active)
        self.app.documents._discard_incomplete_document_task(active, "session-a")

    def test_project_tools_can_page_search_results(self):
        (self.root / "src" / "app.py").write_text("needle one\nneedle two\nneedle three\n", encoding="utf-8")
        first = self.app.tool_dispatcher.run("search_project", {"query": "needle", "kind": "source", "limit": 2})
        second = self.app.tool_dispatcher.run("search_project", {"query": "needle", "kind": "source", "limit": 2, "offset": first["next_offset"]})

        self.assertEqual([item["line"] for item in first["items"]], [1, 2])
        self.assertTrue(first["truncated"])
        self.assertEqual([item["line"] for item in second["items"]], [3])
        self.assertFalse(second["truncated"])

    def test_project_file_reads_return_correct_line_pagination(self):
        (self.root / "src" / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
        result = self.app.tool_dispatcher.run("read_project_file", {"path": "src/app.py", "offset": 2, "limit": 1, "kind": "source"})

        self.assertTrue(result["truncated"])
        self.assertEqual(result["next_offset"], 3)
        self.assertIn("2: two", result["content"])
        self.assertEqual(result["sha256"], hashlib.sha256((self.root / "src" / "app.py").read_bytes()).hexdigest())

    def test_repository_planning_tools_include_tests_and_manifests_but_exclude_secrets_and_generated_files(self):
        self.app.model.allow_source_upload = True
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_login.py").write_text("assert True\n", encoding="utf-8")
        (self.root / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
        (self.root / ".docsagent.toml").write_text("[binding]\nsource_root='src'\n", encoding="utf-8")
        (self.root / "poetry.lock").write_text("# lockfile\n", encoding="utf-8")
        (self.root / "Dockerfile").write_text("FROM python:3\n", encoding="utf-8")
        workflow = self.root / ".github" / "workflows"
        workflow.mkdir(parents=True)
        (workflow / "tests.yml").write_text("name: tests\n", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=never-read\n", encoding="utf-8")
        generated = self.root / "node_modules" / "pkg"
        generated.mkdir(parents=True)
        (generated / "package.json").write_text("{}", encoding="utf-8")

        listing = self.app.tool_dispatcher.run("list_repository_files", {"limit": 100}, "session-a", True, {}, True)
        paths = {item["path"] for item in listing["items"]}
        read = self.app.tool_dispatcher.run("read_repository_file", {"path": "tests/test_login.py", "offset": 1, "limit": 20}, "session-a", True, {}, True)
        matches = self.app.tool_dispatcher.run("search_repository", {"query": "assert True"}, "session-a", True, {}, True)

        self.assertIn("tests/test_login.py", paths)
        self.assertIn("pyproject.toml", paths)
        self.assertIn(".docsagent.toml", paths)
        self.assertIn("poetry.lock", paths)
        self.assertIn("Dockerfile", paths)
        self.assertIn(".github/workflows/tests.yml", paths)
        self.assertNotIn(".env", paths)
        self.assertNotIn("node_modules/pkg/package.json", paths)
        self.assertIn("assert True", read["content"])
        self.assertEqual(matches["items"][0]["path"], "tests/test_login.py")

        with self.assertRaisesRegex(PermissionError, "explicit feature-planning"):
            self.app.tool_dispatcher.run("list_repository_files", {}, "session-a", True, {}, False)

    def test_feature_plan_bundle_requires_git_baseline_and_commits_only_confirmed_feature_docs(self):
        self.app.model.allow_source_upload = True

        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True).stdout

        git("init", "-q")
        git("config", "user.email", "docsagent@example.test")
        git("config", "user.name", "DocsAgent Test")
        git("add", "--", ".")
        git("commit", "-m", "initial")
        base_commit = git("rev-parse", "HEAD").strip()
        task = self.app.documents._create_document_task({
            "goal": "规划功能需求和实现架构",
            "mode": "feature",
            "target_directory": "docs/features/add-workflow",
            "files": list(FEATURE_DOCUMENTS),
        }, "session-feature")
        self.assertEqual(task["base_commit"], base_commit)
        active = {}
        self.app.documents._activate_document_task(active, "session-feature", task["document_task_id"])
        contents = {
            "requirements.md": """# 功能需求

## 目标
把验收条件交给外部编码工具。

## 范围
只规划需求和文档。

## 非目标
DocsAgent 不改源码。

## 验收标准
- REQ-001 计划能映射到测试 TEST-001。

## 测试说明
- TEST-001 检查计划包完整性。
""",
            "architecture.md": """# 架构方案

## 当前架构证据
入口见 [src/app.py:1]。

## 约束
保留源码只读。

## 备选方案
方案 A：复用文档提案。方案 B：新增数据库。

## 目标架构
计划采用 Markdown 文档包。

## 取舍与风险
复用提案可保持本地工作流。

## 假设与待确认
暂无。
""",
            "plan.md": f"""# 实施计划

## 基线
Base commit: {base_commit}

## 实施任务
- TASK-001 完成功能包校验，对应 REQ-001。

## 测试计划
- TEST-001 为 TASK-001 编写测试。

## 风险与回退
校验失败时不提交提案。

## 交接
外部工具修改代码并运行测试。
""",
        }
        for filename, content in contents.items():
            self.app.documents._proposed_draft({
                "document_task_id": task["document_task_id"],
                "path": filename,
                "content": content,
                "summary": filename,
            }, "session-feature", task["document_task_id"], active)

        completed = self.app.documents._complete_document_task(
            {"document_task_id": task["document_task_id"], "summary": "三份功能计划文档"},
            "session-feature",
            active,
        )
        self.assertEqual(completed["awaiting_user_review"], 3)
        self.assertFalse((self.root / "docs/features/add-workflow/plan.md").exists())
        proposals = self.app.pending_proposals("session-feature")
        invalid_preview = self.app.preview_proposal(
            proposals[-1]["proposal_id"],
            "session-feature",
            contents["plan.md"].replace("TEST-001", "TEST-999"),
        )
        self.assertTrue(any("TEST-001" in error for error in invalid_preview["errors"]))
        with self.assertRaisesRegex(ValueError, "cross-document validation"):
            self.app.accept_proposal(
                proposals[-1]["proposal_id"],
                "session-feature",
                contents["plan.md"].replace("TEST-001", "TEST-999"),
            )

        for proposal in proposals:
            self.app.accept_proposal(proposal["proposal_id"], "session-feature")

        with self.assertRaisesRegex(FileExistsError, "already exists"):
            self.app.documents._create_document_task({
                "goal": "overwrite a feature package",
                "mode": "feature",
                "feature_action": "create",
                "target_directory": "docs/features/add-workflow",
                "files": list(FEATURE_DOCUMENTS),
            }, "session-feature")

        modify = self.app.documents._create_document_task({
            "goal": "update a feature package after explicit user choice",
            "mode": "feature",
            "feature_action": "modify",
            "target_directory": "docs/features/add-workflow",
            "files": list(FEATURE_DOCUMENTS),
        }, "session-feature")
        self.assertEqual(modify["feature_action"], "modify")
        self.app.documents._discard_incomplete_document_task({"id": modify["document_task_id"]}, "session-feature")

        plan_diff = GitAdapter(self.root).feature_plan_diff("add-workflow")
        self.assertEqual(set(plan_diff["files"]), {f"docs/features/add-workflow/{name}" for name in FEATURE_DOCUMENTS})
        plan_path = self.root / "docs/features/add-workflow/plan.md"
        plan_path.write_text(plan_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(GitError, "changed after review"):
            GitAdapter(self.root).commit_feature_plan(
                "add-workflow",
                "docs(feature): add workflow plan",
                confirm=True,
                expected_diff_digest=plan_diff["digest"],
            )
        plan_diff = GitAdapter(self.root).feature_plan_diff("add-workflow")
        with self.assertRaisesRegex(GitError, "explicit confirmation"):
            GitAdapter(self.root).commit_feature_plan(
                "add-workflow",
                "docs(feature): plan",
                confirm=False,
                expected_diff_digest=plan_diff["digest"],
            )
        GitAdapter(self.root).commit_feature_plan(
            "add-workflow",
            "docs(feature): add workflow plan",
            confirm=True,
            expected_diff_digest=plan_diff["digest"],
        )

        committed = set(git("show", "--format=", "--name-only", "HEAD").splitlines())
        self.assertEqual(committed, {f"docs/features/add-workflow/{name}" for name in FEATURE_DOCUMENTS})
        self.assertEqual(git("rev-parse", "HEAD^").strip(), base_commit)
        committed_plan = GitAdapter(self.root).feature_plan_diff("add-workflow")
        self.assertFalse(committed_plan["baseline_matches_head"])
        self.assertEqual(committed_plan["files"], [])

    def test_feature_plan_rejects_unrelated_dirty_source_changes_and_missing_git(self):
        self.app.model.allow_source_upload = True
        with self.assertRaisesRegex(GitError, "requires Git"):
            self.app.documents._create_document_task({
                "goal": "plan",
                "mode": "feature",
                "target_directory": "docs/features/new-feature",
                "files": list(FEATURE_DOCUMENTS),
            }, "session-a")

        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True).stdout

        git("init", "-q")
        git("config", "user.email", "docsagent@example.test")
        git("config", "user.name", "DocsAgent Test")
        git("add", "--", ".")
        git("commit", "-m", "initial")
        (self.root / "src/app.py").write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(GitError, "outside the current feature documents"):
            self.app.documents._create_document_task({
                "goal": "plan",
                "mode": "feature",
                "target_directory": "docs/features/new-feature",
                "files": list(FEATURE_DOCUMENTS),
            }, "session-a")

    def test_feature_plan_stops_if_head_changes_after_baseline_capture(self):
        self.app.model.allow_source_upload = True

        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True).stdout

        git("init", "-q")
        git("config", "user.email", "docsagent@example.test")
        git("config", "user.name", "DocsAgent Test")
        git("add", "--", ".")
        git("commit", "-m", "initial")
        task = self.app.documents._create_document_task({
            "goal": "plan",
            "mode": "feature",
            "target_directory": "docs/features/add-workflow",
            "files": list(FEATURE_DOCUMENTS),
        }, "session-feature")

        (self.root / "src/app.py").write_text("VALUE = 2\n", encoding="utf-8")
        git("add", "--", "src/app.py")
        git("commit", "-m", "code changed")
        with self.assertRaisesRegex(RuntimeError, "HEAD changed during feature planning"):
            self.app.documents._assert_feature_baseline(self.app.documents.tasks[task["document_task_id"]])

    def test_feature_plan_records_only_a_selected_range_ending_at_current_head(self):
        self.app.model.allow_source_upload = True

        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True).stdout

        git("init", "-q")
        git("config", "user.email", "docsagent@example.test")
        git("config", "user.name", "DocsAgent Test")
        git("add", "-A")
        git("commit", "-m", "baseline")
        base_commit = git("rev-parse", "HEAD").strip()
        (self.root / "README.md").write_text("# Updated project\n", encoding="utf-8")
        git("add", "--", "README.md")
        git("commit", "-m", "implementation")
        head_commit = git("rev-parse", "HEAD").strip()

        task = self.app.documents._create_document_task({
            "goal": "plan from the selected implementation range",
            "mode": "feature",
            "feature_action": "create",
            "target_directory": "docs/features/range-plan",
            "files": list(FEATURE_DOCUMENTS),
            "expected_base_commit": head_commit,
            "change_range": (base_commit, head_commit),
        }, "session-range")
        self.assertEqual(task["change_range"], (base_commit, head_commit))
        self.app.documents._discard_incomplete_document_task({"id": task["document_task_id"]}, "session-range")

        with self.assertRaisesRegex(ValueError, "must end at the current Git HEAD"):
            self.app.documents._create_document_task({
                "goal": "reject a stale range endpoint",
                "mode": "feature",
                "feature_action": "create",
                "target_directory": "docs/features/stale-range",
                "files": list(FEATURE_DOCUMENTS),
                "expected_base_commit": head_commit,
                "change_range": (base_commit, base_commit),
            }, "session-range")

    def test_feature_plan_request_intent_is_explicit_and_negations_are_rejected(self):
        self.assertTrue(App.feature_plan_request_allowed("请为新增功能制定实现计划"))
        self.assertTrue(App.feature_plan_request_allowed("Create a feature plan for login"))
        self.assertFalse(App.feature_plan_request_allowed("如何制定功能计划？"))
        self.assertFalse(App.feature_plan_request_allowed("不要生成需求计划"))

    def test_binding_preview_and_rebind_require_confirmation_to_discard_session_state(self):
        (self.root / "src-next").mkdir()
        (self.root / "src-next" / "entry.py").write_text("VALUE = 2\n", encoding="utf-8")
        written = self.root / "docs" / "manual.md"
        written.parent.mkdir(parents=True)
        written.write_text("# Kept on disk\n", encoding="utf-8")
        self.app.session_store.messages["default"] = [{"role": "user", "content": "unsaved session"}]

        preview = self.app.preview_binding("src-next", "docs-next")

        self.assertEqual(preview["tree"]["binding"], {"source_root": "src-next", "docs_root": "docs-next"})
        self.assertEqual([item["path"] for item in preview["tree"]["sources"]], ["src-next/entry.py"])
        self.assertTrue(preview["pending"]["has_pending"])
        with self.assertRaisesRegex(RuntimeError, "explicit confirmation"):
            self.app.rebind("src-next", "docs-next")

        self.app.rebind("src-next", "docs-next", discard_pending=True)

        self.assertEqual(self.app.tree()["binding"], {"source_root": "src-next", "docs_root": "docs-next"})
        self.assertEqual(self.app.conversation("default"), [])
        self.assertEqual(written.read_text(encoding="utf-8"), "# Kept on disk\n")

    def test_git_markdown_commit_preserves_other_staged_files(self):
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True).stdout

        (self.root / "docs").mkdir()
        (self.root / "docs" / "code-reference").mkdir()
        (self.root / "docs" / "code-reference" / "guide.md").write_text("# Guide\n\nBefore.\n", encoding="utf-8")
        (self.root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        git("init", "-q")
        git("config", "user.email", "docsagent@example.test")
        git("config", "user.name", "DocsAgent Test")
        git("add", "--", ".")
        git("commit", "-m", "initial")
        (self.root / "docs" / "code-reference" / "guide.md").write_text("# Guide\n\nAfter.\n", encoding="utf-8")
        (self.root / "docs" / "code-reference" / "new.md").write_text("# New\n", encoding="utf-8")
        (self.root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        git("add", "--", "src/app.py")

        adapter = GitAdapter(self.root)
        paths = [entry["path"] for entry in adapter.markdown_status()["entries"]]
        diff, truncated = adapter.diff_markdown(paths)
        self.assertFalse(truncated)
        digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        adapter.commit_markdown(paths, "docs: update", expected_diff_digest=digest, confirm=True)

        committed = set(git("show", "--format=", "--name-only", "HEAD").splitlines())
        staged = set(git("diff", "--cached", "--name-only").splitlines())
        self.assertEqual(committed, {"docs/code-reference/guide.md", "docs/code-reference/new.md"})
        self.assertEqual(staged, {"src/app.py"})

    def test_git_log_shows_no_repository_error_to_the_user(self):
        answer = self.app.answer("查看 git 历史", "session-a")

        self.assertIn("尚未初始化 Git", answer)
        self.assertIn("git init", answer)

    def test_git_diff_history_and_status_include_only_mirrored_markdown(self):
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True).stdout

        (self.root / "docs" / "code-reference").mkdir(parents=True)
        document = self.root / "docs" / "code-reference" / "app.py.md"
        document.write_text("# app.py\n\nBefore.\n", encoding="utf-8")
        (self.root / "docs" / "code-reference" / ".hidden.md").write_text("# Hidden\n", encoding="utf-8")
        (self.root / "docs" / "code-reference" / "secret.env.md").write_text("# Secret\n", encoding="utf-8")
        git("init", "-q")
        git("config", "user.email", "docsagent@example.test")
        git("config", "user.name", "DocsAgent Test")
        git("add", "--", ".")
        git("commit", "-m", "initial")
        document.write_text("# app.py\n\nAfter.\n", encoding="utf-8")
        (self.root / "README.md").write_text("# project\n", encoding="utf-8")
        git("add", "--", "README.md")

        adapter = GitAdapter(self.root)
        status = adapter.document_status("docs/code-reference")
        diff, _ = adapter.diff_documents("docs/code-reference")
        history = adapter.document_history("docs/code-reference")

        self.assertEqual([item["path"] for item in status["entries"]], ["docs/code-reference/app.py.md"])
        self.assertIn("app.py.md", diff)
        self.assertNotIn(".hidden.md", diff)
        self.assertNotIn("secret.env.md", diff)
        self.assertEqual([item["subject"] for item in history], ["initial"])

    def test_manual_markdown_is_in_document_tree(self):
        (self.root / "README.md").write_text("# Project\n", encoding="utf-8")
        documents = {item["path"]: item["status"] for item in self.app.tree()["documents"]}
        self.assertEqual(documents["README.md"], "manual")

    def test_document_tool_is_not_enabled_for_questions_or_negated_requests(self):
        self.assertTrue(App.document_request_allowed("请帮我写一份设计文档"))
        self.assertTrue(App.document_request_allowed("你把所有文档的文件职责的地方全部改成中文，一个文件不要描述语句中英混用"))
        self.assertTrue(App.document_request_allowed("把所有文件职责改成中文"))
        self.assertFalse(App.document_request_allowed("如何写一份设计文档？"))
        self.assertFalse(App.document_request_allowed("不要写设计文档"))

    def test_responses_strict_schema_converts_optional_nested_fields(self):
        schema = {"type": "object", "properties": {"patches": {"type": "array", "items": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": [], "additionalProperties": False}}}, "required": ["patches"], "additionalProperties": False}
        converted = ModelClient._strict_schema(schema)
        item = converted["properties"]["patches"]["items"]
        self.assertEqual(item["required"], ["summary"])
        self.assertEqual(item["properties"]["summary"]["type"], ["string", "null"])

    def test_patch_intent_does_not_require_model_source_access_for_other_documents(self):
        self.assertFalse(self.app.model.allow_source_upload)
        with self.assertRaisesRegex(PermissionError, "project-content access"):
            self.app.documents._create_document_task({"goal": "edit", "mode": "patch", "target_directory": ".", "files": ["README.md"]}, "session-a")

    def test_patch_section_reader_matches_numbered_heading_and_batches_files(self):
        self.app.model.allow_source_upload = True
        (self.root / "docs").mkdir(exist_ok=True)
        first = self.root / "README.md"
        second = self.root / "docs" / "guide.md"
        first.write_text("# Guide\n\n## 1. 文件职责\n\nEnglish one.\n\n## 2. Details\n\nOther.\n", encoding="utf-8")
        second.write_text("# Guide\n\n## 文件职责\n\nEnglish two.\n", encoding="utf-8")
        task = self.app.documents._create_document_task({"goal": "translate", "mode": "patch", "target_directory": ".", "files": ["README.md", "docs/guide.md"]}, "session-a")
        active = {}
        self.app.documents._activate_document_task(active, "session-a", task["document_task_id"])
        result = self.app.documents._read_document_sections({"document_task_id": task["document_task_id"], "heading": "文件职责"}, "session-a", task["document_task_id"], active)
        self.assertEqual(result["found"], 2)
        self.assertTrue(all(item["found"] for item in result["sections"]))

    def test_patch_mode_requires_every_planned_document_to_receive_a_patch(self):
        self.app.model.allow_source_upload = True
        first = self.root / "README.md"
        second = self.root / "DESIGN.md"
        first.write_text("# Readme\n\nEnglish.\n", encoding="utf-8")
        second.write_text("# Design\n\nEnglish.\n", encoding="utf-8")
        task = self.app.documents._create_document_task({"goal": "translate", "mode": "patch", "target_directory": ".", "files": ["README.md", "DESIGN.md"]}, "session-a")
        active = {}
        self.app.documents._activate_document_task(active, "session-a", task["document_task_id"])
        self.app.documents._propose_document_patches({"document_task_id": task["document_task_id"], "patches": [{"path": "README.md", "find": "English.", "replacement": "中文。", "summary": "翻译"}]}, "session-a", task["document_task_id"], active)
        with self.assertRaisesRegex(ValueError, "DESIGN.md"):
            self.app.documents._complete_document_task({"document_task_id": task["document_task_id"], "summary": "翻译"}, "session-a", active)


if __name__ == "__main__":
    unittest.main()
