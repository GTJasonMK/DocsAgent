from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docsagent.agent.mcp_client import MCPError, MCPManager


class MCPManagerTests(unittest.TestCase):
    def test_server_configuration_is_persisted_and_removed(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary}):
            manager = MCPManager(Path(temporary) / "project")
            manager.configure("docs", "stdio", ["python", "server.py"], enabled=True)
            restarted = MCPManager(Path(temporary) / "project")
            self.assertEqual(restarted.list()[0]["name"], "docs")
            self.assertTrue(restarted.list()[0]["enabled"])
            restarted.remove("docs")
            self.assertEqual(restarted.list(), [])

    def test_dynamic_tool_names_are_namespaced(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary}):
            manager = MCPManager(Path(temporary) / "project")
            manager.configure("docs", "streamable_http", url="https://example.test/mcp", enabled=True)
            fake = type("FakeClient", (), {"list_capabilities": lambda self: {"tools": [{"name": "lookup", "description": "Lookup", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}, "call_tool": lambda self, name, arguments: {"content": []}})()
            manager.clients["docs"] = fake
            tools = manager.model_tools()
            self.assertEqual(tools[0]["function"]["name"], "mcp.docs.lookup")

    def test_replace_validates_before_persisting_any_changes(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary}):
            manager = MCPManager(Path(temporary) / "project")
            manager.configure("first", "stdio", ["server"], enabled=True)
            original = manager.list()
            with self.assertRaises(ValueError):
                manager.replace([
                    {"name": "second", "transport": "stdio", "command": ["server"], "enabled": True},
                    {"name": "second", "transport": "stdio", "command": ["other"], "enabled": True},
                ])
            self.assertEqual(manager.list(), original)

    def test_enabled_server_discovery_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary}):
            manager = MCPManager(Path(temporary) / "project")
            manager.configure("docs", "streamable_http", url="https://example.test/mcp", enabled=True)
            manager.clients["docs"] = type("FailedClient", (), {"list_capabilities": lambda self: (_ for _ in ()).throw(MCPError("offline"))})()
            with self.assertRaisesRegex(MCPError, "docs: offline"):
                manager.model_tools()


if __name__ == "__main__":
    unittest.main()
