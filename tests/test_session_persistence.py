from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docsagent.agent.sessions import SessionStore


class SessionPersistenceTests(unittest.TestCase):
    def test_sessions_and_messages_survive_store_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = SessionStore(root)
            session = first.create_session("persisted")
            first.record_local_turn(session["id"], "question", "answer")

            second = SessionStore(root)
            sessions = {item["id"]: item for item in second.list_sessions({}, set())}
            self.assertEqual(sessions[session["id"]]["title"], "persisted")
            self.assertEqual(second.conversation(session["id"])[1]["content"], "answer")

            second.delete_session(session["id"])
            third = SessionStore(root)
            self.assertNotIn(session["id"], {item["id"] for item in third.list_sessions({}, set())})

    def test_corrupt_persisted_state_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = SessionStore(root)
            first.storage_path.write_bytes(b"not a sqlite database")
            with self.assertRaisesRegex(Exception, "database"):
                SessionStore(root)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not available")
    def test_persisted_session_database_is_private(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"XDG_DATA_HOME": temporary}):
            store = SessionStore(Path(temporary) / "project")
            self.assertEqual(stat.S_IMODE(store.storage_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(store.storage_path.parent.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
