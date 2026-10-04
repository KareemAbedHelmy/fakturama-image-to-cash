import json
import unittest
from pathlib import Path

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.extraction import extract_claude_vision

EXPECTED = json.loads((Path(__file__).resolve().parents[1] / "examples" / "order.provided.expected.json").read_text())


class FakeConnection:
    def __init__(self, status, body):
        self.status, self.body, self.sent = status, body, None

    def request(self, method, path, body, headers):
        self.sent = (method, path, json.loads(body), headers)

    def getresponse(self):
        return self

    def read(self, limit):
        return json.dumps(self.body).encode()

    def close(self):
        pass


def reply(order):
    return {"content": [{"type": "tool_use", "name": "record_order", "input": order}]}


class ClaudeExtractionTests(unittest.TestCase):
    def test_forced_schema_tool_and_parsed_order(self):
        connection = FakeConnection(200, reply(EXPECTED))
        order = extract_claude_vision(b"png", api_key="k", connection_factory=lambda: connection)
        self.assertEqual(order.external_reference, "WEB-2026-0714-A17")
        method, path, payload, headers = connection.sent
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(payload["tool_choice"], {"type": "tool", "name": "record_order"})
        self.assertEqual(headers["x-api-key"], "k")

    def test_missing_key_http_error_and_bad_reply_need_review(self):
        with self.assertRaises(ReviewRequired):
            extract_claude_vision(b"png", api_key="")
        with self.assertRaises(ReviewRequired) as ctx:
            extract_claude_vision(b"png", api_key="k", connection_factory=lambda: FakeConnection(401, {}))
        self.assertEqual(ctx.exception.code, "claude_http_401")
        with self.assertRaises(ReviewRequired):
            extract_claude_vision(b"png", api_key="k",
                                  connection_factory=lambda: FakeConnection(200, {"content": []}))

    def test_schema_invalid_model_output_is_rejected(self):
        bad = dict(EXPECTED, currency="USD")
        with self.assertRaises(ReviewRequired):
            extract_claude_vision(b"png", api_key="k", connection_factory=lambda: FakeConnection(200, reply(bad)))


if __name__ == "__main__":
    unittest.main()
