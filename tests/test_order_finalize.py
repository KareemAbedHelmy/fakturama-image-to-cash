import unittest
from pathlib import Path
from unittest import mock

from fakturama_automation import order_finalize_ui as fin
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.input_io import read_order

BILLING = "\r\n".join(["Northstar Office GmbH", "Marta Klein", "Friedrichstrasse 88", "DE-10117 Berlin", "Germany"])
SOURCE = read_order(Path(__file__).resolve().parents[1] / "examples" / "order.provided.expected.json")


def n(kind, name="", children=(), value=None, visible=True, invoke=False):
    patterns = {}
    if value is not None:
        patterns["value"] = {"CurrentValue": value, "CurrentIsReadOnly": 0}
    if invoke:
        patterns["invoke"] = {}
    return {"control_type": kind, "name": name, "class_name": "SWT_Window0", "visible": visible,
            "enabled": True, "patterns": patterns, "children": list(children)}


def document_tree(editor="New Order", tab="*New Order", net="570,00 EUR", vat="108,30 EUR",
                  total="678,30 EUR", reference="WEB-2026-0714-A17", address=None):
    address = address or "Northstar Office Warehouse\r\nBeusselstrasse 44\r\nDE-10553 Berlin\r\nGermany"
    header = n("Pane", children=[
        n("Edit", value="PO000001"), n("Text", "Date"), n("Pane", children=[n("Edit", value="14 Jul 2026")]),
        n("ComboBox", value="Net")])
    refs = n("Pane", children=[
        n("Text", "Cust.Ref."), n("Edit", "Cust.Ref.", value=reference),
        n("Pane", children=[n("Text", "VAT"), n("ComboBox", "VAT", value="With VAT")]),
        n("Group", "Create a follow-up document", [n("Button", "Invoice", invoke=True)]),
        n("Pane", children=[n("Text", "Addresses")]),
        n("Tab", "Delivery address", [n("Edit", value=address)], visible=False)])
    totals = n("Pane", children=[
        n("Text", "Total Net"), n("Edit", "Total Net", value=net), n("Text", "Discount"),
        n("Edit", "Discount", value="0%"), n("Text", "Shipping"),
        n("ComboBox", "Shipping", value="Free of shipping costs"), n("Text", "VAT"),
        n("Edit", "VAT", value=vat), n("Text", "Total"), n("Edit", "Total", value=total)])
    body = n("Pane", editor, [n("Pane", children=[n("Text", "No."), header, refs]), totals])
    return n("Window", "Fakturama - x", [
        n("TabItem", tab, visible=False), body,
        n("Button", "Save the current contents", invoke=True)])


class Recorder:
    def __init__(self):
        self.calls = 0
        self.iface_invoke = self

    def Invoke(self):
        self.calls += 1


class FakeLive:
    """Serves snapshots; switches to `after` once a button was invoked."""

    def __init__(self, before, after):
        self.before, self.after, self.button = before, after, Recorder()

    def __call__(self, root):
        tree = self.after if self.button.calls else self.before
        wrappers = mock.MagicMock()
        wrappers.__getitem__.side_effect = lambda path: self.button
        return tree, wrappers


class AmountTests(unittest.TestCase):
    def test_observed_formats(self):
        self.assertEqual(str(fin.parse_display_amount("678,30 EUR")), "678.30")
        self.assertEqual(str(fin.parse_display_amount("1.234,50 EUR")), "1234.50")
        self.assertEqual(str(fin.parse_display_amount("250,00Â EUR")), "250.00")
        with self.assertRaises(ReviewRequired):
            fin.parse_display_amount("about 5")


class VerifyTests(unittest.TestCase):
    def values(self, **kw):
        return fin.read_document(document_tree(**kw))["values"]

    def test_complete_order_matches(self):
        issues, shown = fin.verify_order_values(self.values(), SOURCE, "PO000001")
        self.assertEqual(issues, [])
        self.assertEqual(shown, ["delivery"])

    def test_incomplete_line_is_rejected(self):
        issues, _ = fin.verify_order_values(self.values(net="250,00 EUR", vat="47,50 EUR", total="297,50 EUR"),
                                            SOURCE, "PO000001")
        self.assertIn("subtotal_mismatch", issues)
        self.assertIn("total_mismatch", issues)

    def test_reference_number_and_address_checked(self):
        issues, _ = fin.verify_order_values(self.values(reference="X", address="Elsewhere 1"), SOURCE, "PO000002")
        self.assertEqual(set(issues), {"number_mismatch", "reference_mismatch", "address_not_matching_source"})


class ScopeTests(unittest.TestCase):
    def run_scope(self, fn, before, after, **kw):
        live = FakeLive(before, after)
        with mock.patch.object(fin, "_snapshot", live), mock.patch.object(fin.time, "sleep"), \
                mock.patch('fakturama_automation.order_address_ui.verify_order_addresses') as addresses, \
                mock.patch.object(fin, 'handle_save_contact_selector', return_value=False):
            addresses.return_value = {'invoice_address_selected': True}
            result = fn(object(), SOURCE, "PO000001", lambda: None, **kw)
        return result, live.button.calls

    def test_verify_only_never_saves(self):
        result, calls = self.run_scope(fin.finalize_order_scope, document_tree(), None)
        self.assertEqual(result["status"], "ORDER_VERIFIED_UNSAVED")
        self.assertEqual(calls, 0)

    def test_save_once_and_reread(self):
        result, calls = self.run_scope(fin.finalize_order_scope, document_tree(address=BILLING),
                                       document_tree(editor="PO000001", tab="PO000001"), save=True)
        self.assertEqual(result["status"], "ORDER_SAVED_AND_REREAD")
        self.assertEqual(calls, 1)

    def test_mismatch_blocks_save(self):
        with self.assertRaises(ReviewRequired):
            self.run_scope(fin.finalize_order_scope, document_tree(total="1,00 EUR"), None, save=True)

    def test_unconfirmed_save_is_not_retried(self):
        diagnostics = {}
        with mock.patch.object(fin.time, "monotonic", side_effect=[0, 0, 100]):
            with self.assertRaises(ReviewRequired) as ctx:
                self.run_scope(fin.finalize_order_scope, document_tree(address=BILLING),
                               document_tree(address=BILLING), save=True, diagnostics=diagnostics)
        self.assertEqual(ctx.exception.code, "order_save_not_confirmed_check_dialogs")
        self.assertTrue(diagnostics["progress"]["save_invoked"])

    def test_save_prepares_addresses_before_invoking_save(self):
        live = FakeLive(document_tree(), document_tree(editor='PO000001', tab='PO000001', address=BILLING))
        def addresses(*args, **kwargs):
            self.assertEqual(live.button.calls, 0)
            live.before = document_tree(address=BILLING)
            return {'invoice_address_selected': True}
        with mock.patch.object(fin, '_snapshot', live), \
                mock.patch('fakturama_automation.order_address_ui.verify_order_addresses', side_effect=addresses), \
                mock.patch.object(fin, 'handle_save_contact_selector', return_value=False):
            result = fin.finalize_order_scope(object(), SOURCE, 'PO000001', lambda: None, save=True)
        self.assertEqual(result['status'], 'ORDER_SAVED_AND_REREAD')
        self.assertEqual(live.button.calls, 1)

    def test_already_saved_order_is_never_saved_again(self):
        with self.assertRaises(ReviewRequired) as ctx:
            self.run_scope(fin.finalize_order_scope, document_tree(editor="PO000001", tab="PO000001"),
                           None, save=True)
        self.assertEqual(ctx.exception.code, "order_already_saved_do_not_save_again")

    def test_contact_popup_is_handled_after_one_save(self):
        live = FakeLive(document_tree(address=BILLING),
                        document_tree(editor='PO000001', tab='PO000001', address=BILLING))
        def popup(*args):
            self.assertEqual(live.button.calls, 1)
            return True
        with mock.patch.object(fin, '_snapshot', live), \
                mock.patch('fakturama_automation.order_address_ui.verify_order_addresses', return_value={}), \
                mock.patch.object(fin, 'handle_save_contact_selector', side_effect=popup) as handler:
            result = fin.finalize_order_scope(object(), SOURCE, 'PO000001', lambda: None, save=True)
        self.assertEqual(result['status'], 'ORDER_SAVED_AND_REREAD')
        self.assertEqual(live.button.calls, 1)
        self.assertEqual(handler.call_count, 1)

    def test_contact_popup_failure_is_not_swallowed_or_retried(self):
        live = FakeLive(document_tree(address=BILLING), document_tree(address=BILLING))
        diagnostics = {}
        with mock.patch.object(fin, '_snapshot', live), \
                mock.patch('fakturama_automation.order_address_ui.verify_order_addresses', return_value={}), \
                mock.patch.object(fin, 'handle_save_contact_selector',
                                  side_effect=ReviewRequired('order_save_contact_not_resolved')) as handler:
            with self.assertRaises(ReviewRequired) as caught:
                fin.finalize_order_scope(object(), SOURCE, 'PO000001', lambda: None,
                                         save=True, diagnostics=diagnostics)
        self.assertEqual(caught.exception.code, 'order_save_contact_not_resolved')
        self.assertTrue(diagnostics['progress']['save_invoked'])
        self.assertEqual(live.button.calls, 1)
        self.assertEqual(handler.call_count, 1)

    def test_followup_requires_saved_order(self):
        with self.assertRaises(ReviewRequired) as ctx:
            self.run_scope(fin.create_followup_invoice_scope, document_tree(), None)
        self.assertEqual(ctx.exception.code, "followup_order_not_saved_or_unsaved_tabs_open")

    def test_followup_opens_linked_invoice_once(self):
        result, calls = self.run_scope(fin.create_followup_invoice_scope,
                                       document_tree(editor="PO000001", tab="PO000001"),
                                       document_tree(editor="New Invoice", tab="*New Invoice"))
        self.assertEqual(result["status"], "LINKED_INVOICE_EDITOR_OPENED_UNSAVED")
        self.assertEqual(result["copy_issues"], [])
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
