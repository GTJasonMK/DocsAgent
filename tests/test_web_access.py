from __future__ import annotations

import unittest
from unittest.mock import patch

from docsagent.agent.web_access import _PageText, _SearchResults, _validate_public_url


class WebAccessTests(unittest.TestCase):
    def test_private_and_credentialed_urls_are_rejected(self):
        with self.assertRaises(PermissionError):
            _validate_public_url("http://127.0.0.1/admin")
        with self.assertRaises(ValueError):
            _validate_public_url("https://user:password@example.com/")

    @patch("docsagent.agent.web_access.socket.getaddrinfo")
    def test_hostnames_resolving_to_private_addresses_are_rejected(self, resolve):
        resolve.return_value = [(None, None, None, None, ("10.0.0.2", 443))]
        with self.assertRaises(PermissionError):
            _validate_public_url("https://example.test/")

    def test_page_parser_skips_script_and_style_content(self):
        parser = _PageText()
        parser.feed("<title>Guide</title><h1>Title</h1><script>secret()</script><p>Useful text</p>")
        self.assertEqual(" ".join(parser.title_parts), "Guide")
        self.assertIn("Useful text", " ".join(parser.parts))
        self.assertNotIn("secret", " ".join(parser.parts))

    def test_search_parser_extracts_result_links_and_snippets(self):
        parser = _SearchResults()
        parser.feed('<div class="result"><a class="result__a" href="https://example.com">Example</a><a class="result__snippet">A result</a></div>')
        self.assertEqual(parser.results, [{"title": "Example", "url": "https://example.com", "snippet": "A result"}])


if __name__ == "__main__":
    unittest.main()
