from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docsagent.agent.persistence import RunStore


class RunStoreTests(unittest.TestCase):
    def test_events_and_run_index_survive_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RunStore(root)
            store.append("run-1", "session-1", {"event": "status", "phase": "starting"})
            store.append("run-1", "session-1", {"event": "done", "status": "completed"})
            restarted = RunStore(root)
            self.assertEqual(len(restarted.events("run-1")), 2)
            self.assertEqual(restarted.list_runs()[0]["run_id"], "run-1")
            restarted.delete("run-1")
            self.assertEqual(restarted.list_runs(), [])


if __name__ == "__main__":
    unittest.main()
