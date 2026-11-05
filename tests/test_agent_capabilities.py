from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from docsagent.agent.command_runner import run_project_command
from docsagent.project.changes import edit_project_file


class CapabilityTests(unittest.TestCase):
    def test_project_edit_returns_diff_and_refuses_stale_or_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src" / "app.py"
            source.parent.mkdir()
            source.write_text("VALUE = 1\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            result = edit_project_file(root, "src/app.py", "VALUE = 2\n", digest)

            self.assertIn("-VALUE = 1", result["diff"])
            self.assertIn("+VALUE = 2", result["diff"])
            self.assertEqual(source.read_text(encoding="utf-8"), "VALUE = 2\n")
            with self.assertRaisesRegex(RuntimeError, "changed since it was read"):
                edit_project_file(root, "src/app.py", "VALUE = 3\n", digest)
            with self.assertRaises(PermissionError):
                edit_project_file(root, ".env", "TOKEN=x", None)

    def test_project_edit_creates_only_missing_file_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = edit_project_file(root, "tests/new_test.py", "assert True\n", None)
            self.assertTrue(result["changed"])
            self.assertEqual(result["base_sha256"], None)
            with self.assertRaisesRegex(ValueError, "expected_sha256"):
                edit_project_file(root, "tests/new_test.py", "assert False\n", None)

    def test_project_edit_preserves_raw_newline_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "src" / "lines.py"
            target.parent.mkdir()
            target.write_bytes(b"one\r\ntwo\r\n")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            result = edit_project_file(root, "src/lines.py", "one\r\ntwo\r\nthree\r\n", digest)
            self.assertTrue(result["changed"])

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not available")
    def test_project_edit_preserves_existing_file_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "scripts" / "run.sh"
            target.parent.mkdir()
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(0o755)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            edit_project_file(root, "scripts/run.sh", "#!/bin/sh\nexit 1\n", digest)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_command_runner_requires_exact_allowlist_and_never_uses_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = ["python", "-c", "print('ok')"]
            with self.assertRaises(PermissionError):
                run_project_command(root, command, [])

            result = run_project_command(root, command, [command])
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["stdout"], "ok\n")
            self.assertEqual(result["argv"], command)

    def test_command_runner_caps_timeout_and_rejects_invalid_argv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                run_project_command(root, [], [])
            with self.assertRaises(ValueError):
                run_project_command(root, ["python"], [["python"]], timeout=301)

    def test_command_runner_bounds_large_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = ["python", "-c", "print('x' * 50000)"]
            result = run_project_command(root, command, [command])
            self.assertTrue(result["truncated"])
            self.assertLessEqual(len(result["stdout"]), 20000)

    def test_command_runner_kills_process_when_task_is_cancelled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = ["python", "-c", "import time; time.sleep(10)"]
            checks = 0

            def cancel_after_start():
                nonlocal checks
                checks += 1
                if checks > 1:
                    raise RuntimeError("cancelled")

            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                run_project_command(root, command, [command], cancel_check=cancel_after_start)


if __name__ == "__main__":
    unittest.main()
