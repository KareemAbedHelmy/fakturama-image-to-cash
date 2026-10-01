import json
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.domain import OrderInput
from fakturama_automation.finance import line_net, product_gross, validate_order
from fakturama_automation.input_io import read_order
from fakturama_automation.journal import Journal
from fakturama_automation.simulator import Simulator
from fakturama_automation.workflow import Workflow

EXPECTED = Path(__file__).resolve().parents[1]/"examples/order.provided.expected.json"


class ProvidedOrderTests(unittest.TestCase):
    def test_reviewed_image_values_reconcile(self):
        order = read_order(EXPECTED)
        self.assertEqual(validate_order(order).gross, Decimal("678.30"))
        self.assertEqual(line_net(order.items[0]), Decimal("450.00"))
        self.assertEqual(line_net(order.items[1]), Decimal("120.00"))
        self.assertEqual(product_gross(order.items[0]), Decimal("297.50"))
        self.assertEqual(product_gross(order.items[1]), Decimal("47.60"))

    def test_source_customer_id_is_reference_data(self):
        order = read_order(EXPECTED)
        self.assertEqual(order.source_customer_id, "CUST-1007")
        with TemporaryDirectory() as folder, Journal(Path(folder)/"j.sqlite") as journal:
            sim = Simulator()
            Workflow(sim, journal, "provided-image-simulator").run(order)
            self.assertEqual(sim.debtor_rows[0].id, "1")
            self.assertNotEqual(sim.debtor_rows[0].id, order.source_customer_id)

    def test_distinct_delivery_recipient_and_unit_survive_saved_invoice(self):
        order = read_order(EXPECTED)
        with TemporaryDirectory() as folder, Journal(Path(folder)/"j.sqlite") as journal:
            sim = Simulator()
            result = Workflow(sim, journal, "provided-image-simulator").run(order)
            invoice = sim.documents[("Invoice", result.invoice_number)]
            self.assertEqual(invoice.delivery.recipient_name, "Northstar Office Warehouse")
            self.assertEqual(invoice.delivery.street, "Beusselstrasse 44")
            self.assertEqual(invoice.items[0].unit, "pcs")
            self.assertEqual(invoice.payment_value, Decimal("678.30"))

    def test_saved_recipient_corruption_stops(self):
        class CorruptRecipient(Simulator):
            def save_invoice(self, handle):
                super().save_invoice(handle)
                key = ("Invoice", handle.number)
                snapshot = self.documents[key]
                wrong = snapshot.delivery.model_copy(update={"recipient_name": "Northstar Office GmbH"})
                self.documents[key] = replace(snapshot, delivery=wrong)
        with TemporaryDirectory() as folder, Journal(Path(folder)/"j.sqlite") as journal:
            with self.assertRaisesRegex(ReviewRequired, "document_contents_mismatch"):
                Workflow(CorruptRecipient(), journal, "provided-image-simulator").run(read_order(EXPECTED))

    def test_same_sku_different_units_requires_review(self):
        data = json.loads(EXPECTED.read_text())
        second = dict(data["items"][0], unit="carton")
        data["items"] = [data["items"][0], second]
        data["totals"] = {"net": "900.00", "vat": "171.00", "gross": "1071.00"}
        with self.assertRaisesRegex(ReviewRequired, "repeated_sku_definition_conflict"):
            validate_order(OrderInput.model_validate(data))
