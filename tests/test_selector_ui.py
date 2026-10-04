import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.selector_ui import AddressSelectorSession, ProductSelectorSession


class ValuePattern:
    def __init__(self, wrapper): self.wrapper = wrapper
    @property
    def CurrentValue(self):
        return self.wrapper.data["patterns"]["value"]["CurrentValue"]
    @property
    def CurrentIsReadOnly(self):
        return self.wrapper.data["patterns"]["value"]["CurrentIsReadOnly"]
    def SetValue(self, value):
        self.wrapper.writes.append(value)
        self.wrapper.data["patterns"]["value"]["CurrentValue"] = value
        if self.wrapper.fail_after_write: raise RuntimeError("uncertain")


class Wrapper:
    def __init__(self, data, parent=None):
        self.data, self.container = data, parent
        self.element_info = SimpleNamespace(name=data.get("name", ""),
            control_type=data.get("control_type", ""), class_name=data.get("class_name", ""),
            automation_id=data.get("automation_id", ""), process_id=51052)
        self._children = [Wrapper(child, self) for child in data.get("children", [])]
        self.writes, self.invocations = [], 0
        self.fail_after_write = False
        self.iface_value = ValuePattern(self)
    def children(self): return self._children
    def parent(self): return self.container
    def is_visible(self): return self.data.get("visible", False)
    def is_enabled(self): return self.data.get("enabled", False)
    def get_value(self):
        # pywinauto documents this on EditWrapper, not ComboBoxWrapper.
        if self.element_info.control_type != "Edit":
            raise AttributeError("get_value is an EditWrapper method")
        return self.iface_value.CurrentValue
    def descendants(self, control_type=None):
        result = []
        for child in self._children:
            if control_type is None or child.element_info.control_type == control_type:
                result.append(child)
            result.extend(child.descendants(control_type))
        return result
    def invoke(self):
        self.invocations += 1
        if self.element_info.name == "Cancel":
            dialog = self
            while dialog.element_info.control_type != "Window": dialog = dialog.parent()
            owner = dialog.parent()
            owner._children.remove(dialog)
            owner.data["enabled"] = True


class AddressSelectorUITests(unittest.TestCase):
    def setUp(self):
        tree = json.loads((Path(__file__).parent / "fixtures/address-selector.uia.json").read_text())
        self.root = Wrapper(tree)
        self.session = AddressSelectorSession(self.root)

    def test_nested_enabled_modal_under_disabled_owner(self):
        self.assertFalse(self.root.is_enabled())
        report = self.session.observe()
        self.assertEqual(report["order_number"], "PO000001")
        self.assertEqual(report["query"], "")
        self.assertFalse(report["rows_accessible"])
        self.assertEqual(report["result_state"], "UNVERIFIED")

    def test_literal_query_readback_never_implies_missing_customer(self):
        query = "%{F4} ^a Northstar Office GmbH"
        report = self.session.set_query(query)
        self.assertEqual(report["query"], query)
        self.assertEqual(report["result_state"], "UNVERIFIED")
        with self.assertRaisesRegex(ReviewRequired, "rows_not_accessible"):
            self.session.read_rows()
        self.assertEqual(sum(c.invocations for c in self.root.descendants()), 0)

    def test_cancel_only_invokes_scoped_cancel_and_retains_order(self):
        cancel = next(c for c in self.root.descendants() if c.element_info.name == "Cancel")
        self.session.cancel()
        self.assertEqual(cancel.invocations, 1)
        self.assertTrue(self.root.is_enabled())
        self.assertEqual(self.session.number, "PO000001")
        self.assertFalse(any(c.element_info.name == "Select the address" for c in self.root.descendants()))

    def test_duplicate_search_not_written(self):
        search = self.session.search_control(self.session.dialog())
        search.parent()._children.append(Wrapper(search.data, search.parent()))
        with self.assertRaisesRegex(ReviewRequired, "search_missing_or_ambiguous"):
            self.session.set_query("Northstar")
        self.assertEqual(search.writes, [])

    def test_changed_source_order_blocks_query(self):
        number = next(c for c in self.root.descendants("Edit")
                      if c.data.get("patterns", {}).get("value", {}).get("CurrentValue") == "PO000001")
        number.data["patterns"]["value"]["CurrentValue"] = "PO000002"
        with self.assertRaisesRegex(ReviewRequired, "source_order_changed"):
            self.session.set_query("Northstar")
        self.assertEqual(sum(len(c.writes) for c in self.root.descendants()), 0)

    def test_uncertain_query_write_not_retried(self):
        search = self.session.search_control(self.session.dialog())
        search.fail_after_write = True
        with self.assertRaisesRegex(ReviewRequired, "query_write_uncertain"):
            self.session.set_query("Northstar")
        self.assertEqual(search.writes, ["Northstar"])

    def test_dialog_process_mismatch_stops_before_query(self):
        self.session.dialog().element_info.process_id = 999
        with self.assertRaisesRegex(ReviewRequired, "process_mismatch"):
            self.session.set_query("Northstar")
        self.assertEqual(sum(len(c.writes) for c in self.root.descendants()), 0)

    def test_even_exposed_empty_grid_requires_real_result_reader(self):
        dialog = self.session.dialog()
        dialog._children.append(Wrapper({"control_type": "DataGrid", "visible": True,
                                        "enabled": True}, dialog))
        with self.assertRaisesRegex(ReviewRequired, "result_reader_not_implemented"):
            self.session.read_rows()


class ProductSelectorUITests(unittest.TestCase):
    def setUp(self):
        tree = json.loads((Path(__file__).parent / "fixtures/product-selector.uia.json").read_text())
        self.root = Wrapper(tree)
        self.session = ProductSelectorSession(self.root)

    def test_product_sku_query_has_no_assumed_empty_result(self):
        report = self.session.set_query("CHR-ERG-01")
        self.assertEqual(report["query"], "CHR-ERG-01")
        self.assertEqual(report["order_number"], "PO000001")
        self.assertEqual(report["result_state"], "UNVERIFIED")
        with self.assertRaisesRegex(ReviewRequired, "product_selector_rows_not_accessible"):
            self.session.read_rows()

    def test_product_session_never_attaches_to_address_dialog(self):
        dialog = self.session.dialog()
        dialog.element_info.name = "Select the address"
        with self.assertRaisesRegex(ReviewRequired, "product_selector_dialog_missing_or_ambiguous"):
            ProductSelectorSession(self.root)
        self.assertEqual(sum(c.invocations for c in self.root.descendants()), 0)

    def test_cancel_product_selector_keeps_source_editor(self):
        cancel = next(c for c in self.root.descendants() if c.element_info.name == "Cancel")
        self.session.cancel()
        self.assertEqual(cancel.invocations, 1)
        self.assertTrue(self.root.is_enabled())
        self.assertTrue(any(c.element_info.name == "New Order" for c in self.root.descendants("Pane")))

    def test_session_does_not_reuse_uploaded_numeric_ids(self):
        for index, control in enumerate(self.root.descendants()):
            control.element_info.automation_id = "next-session-" + str(index)
            control.data["rect"] = [0, 0, 1, 1]
        self.assertEqual(self.session.set_query("MAT-DESK-02")["query"], "MAT-DESK-02")
