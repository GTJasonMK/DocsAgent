from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docsagent.project.feature_plans import (
    FEATURE_DOCUMENTS,
    feature_document_paths,
    feature_id_from_directory,
    validate_feature_bundle,
)
from docsagent.infrastructure.git import GitAdapter, GitBaselineError, GitError


def valid_bundle(base_commit: str) -> dict[str, str]:
    return {
        "requirements.md": f"""# 功能需求

## 目标
让计划包中的验收条件可以交给外部编码工具执行。

## 范围
只生成需求、架构和实施计划文档。

## 非目标
DocsAgent 不修改源码，也不执行测试。

## 验收标准
- REQ-001 功能计划必须引用项目事实，测试见 TEST-001。

## 测试说明
- TEST-001 检查功能包完整性和引用关系。
""",
        "architecture.md": """# 架构方案

## 当前架构证据
源码入口见 [src/app.py:1]。

## 约束
Agent 只能提出文档草稿。

## 备选方案
方案 A：复用现有文档提案。
方案 B：引入独立任务数据库。

## 目标架构
使用 Markdown 功能包保存计划。

## 取舍与风险
复用现有提案缩小改动范围。

## 假设与待确认
暂无。
""",
        "plan.md": f"""# 实施计划

## 基线
Base commit: {base_commit}

## 实施任务
- TASK-001 实现功能包校验，对应 REQ-001。

## 测试计划
- TEST-001 为 TASK-001 增加单元测试。

## 风险与回退
校验失败时不创建提案。

## 交接
外部编码工具依据本计划修改代码并运行测试。
""",
    }


class FeaturePlanValidationTests(unittest.TestCase):
    def test_accepts_complete_traceable_bundle_with_existing_architecture_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src" / "app.py"
            source.parent.mkdir()
            source.write_text("def app():\n    return True\n", encoding="utf-8")

            errors = validate_feature_bundle(valid_bundle("a" * 40), "add-workflow", "a" * 40, root)

        self.assertEqual(errors, [])

    def test_requires_test_and_requirement_traceability_and_valid_evidence(self):
        bundle = valid_bundle("b" * 40)
        bundle["plan.md"] = bundle["plan.md"].replace("REQ-001", "REQ-999").replace("TEST-001", "TEST-999")
        bundle["architecture.md"] = bundle["architecture.md"].replace("src/app.py:1", "missing.py:2")

        errors = validate_feature_bundle(bundle, "add-workflow", "b" * 40)

        self.assertTrue(any("REQ-001" in item for item in errors))
        self.assertTrue(any("TEST-001" in item for item in errors))
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "src" / "app.py"
            source.parent.mkdir()
            source.write_text("pass\n", encoding="utf-8")
            errors = validate_feature_bundle(bundle, "add-workflow", "b" * 40, Path(temporary))
        self.assertTrue(any("evidence file does not exist" in item for item in errors))

    def test_rejects_invalid_ids_paths_and_base_commit(self):
        with self.assertRaises(ValueError):
            feature_id_from_directory("docs/features/../escape")
        with self.assertRaises(ValueError):
            feature_document_paths("../escape")
        with self.assertRaises(ValueError):
            validate_feature_bundle(valid_bundle("bad"), "valid-id", "bad")

    def test_requires_exact_three_documents(self):
        bundle = valid_bundle("c" * 40)
        bundle.pop(FEATURE_DOCUMENTS[-1])
        errors = validate_feature_bundle(bundle, "feature", "c" * 40)
        self.assertTrue(any("missing: plan.md" in item for item in errors))

    def test_architecture_requires_a_verifiable_repository_citation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src" / "app.py"
            source.parent.mkdir()
            source.write_text("pass\n", encoding="utf-8")
            bundle = valid_bundle("d" * 40)
            bundle["architecture.md"] = bundle["architecture.md"].replace("[src/app.py:1]", "the application module")

            errors = validate_feature_bundle(bundle, "feature", "d" * 40, root)

        self.assertTrue(any("repository citation" in item for item in errors))

    def test_feature_directory_symlink_is_rejected_for_git_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            features = root / "docs" / "features"
            (features / "real-package").mkdir(parents=True)
            (features / "linked-package").symlink_to(features / "real-package", target_is_directory=True)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=root, check=True, capture_output=True)

            with self.assertRaisesRegex(GitError, "cannot be a symlink"):
                GitAdapter(root).feature_plan_diff("linked-package")


class GitWorkflowTests(unittest.TestCase):
    def initialize_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "DocsAgent Test"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "docsagent@example.test"], cwd=root, check=True, capture_output=True)

    def git(self, root: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True).stdout

    def test_unborn_repository_reports_staged_markdown_and_empty_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "docs" / "guide.md"
            document.parent.mkdir()
            document.write_text("# Guide\n", encoding="utf-8")
            self.initialize_repo(root)
            self.git(root, "add", "--", "docs/guide.md")

            adapter = GitAdapter(root)
            status = adapter.markdown_status()
            diff, truncated = adapter.diff_markdown(["docs/guide.md"])
            history = adapter.project_history()

            with self.assertRaises(GitBaselineError):
                adapter.head_commit()
            self.assertEqual(status["head_commit"], "")
            self.assertEqual([item["path"] for item in status["entries"]], ["docs/guide.md"])
            self.assertFalse(truncated)
            self.assertIn("+# Guide", diff)
            self.assertEqual(history, {"head_commit": "", "items": []})

    def test_git_head_command_errors_are_not_reported_as_empty_history_or_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            self.initialize_repo(root)
            self.git(root, "add", "-A")
            self.git(root, "commit", "-m", "baseline")
            adapter = GitAdapter(root)
            run_git = subprocess.run

            def fail_head(command, *args, **kwargs):
                if command[:4] == ["git", "rev-parse", "--verify", "--quiet"]:
                    return subprocess.CompletedProcess(command, 128, "", "fatal: injected HEAD lookup failure")
                return run_git(command, *args, **kwargs)

            with patch("docsagent.infrastructure.git.subprocess.run", side_effect=fail_head):
                with self.assertRaisesRegex(GitError, "injected HEAD lookup failure"):
                    adapter.project_history()
                with self.assertRaisesRegex(GitError, "injected HEAD lookup failure"):
                    adapter.markdown_status()

    def test_commit_range_is_ancestor_to_head_and_diff_excludes_hidden_and_binary_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "README.md").write_text("# Initial\n", encoding="utf-8")
            (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.initialize_repo(root)
            self.git(root, "add", "-A")
            self.git(root, "commit", "-m", "baseline")
            base = self.git(root, "rev-parse", "HEAD").strip()

            (root / "README.md").write_text("# Updated\n", encoding="utf-8")
            (root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=private\n", encoding="utf-8")
            (root / "image.bin").write_bytes(b"\x00\xff")
            self.git(root, "add", "-A")
            self.git(root, "commit", "-m", "implementation")
            head = self.git(root, "rev-parse", "HEAD").strip()

            result = GitAdapter(root).compare_to_head(base)

            self.assertEqual(result["from_commit"], base)
            self.assertEqual(result["to_commit"], head)
            self.assertEqual(set(result["files"]), {"README.md", "src/app.py"})
            self.assertNotIn("private", result["diff"])
            with self.assertRaisesRegex(ValueError, "different commits"):
                GitAdapter(root).validate_commit_range(head, head)
            with self.assertRaisesRegex(ValueError, "ancestor"):
                GitAdapter(root).validate_commit_range(head, base)

    def test_markdown_commit_requires_reviewed_unchanged_diff_and_only_commits_selected_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "src").mkdir()
            (root / "docs" / "guide.md").write_text("# Guide\n\nBefore.\n", encoding="utf-8")
            (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.initialize_repo(root)
            self.git(root, "add", "-A")
            self.git(root, "commit", "-m", "baseline")
            document = root / "docs" / "guide.md"
            document.write_text("# Guide\n\nReviewed version.\n", encoding="utf-8")
            adapter = GitAdapter(root)
            diff, _ = adapter.diff_markdown(["docs/guide.md"])
            digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()

            with self.assertRaisesRegex(ValueError, "reviewed Markdown Diff digest"):
                adapter.commit_markdown(["docs/guide.md"], "docs: update", confirm=True)
            with self.assertRaisesRegex(GitError, "changed after review"):
                adapter.commit_markdown(["docs/guide.md"], "docs: update", confirm=True, expected_diff_digest="0" * 64)

            document.write_text("# Guide\n\nChanged after review.\n", encoding="utf-8")
            with self.assertRaisesRegex(GitError, "changed after review"):
                adapter.commit_markdown(["docs/guide.md"], "docs: update", confirm=True, expected_diff_digest=digest)

            document.write_text("# Guide\n\nReviewed version.\n", encoding="utf-8")
            new_document = root / "docs" / "new.md"
            new_document.write_text("# New\n\nNew content.\n", encoding="utf-8")
            selected = ["docs/guide.md", "docs/new.md"]
            diff, _ = adapter.diff_markdown(selected)
            digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
            (root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
            self.git(root, "add", "--", "src/app.py")
            adapter.commit_markdown(selected, "docs: update", confirm=True, expected_diff_digest=digest)

            committed = set(self.git(root, "show", "--format=", "--name-only", "HEAD").splitlines())
            staged = set(self.git(root, "diff", "--cached", "--name-only").splitlines())
            self.assertEqual(committed, {"docs/guide.md", "docs/new.md"})
            self.assertEqual(staged, {"src/app.py"})

    def test_markdown_commit_rejects_a_truncated_review_diff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            document = root / "docs" / "large.md"
            document.write_text("# Large\n", encoding="utf-8")
            self.initialize_repo(root)
            self.git(root, "add", "-A")
            self.git(root, "commit", "-m", "baseline")
            document.write_text("# Large\n" + ("x" * 60000) + "\n", encoding="utf-8")
            diff, truncated = GitAdapter(root).diff_markdown(["docs/large.md"])
            self.assertTrue(truncated)
            digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()

            with self.assertRaisesRegex(GitError, "Diff is truncated"):
                GitAdapter(root).commit_markdown(["docs/large.md"], "docs: update", confirm=True, expected_diff_digest=digest)
