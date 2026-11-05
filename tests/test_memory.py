from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docsagent.agent.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_memory_is_explicitly_added_and_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = MemoryStore(Path(temporary))
            item = first.add("prefer concise plans", "user confirmation")
            second = MemoryStore(Path(temporary))
            self.assertEqual(second.list()[0]["text"], "prefer concise plans")
            second.delete(item["id"])
            self.assertEqual(second.list(), [])


if __name__ == "__main__":
    unittest.main()
