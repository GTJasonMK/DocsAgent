from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from docsagent.cli import _print_draft_warnings
from docsagent.project.models import Draft


class CliTests(unittest.TestCase):
    def test_draft_fallback_warnings_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            draft = Draft(
                source=root / "src" / "main.py",
                document=root / "docs" / "main.py.md",
                content="# main.py\n",
                facts=None,
                warnings=["模型摘要失败，已使用静态解析：connection refused"],
            )
            output = io.StringIO()

            with redirect_stdout(output):
                _print_draft_warnings(root, [draft])

        self.assertIn("docs/main.py.md", output.getvalue())
        self.assertIn("模型摘要失败，已使用静态解析", output.getvalue())


if __name__ == "__main__":
    unittest.main()
