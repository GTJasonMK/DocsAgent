from __future__ import annotations

import unittest
from unittest.mock import patch

from docsagent.agent.browser_access import BrowserSession


class BrowserAccessTests(unittest.TestCase):
    def test_browser_rejects_private_urls_before_starting(self):
        browser = BrowserSession()
        with self.assertRaises(PermissionError):
            browser.read_page("http://127.0.0.1:8000/")
        self.assertIsNone(browser._context)

    def test_browser_enforces_content_limit(self):
        browser = BrowserSession()
        with self.assertRaises(ValueError):
            browser.read_page("https://example.com", max_chars=0)


if __name__ == "__main__":
    unittest.main()
