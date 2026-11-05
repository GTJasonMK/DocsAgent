from __future__ import annotations

import unittest
from unittest.mock import patch

from docsagent.providers.model_client import ModelClient


class ModelClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ModelClient({
            "model_url": "https://example.test/v1",
            "model": "test-model",
            "temperature": 0.8,
        })
        self.messages = [{"role": "user", "content": "hello"}]

    def test_chat_uses_configured_temperature_when_omitted(self):
        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(self.client, "_request", return_value=response) as request:
            self.assertEqual(self.client.chat(self.messages), "ok")

        self.assertEqual(request.call_args.args[1]["temperature"], 0.8)

    def test_chat_respects_explicit_default_temperature(self):
        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(self.client, "_request", return_value=response) as request:
            self.assertEqual(self.client.chat(self.messages, temperature=0.2), "ok")

        self.assertEqual(request.call_args.args[1]["temperature"], 0.2)

    def test_provider_arguments_must_be_a_json_object(self):
        self.assertEqual(ModelClient._arguments_object('{"path":"src/app.py"}'), {"path": "src/app.py"})
        for value in ("not json", "[]"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ModelClient._arguments_object(value)


if __name__ == "__main__":
    unittest.main()
