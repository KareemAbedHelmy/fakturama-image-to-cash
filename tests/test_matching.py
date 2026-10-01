import unittest
from dataclasses import replace
from decimal import Decimal

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.matching import (PAYMENT_CODES, DebtorRow, PaymentRow, ProductRow, VatRow,
                                          debtor_match, payment_match, product_match, vat_match, vat_name)
from test_domain_finance import source


class MatchingTests(unittest.TestCase):
    def test_debtor_exact_fields(self):
        d = source().debtor
        row = DebtorRow("1", d.company, d.first_name, d.last_name, d.billing.zip, d.billing.city)
        self.assertEqual(debtor_match([row], d), row)
        with self.assertRaisesRegex(ReviewRequired, "conflicting_debtor_identity"):
            debtor_match([replace(row, zip="99999")], d)
        with self.assertRaisesRegex(ReviewRequired, "ambiguous_debtor"):
            debtor_match([row, replace(row, id="2")], d)

    def test_sku_not_substring_or_case_fuzzy(self):
        rows = [ProductRow("1", "ABC-12", "Example", Decimal("19"))]
        self.assertIsNone(product_match(rows, "ABC-1"))
        self.assertIsNone(product_match(rows, "abc-12"))
        self.assertEqual(product_match(rows, "ABC-12"), rows[0])

    def test_duplicate_sku_blocks(self):
        row = ProductRow("1", "ABC", "Example", Decimal("19"))
        with self.assertRaisesRegex(ReviewRequired, "ambiguous_product"):
            product_match([row, replace(row, id="2")], "ABC")

    def test_vat_name_decimal_format(self):
        self.assertEqual(vat_name(Decimal("19.00")), "VAT 19%")
        self.assertEqual(vat_name(Decimal("7.50")), "VAT 7.5%")

    def test_vat_exact_name_value_code(self):
        row = VatRow("1", "VAT 19%", Decimal("19"), "S")
        self.assertEqual(vat_match([row], Decimal("19")), row)
        for conflicting in [replace(row, value=Decimal("7")), replace(row, code="Z")]:
            with self.assertRaisesRegex(ReviewRequired, "conflicting_vat_definition"):
                vat_match([conflicting], Decimal("19"))
        with self.assertRaisesRegex(ReviewRequired, "ambiguous_vat"):
            vat_match([row, replace(row, id="2")], Decimal("19"))

    def test_all_payment_mappings_and_conflicts(self):
        for method, code in PAYMENT_CODES.items():
            with self.subTest(method=method):
                row = PaymentRow("1", method, code, Decimal("0"), 0, 0)
                self.assertEqual(payment_match([row], method), row)
                for conflicting in [replace(row, code="Cash"), replace(row, net_days=30),
                                    replace(row, cash_discount=Decimal("2")), replace(row, discount_days=2)]:
                    with self.assertRaisesRegex(ReviewRequired, "conflicting_payment_definition"):
                        payment_match([conflicting], method)
                with self.assertRaisesRegex(ReviewRequired, "ambiguous_payment"):
                    payment_match([row, replace(row, id="2")], method)
