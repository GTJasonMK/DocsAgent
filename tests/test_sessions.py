from __future__ import annotations

import unittest

from docsagent.agent.sessions import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_zero_context_limit_returns_no_messages(self):
        sessions = SessionStore()
        sessions.append("default", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ])

        messages, _ = sessions.request_context("default", 0)

        self.assertEqual(messages, [])

    def test_negative_context_limit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            SessionStore().request_context("default", -1)


if __name__ == "__main__":
    unittest.main()
