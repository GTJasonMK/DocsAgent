from __future__ import annotations

import unittest
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from docsagent.agent.mcp_client import MCPClient, MCPServerConfig


class MCPClientTests(unittest.TestCase):
    def test_server_config_requires_safe_transport_and_scope(self):
        with self.assertRaises(ValueError):
            MCPClient(MCPServerConfig("bad name", "stdio", ("server",), enabled=True))
        with self.assertRaises(ValueError):
            MCPClient(MCPServerConfig("server", "streamable_http", url="file:///tmp/x", enabled=True))
        client = MCPClient(MCPServerConfig("docs", "stdio", ("server",), enabled=False))
        self.assertEqual(client.namespace, "mcp.docs")
        with self.assertRaises(PermissionError):
            client.request("tools/list")

    def test_http_request_translates_json_rpc_errors(self):
        client = MCPClient(MCPServerConfig("docs", "streamable_http", url="https://example.test/mcp", enabled=True))
        class Response:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return self.body
        def response_for(request, timeout):
            if not request.data:
                return Response(b"")
            payload = json.loads(request.data)
            body = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {"tools": []}}).encode()
            return Response(body)
        with patch("docsagent.agent.mcp_client.urllib.request.urlopen", side_effect=response_for):
            self.assertEqual(client.request("tools/list"), {"tools": []})

    def test_http_sse_response_extracts_json_rpc_message(self):
        messages = MCPClient._decode_http_messages(
            b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{}}\n\n",
            "text/event-stream",
        )
        self.assertEqual(messages, [{"jsonrpc": "2.0", "id": 2, "result": {}}])

    def test_stdio_ignores_notifications_and_sends_initialized_notification(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "server.py"
            script.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    if request.get('method') == 'initialize':\n"
                "        print(json.dumps({'jsonrpc':'2.0','method':'notifications/loggingMessage','params':{}}), flush=True)\n"
                "        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{}}), flush=True)\n"
                "    elif request.get('method') in ('tools/list', 'resources/list', 'prompts/list'):\n"
                "        key = request['method'].split('/')[0]\n"
                "        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{key:[]}}), flush=True)\n",
                encoding="utf-8",
            )
            client = MCPClient(MCPServerConfig("docs", "stdio", (sys.executable, str(script)), enabled=True))
            try:
                self.assertEqual(client.list_capabilities()["tools"], [])
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
