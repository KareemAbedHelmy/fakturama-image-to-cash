import unittest
from unittest import mock

from fakturama_automation import order_finalize_ui, product_create_ui as pc
from fakturama_automation.errors import ReviewRequired
from test_order_finalize import SOURCE, Recorder, n


def window(tabs, product=None):
    children = [n("TabItem", t, visible=False) for t in tabs]
    if product:
        children.append(n("Pane", "New product", [
            n("Edit", "Item Number", value=product["sku"]), n("Edit", "Name", value=product["name"]),
            n("Edit", "Description", value=product["name"]), n("Text", "Price (gross)"),
            n("Pane", children=[n("Edit", value=product["gross"])]),
            n("ComboBox", "VAT", value=product["vat"])]))
    children += [n("Button", "Create a new product", invoke=True),
                 n("Button", "Save the current contents", invoke=True)]
    return n("Window", "Fakturama - x", children)


MAT = {"sku": "MAT-DESK-02", "name": "Anti-Fatigue Desk Mat", "gross": "47,60 EUR", "vat": "VAT 19%"}


class Live:
    def __init__(self, before, after):
        self.before, self.after, self.button = before, after, Recorder()

    def __call__(self, root):
        wrappers = mock.MagicMock()
        wrappers.__getitem__.side_effect = lambda path: self.button
        return (self.after if self.button.calls else self.before), wrappers


class ProductCreateTests(unittest.TestCase):
    def run_scope(self, fn, before, after, sku="MAT-DESK-02"):
        live = Live(before, after)
        with mock.patch.object(pc, "_snapshot", live), mock.patch.object(order_finalize_ui.time, "sleep"):
            return fn(object(), SOURCE, sku, lambda: None), live.button.calls

    def test_open_invokes_once(self):
        result, calls = self.run_scope(pc.open_new_product_scope, window(["*New Order"]),
                                       window(["*New Order", "New product"]))
        self.assertEqual(result["status"], "NEW_PRODUCT_EDITOR_OPENED")
        self.assertEqual(calls, 1)

    def test_open_refuses_unknown_sku_or_existing_editor(self):
        with self.assertRaises(ReviewRequired):
            self.run_scope(pc.open_new_product_scope, window(["*New Order"]), None, sku="NOPE")
        with self.assertRaises(ReviewRequired):
            self.run_scope(pc.open_new_product_scope, window(["*New Order", "*New product"]), None)

    def test_save_verifies_source_values_then_saves_once(self):
        result, calls = self.run_scope(pc.save_product_scope, window(["*New Order", "*New product"], MAT),
                                       window(["*New Order", "Anti-Fatigue Desk Mat"]))
        self.assertEqual(result["status"], "PRODUCT_SAVED")
        self.assertEqual(calls, 1)

    def test_wrong_gross_blocks_save(self):
        with self.assertRaises(ReviewRequired) as ctx:
            self.run_scope(pc.save_product_scope,
                           window(["*New Order", "*New product"], {**MAT, "gross": "40,00 EUR"}), None)
        self.assertEqual(ctx.exception.code, "product_save_gross_price_not_observed")


if __name__ == "__main__":
    unittest.main()
