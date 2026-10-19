import unittest
from decimal import Decimal

from fakturama_automation.errors import ReviewRequired
from pathlib import Path

from PIL import Image

from fakturama_automation.vat_ui import decide_vat, parse_vat_rows, row_center, row_lines


FIXTURES = Path(__file__).parent / 'fixtures'


class VatRowPositionTests(unittest.TestCase):
    def test_rows_are_located_from_the_rules_of_a_captured_grid(self):
        lines = row_lines(Image.open(FIXTURES / 'vat-grid-two-rows.png'))
        self.assertGreaterEqual(len(lines), 5)
        first, second = row_center(lines, 0), row_center(lines, 1)
        self.assertTrue(lines[0] < first < lines[1] < second < lines[2])

    def test_missing_rule_stops(self):
        with self.assertRaises(ReviewRequired):
            row_center([30], 0)


class VatRowTests(unittest.TestCase):
    def test_copy_rows_are_parsed(self):
        rows = parse_vat_rows('true\tVAT 19%\tVAT 19%\t0.19\r\nfalse\tVAT 7%\tVAT 7%\t0.07')
        self.assertEqual([r['name'] for r in rows], ['VAT 19%', 'VAT 7%'])
        self.assertTrue(rows[0]['standard'])
        self.assertEqual(rows[1]['value'], Decimal('0.07'))
        self.assertEqual(parse_vat_rows(''), [])

    def test_unexpected_schema_stops(self):
        for text in ('true\tVAT 19%\t0.19', 'maybe\ta\tb\t0.1', 'true\ta\tb\tx'):
            with self.subTest(text=text), self.assertRaises(ReviewRequired):
                parse_vat_rows(text)

    def test_existence_decision(self):
        rows = parse_vat_rows('true\tVAT 19%\tVAT 19%\t0.19')
        self.assertEqual(decide_vat(rows, Decimal('19')), 'EXISTS')
        self.assertEqual(decide_vat(rows, Decimal('7')), 'MISSING')

    def test_same_name_with_other_value_or_duplicates_stop(self):
        wrong = parse_vat_rows('true\tVAT 19%\tVAT 19%\t0.20')
        twice = parse_vat_rows('true\tVAT 19%\ta\t0.19\nfalse\tVAT 19%\tb\t0.19')
        for rows in (wrong, twice):
            with self.assertRaises(ReviewRequired):
                decide_vat(rows, Decimal('19'))


if __name__ == '__main__':
    unittest.main()
