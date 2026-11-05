from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from docsagent.agent.application import App
from docsagent.project.binding import load_binding
from docsagent.web.server import DocsServer


class WebAssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        self.web_root = self.root / "web"
        (self.web_root / "assets").mkdir(parents=True)
        (self.web_root / "index.html").write_text("<main>Vue workspace</main>", encoding="utf-8")
        (self.web_root / "assets" / "app.js").write_text("console.log('workspace')", encoding="utf-8")
        app = App(load_binding(self.root), self.web_root)
        self.server = DocsServer(("127.0.0.1", 0), app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)
        self.connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        self.addCleanup(self.connection.close)

    def test_serves_index_and_assets_but_not_legacy_or_outside_paths(self):
        self.connection.request("GET", "/")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b"Vue workspace", response.read())

        self.connection.request("GET", "/assets/app.js")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/javascript")
        self.assertIn(b"workspace", response.read())

        self.connection.request("GET", "/app.js")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 404)
        response.read()

        self.connection.request("GET", "/assets/../../../../etc/passwd")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 400)
        response.read()

    def test_feature_plan_git_routes_require_a_valid_id_and_explicit_confirmation(self):
        self.connection.request("GET", "/api/git/feature-plan/diff?feature_id=../outside")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn(b"feature id", response.read())

        body = b'{"feature_id":"add-workflow","message":"docs(feature): plan"}'
        self.connection.request(
            "POST",
            "/api/git/feature-plan/commit",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = self.connection.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn(b"explicit confirmation", response.read())

    def test_markdown_commit_route_requires_the_reviewed_diff_digest(self):
        body = b'{"confirm":true,"message":"docs: update","paths":["docs/guide.md"]}'
        self.connection.request(
            "POST",
            "/api/git/commit",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = self.connection.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn(b"reviewed Markdown Diff digest is required", response.read())

    def test_feature_plan_commit_passes_the_review_digest_to_git(self):
        calls = []

        class AdapterStub:
            def __init__(self, root):
                self.root = root

            def commit_feature_plan(self, feature_id, message, confirm=False, expected_diff_digest=None):
                calls.append((feature_id, message, confirm, expected_diff_digest))
                return "committed"

        body = json.dumps({
            "confirm": True,
            "feature_id": "add-workflow",
            "message": "docs(feature): plan",
            "expected_diff_digest": "a" * 64,
        }).encode()
        with patch("docsagent.web.server.GitAdapter", AdapterStub):
            self.connection.request(
                "POST",
                "/api/git/feature-plan/commit",
                body=body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
            )
            response = self.connection.getresponse()
            payload = json.loads(response.read())

        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"result": "committed", "feature_id": "add-workflow"})
        self.assertEqual(calls, [("add-workflow", "docs(feature): plan", True, "a" * 64)])

    def test_proposal_route_returns_a_flat_proposal_list(self):
        proposals = [{"proposal_id": "proposal-1"}]
        self.server.app.pending_proposals = lambda _session_id: proposals

        self.connection.request("GET", "/api/chat/proposals?all_sessions=true")
        response = self.connection.getresponse()

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), {"proposals": proposals})

    def test_chat_clear_route_clears_the_requested_session(self):
        cleared = []
        self.server.app.clear_conversation = lambda session_id: cleared.append(session_id)
        body = b'{"session_id":"session-a"}'
        self.connection.request(
            "POST",
            "/api/chat/clear",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = self.connection.getresponse()

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), {"cleared": True})
        self.assertEqual(cleared, ["session-a"])

    def test_persisted_task_events_can_be_read_and_deleted_explicitly(self):
        self.server.app.run_store.append("run-1", "session-1", {"id": 1, "event": "status", "phase": "starting"})
        self.connection.request("GET", "/api/chat/runs?id=run-1")
        response = self.connection.getresponse()
        self.assertEqual(json.loads(response.read()), {"run_id": "run-1", "events": [{"id": 1, "event": "status", "phase": "starting"}]})

        body = json.dumps({"confirm": True, "run_id": "run-1"}).encode()
        self.connection.request("POST", "/api/chat/runs/delete", body=body, headers={"Content-Type": "application/json"})
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), {"deleted": True})

    def test_unexpected_post_error_returns_internal_server_error(self):
        def fail_settings(_settings):
            raise RuntimeError("injected settings failure")

        self.server.app.configure_agent = fail_settings
        body = b"{}"
        self.connection.request(
            "POST",
            "/api/settings",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = self.connection.getresponse()
        payload = response.read()

        self.assertEqual(response.status, 500)
        self.assertIn(b"injected settings failure", payload)
