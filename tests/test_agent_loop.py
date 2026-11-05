from __future__ import annotations

import unittest

from docsagent.agent.loop import AgentLoop
from docsagent.providers.model_client import ModelClient


class AgentLoopTests(unittest.TestCase):
    def client_with_turns(self, turns):
        client = object.__new__(ModelClient)
        client.request_format = "openai_chat"
        pending = iter(turns)
        client.chat_turn = lambda _messages, _tools, _temperature: next(pending)
        return client

    def test_duplicate_tool_call_is_blocked_and_agent_can_recover(self):
        call = {"id": "call-1", "name": "read_project_file", "arguments": {"path": "src/app.py"}}
        calls = [0]

        def execute(*_args):
            calls[0] += 1
            return {"content": "value = 1"}

        client = self.client_with_turns([("", [call]), ("", [call]), ("I can answer from the first result.", [])])

        result = AgentLoop().run(client, [{"role": "user", "content": "inspect"}], [{"name": "read_project_file"}], execute)

        self.assertEqual(result, "I can answer from the first result.")
        self.assertEqual(calls[0], 1)

    def test_runtime_status_is_appended_only_to_model_requests(self):
        client = self.client_with_turns([("done", [])])
        seen = []
        client.chat_turn = lambda messages, *_: (seen.append(messages) or ("done", []))
        history = [{"role": "user", "content": "inspect"}]

        self.assertEqual(AgentLoop().run(client, history, [{"name": "read"}], lambda *_: {}), "done")
        self.assertIn("<agent_status>", seen[0][-1]["content"])
        self.assertEqual(history, [{"role": "user", "content": "inspect"}])

    def test_context_budget_is_enforced_before_model_call(self):
        client = self.client_with_turns([])

        with self.assertRaisesRegex(RuntimeError, "context budget"):
            AgentLoop().run(client, [{"role": "user", "content": "x" * 100}], [{"name": "read"}], lambda *_: {}, max_context_chars=10)

    def test_agent_turn_reports_round_and_tool_lifecycle(self):
        call = {"id": "call-1", "name": "read_project_file", "arguments": {"path": "src/app.py"}}
        client = self.client_with_turns([("", [call]), ("done", [])])
        events = []

        result = AgentLoop().run(
            client,
            [{"role": "user", "content": "inspect"}],
            [{"name": "read_project_file"}],
            lambda *_: {"content": "VALUE = 1"},
            on_event=lambda event, **payload: events.append((event, payload)),
        )

        self.assertEqual(result, "done")
        names = [event for event, _ in events]
        self.assertIn("round", names)
        self.assertIn("tool_started", names)
        self.assertIn("tool_finished", names)
        self.assertEqual(events[-1][0], "status")

    def test_round_limit_summarizes_collected_tool_evidence(self):
        call = {"id": "call-1", "name": "read_project_file", "arguments": {"path": "src/app.py"}}
        client = self.client_with_turns([("", [call])])
        captured = []
        client.chat = lambda messages, temperature=0.2: (captured.extend(messages) or "已根据读取结果总结。")
        events = []

        result = AgentLoop().run(
            client,
            [{"role": "user", "content": "inspect"}],
            [{"name": "read_project_file"}],
            lambda *_: {"content": "VALUE = 1"},
            max_rounds=1,
            on_event=lambda event, **payload: events.append((event, payload)),
        )

        self.assertEqual(result, "已根据读取结果总结。")
        self.assertTrue(any("VALUE = 1" in item["content"] for item in captured))
        self.assertTrue(any("执行轮次上限" in item["content"] for item in captured))
        self.assertTrue(any(event == "status" and payload.get("phase") == "finalizing" for event, payload in events))

    def test_tool_call_limit_summarizes_without_executing_over_limit_call(self):
        calls = [
            {"id": "call-1", "name": "read_project_file", "arguments": {"path": "src/one.py"}},
            {"id": "call-2", "name": "read_project_file", "arguments": {"path": "src/two.py"}},
        ]
        client = self.client_with_turns([("", [calls[0]]), ("", [calls[1]])])
        captured = []
        client.chat = lambda messages, temperature=0.2: (captured.extend(messages) or "根据首个文件结果完成回答。")
        executed = []

        result = AgentLoop().run(
            client,
            [{"role": "user", "content": "inspect"}],
            [{"name": "read_project_file"}],
            lambda name, arguments: (executed.append(arguments["path"]) or {"content": arguments["path"]}),
            max_tool_calls=1,
        )

        self.assertEqual(result, "根据首个文件结果完成回答。")
        self.assertEqual(executed, ["src/one.py"])
        self.assertTrue(any("src/one.py" in item["content"] for item in captured))
        self.assertTrue(any("本轮工具调用因超出限制而未执行" in item["content"] for item in captured))

    def test_event_callback_failures_are_propagated(self):
        client = self.client_with_turns([])

        def fail_event(*_args, **_kwargs):
            raise RuntimeError("event sink failed")

        with self.assertRaisesRegex(RuntimeError, "event sink failed"):
            AgentLoop().run(client, [{"role": "user", "content": "inspect"}], on_event=fail_event)

    def test_falsey_non_string_tool_arguments_are_not_treated_as_empty(self):
        for arguments in (0, False):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, "valid JSON"):
                AgentLoop._normalize_tool_args(arguments)


if __name__ == "__main__":
    unittest.main()
