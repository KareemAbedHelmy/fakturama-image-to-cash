import json
import unittest
from pathlib import Path

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.order_ui import analyze_order_header


class ObservedOrderUITests(unittest.TestCase):
    def setUp(self):
        self.root = json.loads((Path(__file__).parent / "fixtures/new-order.uia.json").read_text())

    def nodes(self, node=None):
        node = self.root if node is None else node
        yield node
        for child in node.get("children", []):
            yield from self.nodes(child)

    def test_observed_header_and_currency_blocker(self):
        report = analyze_order_header(self.root, "EUR")
        self.assertEqual(report["values"]["number"], "PO000001")
        self.assertEqual(report["values"]["date_text"], "1 Oct 2026")
        self.assertEqual(report["values"]["price_mode"], "Gross")
        self.assertEqual(report["values"]["vat_mode"], "With VAT")
        self.assertEqual(report["currency"], "AED")
        self.assertIn("display_currency_mismatch", report["review_issues"])
        self.assertTrue(report["save_invokable"])
        self.assertFalse(report["followup_invoice_invokable"])

    def test_no_snapshot_ids_or_absolute_coordinates_used(self):
        for index, node in enumerate(self.nodes()):
            node["automation_id"] = "fresh-session-" + str(index)
            node["rect"] = [0, 0, 1, 1]
            if node.get("patterns", {}).get("value", {}).get("CurrentValue") == "PO000001":
                node["patterns"]["value"]["CurrentValue"] = "PO000973"
        self.assertEqual(analyze_order_header(self.root, "EUR")["values"]["number"], "PO000973")

    def test_disabled_parent_blocks_child_followup_even_if_child_enabled(self):
        invoice = next(n for n in self.nodes() if n.get("name") == "Invoice")
        self.assertTrue(invoice["enabled"])
        self.assertFalse(analyze_order_header(self.root, "AED")["followup_invoice_invokable"])

    def test_matching_currency_has_no_currency_mismatch(self):
        report = analyze_order_header(self.root, "AED")
        self.assertNotIn("display_currency_mismatch", report["review_issues"])
        self.assertEqual(report["accessible_item_grid_count"], 0)
        self.assertIn("item_grid_not_exposed_in_uia", report["review_issues"])

    def test_duplicate_reference_never_selects_first(self):
        editor = next(n for n in self.nodes() if n.get("control_type") == "Pane" and n.get("name") == "New Order")
        editor["children"].append({"control_type": "Edit", "name": "Cust.Ref."})
        with self.assertRaisesRegex(ReviewRequired, "reference_missing_or_ambiguous"):
            analyze_order_header(self.root, "EUR")

    def test_anonymous_number_beside_wrong_label_rejected(self):
        label = next(n for n in self.nodes() if n.get("name") == "No.")
        label["name"] = "Unrelated field"
        with self.assertRaisesRegex(ReviewRequired, "number_label_missing_or_ambiguous"):
            analyze_order_header(self.root, "EUR")

    def test_currency_codes_must_agree_in_all_totals(self):
        total = next(n for n in self.nodes() if n.get("control_type") == "Edit" and n.get("name") == "Total")
        total["patterns"]["value"]["CurrentValue"] = "EUR0.00"
        report = analyze_order_header(self.root, "EUR")
        self.assertIsNone(report["currency"])
        self.assertIn("display_currency_unverified", report["review_issues"])

    def test_hidden_helper_not_editor(self):
        self.root["visible"] = False
        with self.assertRaisesRegex(ReviewRequired, "root_not_visible_fakturama_shell"):
            analyze_order_header(self.root, "EUR")

    def test_truncated_tree_not_complete_empty_grid(self):
        self.root["children"].append({"truncated": True})
        with self.assertRaisesRegex(ReviewRequired, "tree_truncated"):
            analyze_order_header(self.root, "EUR")

    def test_modal_dialog_blocks_header_inspection(self):
        self.root["enabled"] = False
        with self.assertRaisesRegex(ReviewRequired, "editor_disabled"):
            analyze_order_header(self.root, "EUR")
