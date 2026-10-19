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


class ReviewIssueTidyTests(unittest.TestCase):
    def test_repeated_and_excess_issues_are_dropped_but_data_is_kept(self):
        from fakturama_automation.extraction import MAX_REVIEW_ISSUES, tidy_review_issues
        issues = ['same issue'] * 40 + [f'issue {i}' for i in range(30)] + ['', 7]
        tidied = tidy_review_issues({'currency': 'EUR', 'review_issues': issues})
        self.assertEqual(tidied['currency'], 'EUR')
        self.assertEqual(len(tidied['review_issues']), MAX_REVIEW_ISSUES)
        self.assertEqual(tidied['review_issues'][0], 'same issue')
        self.assertEqual(len(set(tidied['review_issues'])), MAX_REVIEW_ISSUES)

    def test_other_shapes_pass_through(self):
        from fakturama_automation.extraction import tidy_review_issues
        self.assertEqual(tidy_review_issues({'a': 1}), {'a': 1})
        self.assertEqual(tidy_review_issues([1]), [1])


class NormalizeCandidateTests(unittest.TestCase):
    def test_percent_signs_are_stripped_and_missing_overall_charges_become_zero(self):
        from fakturama_automation.extraction import normalize_candidate
        value = normalize_candidate({
            'items': [{'sku': 'A', 'vat_percent': '19%', 'discount_percent': '10 %'},
                      {'sku': 'B', 'vat_percent': '7', 'discount_percent': '0%'}],
            'order_discount_percent': '', 'shipping_net': None, 'review_issues': []})
        self.assertEqual([(i['vat_percent'], i['discount_percent']) for i in value['items']],
                         [('19', '10'), ('7', '0')])
        self.assertEqual((value['order_discount_percent'], value['shipping_net']), ('0', '0.00'))
        self.assertEqual(len(value['review_issues']), 2)

    def test_printed_values_are_left_alone(self):
        from fakturama_automation.extraction import normalize_candidate
        value = normalize_candidate({'items': [], 'order_discount_percent': '5%', 'shipping_net': '4.90',
                                     'review_issues': []})
        self.assertEqual((value['order_discount_percent'], value['shipping_net']), ('5', '4.90'))
        self.assertEqual(value['review_issues'], [])
