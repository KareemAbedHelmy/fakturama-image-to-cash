import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from fakturama_automation.cli import seed_simulator
from fakturama_automation.domain import OrderInput
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.finance import product_gross
from fakturama_automation.journal import Journal
from fakturama_automation.matching import PaymentRow, VatRow
from fakturama_automation.simulator import Simulator
from fakturama_automation.workflow import Workflow
from test_domain_finance import fixture, source


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.journal = Journal(Path(self.tmp.name) / "journal.sqlite")
        self.sim = Simulator()

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def run_flow(self, order=None):
        return Workflow(self.sim, self.journal, "test-workspace").run(order or source())

    def test_all_missing_continuous_order_first(self):
        result = self.run_flow()
        calls = self.sim.calls
        self.assertLess(calls.index("open_new_order"), calls.index("search_debtors_from_order"))
        self.assertLess(calls.index("open_debtor_editor"), calls.index("create_payment_term"))
        self.assertLess(calls.index("create_vat"), calls.index("create_product"))
        self.assertLess(calls.index("save_order"), calls.index("create_followup_invoice"))
        self.assertLess(calls.index("create_followup_invoice"), calls.index("save_invoice"))
        self.assertEqual(calls.count("save_order"), 1)
        self.assertEqual(calls.count("save_invoice"), 1)
        self.assertEqual(calls.count("save_debtor"), 1)
        self.assertEqual(calls.count("create_product"), 2)
        self.assertEqual(self.sim.product_selection_count, 2)
        self.assertEqual(self.sim.products[0].gross, Decimal("119.00"))
        invoice = self.sim.documents[("Invoice", result.invoice_number)]
        self.assertEqual(invoice.source_order_number, result.order_number)
        self.assertEqual(invoice.payment_value, Decimal("278.40"))
        self.assertTrue(invoice.paid)
        self.assertEqual(self.sim.documents[("Order", result.order_number)].state, "open")
        self.assertTrue(self.sim.order_open)
        self.assertEqual(self.journal.summary()[0]["state"], "VERIFIED")

    def test_all_existing_no_master_creations(self):
        seed_simulator(self.sim, source(), "existing")
        self.run_flow()
        for action in ["open_debtor_editor", "create_payment_term", "create_vat", "create_product"]:
            self.assertNotIn(action, self.sim.calls)
        self.assertEqual(self.sim.calls.count("search_products_from_order"), 2)

    def test_mixed_master_reuse(self):
        seed_simulator(self.sim, source(), "mixed")
        self.run_flow()
        self.assertEqual(self.sim.calls.count("create_product"), 1)
        self.assertEqual(self.sim.calls.count("create_vat"), 1)

    def test_unpaid_does_not_invent_payment(self):
        data = fixture()
        data["payment"].update(status="UNPAID", date=None)
        order = OrderInput.model_validate(data)
        result = self.run_flow(order)
        invoice = self.sim.documents[("Invoice", result.invoice_number)]
        self.assertFalse(invoice.paid)
        self.assertIsNone(invoice.payment_date)
        self.assertIsNone(invoice.payment_value)
        self.assertEqual(invoice.state, "unpaid")

    def test_separate_delivery_address_is_preserved(self):
        data = fixture()
        data["debtor"]["delivery"]["street"] = "Delivery Street 20"
        order = OrderInput.model_validate(data)
        result = self.run_flow(order)
        saved = self.sim.documents[("Invoice", result.invoice_number)]
        self.assertEqual(saved.delivery.street, "Delivery Street 20")
        self.assertNotEqual(saved.billing, saved.delivery)

    def test_existing_address_conflict_stops_before_products(self):
        data = fixture()
        data["debtor"]["billing"]["street"] = "Conflicting Street 99"
        self.sim.seed_debtor(OrderInput.model_validate(data).debtor)
        with self.assertRaisesRegex(ReviewRequired, "populated_addresses_mismatch"):
            self.run_flow()
        self.assertNotIn("search_products_from_order", self.sim.calls)

    def test_duplicate_debtors_stops_no_creation(self):
        self.sim.seed_debtor(source().debtor)
        self.sim.seed_debtor(source().debtor)
        with self.assertRaisesRegex(ReviewRequired, "ambiguous_debtor"):
            self.run_flow()
        self.assertNotIn("open_debtor_editor", self.sim.calls)

    def test_duplicate_products_stops(self):
        item = source().items[0]
        self.sim.seed_product(item, product_gross(item))
        self.sim.seed_product(item, product_gross(item))
        with self.assertRaisesRegex(ReviewRequired, "ambiguous_product"):
            self.run_flow()
        self.assertNotIn("create_product", self.sim.calls)

    def test_vat_conflict_stops_before_product_creation(self):
        self.sim.vats.append(VatRow("1", "VAT 19%", Decimal("7"), "S"))
        with self.assertRaisesRegex(ReviewRequired, "conflicting_vat_definition"):
            self.run_flow()
        self.assertNotIn("create_product", self.sim.calls)

    def test_existing_product_price_unchanged_transaction_price_used(self):
        seed_simulator(self.sim, source(), "existing")
        self.sim.products[0].gross = Decimal("999.00")
        self.run_flow()
        self.assertEqual(self.sim.products[0].gross, Decimal("999.00"))
        self.assertEqual(self.sim.lines[0].unit_net, Decimal("100.00"))

    def test_repeated_sku_creates_one_master_two_lines(self):
        data = fixture()
        data["items"] = [data["items"][0], dict(data["items"][0])]
        data["totals"] = {"net": "360.00", "vat": "68.40", "gross": "428.40"}
        self.run_flow(OrderInput.model_validate(data))
        self.assertEqual(self.sim.calls.count("create_product"), 1)
        self.assertEqual(self.sim.product_selection_count, 2)
        self.assertEqual(len(self.sim.lines), 2)

    def test_invalid_input_no_desktop_or_journal_job(self):
        data = fixture()
        data["totals"]["gross"] = "1"
        with self.assertRaises(ReviewRequired):
            self.run_flow(OrderInput.model_validate(data))
        self.assertEqual(self.sim.calls, [])
        self.assertEqual(self.journal.summary(), [])

    def test_new_masters_must_be_reselected_and_found(self):
        for kind in ["debtor", "product", "vat"]:
            with self.subTest(kind=kind), TemporaryDirectory() as folder, Journal(Path(folder)/"j.sqlite") as journal:
                sim = Simulator()
                sim.hide_saved_master = kind
                with self.assertRaises(ReviewRequired):
                    Workflow(sim, journal, kind).run(source())
                self.assertNotIn("save_order", sim.calls)
                self.assertEqual(journal.summary()[0]["state"], "REVIEW")

    def test_missing_saved_order_row_prevents_followup(self):
        self.sim.hide_document_rows = True
        with self.assertRaisesRegex(ReviewRequired, "saved_document_missing_or_ambiguous"):
            self.run_flow()
        self.assertNotIn("create_followup_invoice", self.sim.calls)

    def test_uncertain_save_never_retried(self):
        for action in ["save_debtor", "create_product", "save_order", "save_invoice"]:
            with self.subTest(action=action), TemporaryDirectory() as folder, Journal(Path(folder)/"j.sqlite") as journal:
                sim = Simulator()
                sim.fail_after = action
                with self.assertRaisesRegex(ReviewRequired, "simulated_failure_after_action"):
                    Workflow(sim, journal, action).run(source())
                self.assertEqual(sim.calls.count(action), 1)
                self.assertEqual(journal.summary()[0]["state"], "REVIEW")
                # Input duplicates cannot start another desktop operation after uncertain save.
                count = len(sim.calls)
                with self.assertRaisesRegex(ReviewRequired, "duplicate_job"):
                    Workflow(sim, journal, action).run(source())
                self.assertEqual(count, len(sim.calls))
                if action == "save_invoice":
                    self.assertIn(("Invoice", "I00001"), sim.documents)

    def test_fail_before_each_core_operation_prevents_success(self):
        for action in ["open_new_order", "configure_order", "search_debtors_from_order", "select_debtor",
                       "verify_addresses", "create_payment_term", "create_vat", "create_product",
                       "select_product", "complete_line", "save_order", "create_followup_invoice",
                       "apply_invoice_payment", "save_invoice", "read_saved_document"]:
            with self.subTest(action=action), TemporaryDirectory() as folder, Journal(Path(folder)/"j.sqlite") as journal:
                sim = Simulator()
                sim.fail_before = action
                with self.assertRaises(ReviewRequired):
                    Workflow(sim, journal, action).run(source())
                self.assertEqual(journal.summary()[0]["state"], "REVIEW")

    def test_persisted_payment_corruption_is_detected(self):
        class CorruptPayment(Simulator):
            def save_invoice(self, invoice):
                super().save_invoice(invoice)
                key = ("Invoice", invoice.number)
                self.documents[key] = replace(self.documents[key], payment_value=Decimal("0.01"))
        self.sim = CorruptPayment()
        with self.assertRaisesRegex(ReviewRequired, "invoice_paid_details_mismatch"):
            self.run_flow()

    def test_persisted_link_corruption_is_detected(self):
        class CorruptLink(Simulator):
            def save_invoice(self, invoice):
                super().save_invoice(invoice)
                key = ("Invoice", invoice.number)
                self.documents[key] = replace(self.documents[key], source_order_number="wrong")
        self.sim = CorruptLink()
        with self.assertRaisesRegex(ReviewRequired, "persisted_invoice_link_or_dates_mismatch"):
            self.run_flow()

    def test_persisted_debtor_identity_corruption_is_detected(self):
        class CorruptIdentity(Simulator):
            def save_invoice(self, invoice):
                super().save_invoice(invoice)
                key = ("Invoice", invoice.number)
                self.documents[key] = replace(self.documents[key], debtor_company="Wrong Company")
        self.sim = CorruptIdentity()
        with self.assertRaisesRegex(ReviewRequired, "document_contents_mismatch"):
            self.run_flow()

    def test_order_tab_continuity(self):
        class CloseOrder(Simulator):
            def save_debtor(self, editor, method):
                super().save_debtor(editor, method)
                self.order_open = False
        self.sim = CloseOrder()
        with self.assertRaisesRegex(ReviewRequired, "original_order_not_open"):
            self.run_flow()
        self.assertNotIn("search_products_from_order", self.sim.calls)

    def test_invoice_payment_unavailable_stops_without_save(self):
        seed_simulator(self.sim, source(), "existing")
        self.sim.payments.clear()
        with self.assertRaisesRegex(ReviewRequired, "invoice_payment_unavailable"):
            self.run_flow()
        self.assertNotIn("save_invoice", self.sim.calls)

    def test_existing_remote_reference_stops_before_new_order(self):
        self.run_flow()
        self.sim.order_open = False
        # Different journal cannot authorize creation when remote Documents already contain the reference.
        with Journal(Path(self.tmp.name)/"second.sqlite") as journal:
            with self.assertRaisesRegex(ReviewRequired, "existing_document_reference"):
                Workflow(self.sim, journal, "test-workspace").run(source())

    def test_reuses_payment_from_terms_list_when_dropdown_is_stale(self):
        class StaleDropdown(Simulator):
            def debtor_payment_options(self, editor):
                super().debtor_payment_options(editor)
                return ()
        self.sim = StaleDropdown()
        self.sim.payments.append(PaymentRow("1", "Bank Transfer", "Credit transfer", Decimal("0"), 0, 0))
        self.run_flow()
        self.assertNotIn("create_payment_term", self.sim.calls)

    def test_duplicate_payment_dropdown_stops(self):
        row = PaymentRow("1", "Bank Transfer", "Credit transfer", Decimal("0"), 0, 0)
        self.sim.payments = [row, replace(row, id="2")]
        with self.assertRaisesRegex(ReviewRequired, "ambiguous_payment_dropdown"):
            self.run_flow()
        self.assertNotIn("save_debtor", self.sim.calls)
