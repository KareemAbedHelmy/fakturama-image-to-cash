import unittest
from pathlib import Path

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.payment_table import inspect_copied_payment_rows, parse_payment_table


class PaymentTableTests(unittest.TestCase):
    def test_actual_windows_copy_parses_all_six_cells_without_inferred_code_or_absence(self):
        text = (Path(__file__).parent / 'fixtures/payment-list.copy.windows.tsv').read_text()
        report = inspect_copied_payment_rows(text, 'Bank Transfer')
        self.assertEqual(report['rows'], [{'standard': True, 'name': 'Cash', 'description': 'Pay Cash',
                                          'discount_raw': '0.0', 'discount_days': 0, 'net_days': 0}])
        self.assertEqual(report['lookup_result'], 'NO_EXACT_NAME_IN_COPY')
        for key in ['clipboard_provenance_verified', 'all_rows_enumerated', 'payment_code_verified',
                    'master_absence_verified', 'persistence_verified']:
            self.assertFalse(report[key])

    def test_preserves_multiple_rows_literal_text_and_windows_line_endings(self):
        text = 'true\tCash\tPay Cash\t0.0\t0\t0\r\nfalse\tBank Transfer\tBank Transfer\t0.00\t0\t0\r\n'
        report = inspect_copied_payment_rows(text, 'Bank Transfer')
        self.assertEqual(report['exact_name_count'], 1)
        self.assertEqual(report['rows'][1]['discount_raw'], '0.00')
        row = parse_payment_table('false\t%{F4} ^a\t"literal"\t0.0\t0\t0')[0]
        self.assertEqual(row.name, '%{F4} ^a')
        self.assertEqual(row.description, '"literal"')

    def test_duplicate_exact_rows_or_nonzero_definition_stop_for_review(self):
        row = 'false\tBank Transfer\tBank Transfer\t0.0\t0\t0'
        with self.assertRaisesRegex(ReviewRequired, 'ambiguous_payment'):
            inspect_copied_payment_rows(row + '\n' + row, 'Bank Transfer')
        for cells in [('0.01', '0', '0'), ('0.0', '5', '0'), ('0.0', '0', '30')]:
            with self.subTest(cells=cells), self.assertRaisesRegex(ReviewRequired, 'conflicting_payment_definition'):
                inspect_copied_payment_rows('false\tBank Transfer\tBank Transfer\t' + '\t'.join(cells), 'Bank Transfer')

    def test_no_fuzzy_or_case_insensitive_name_matching(self):
        for name in ['bank transfer', 'BankTransfer', ' Bank Transfer', 'Bank Transfer ']:
            report = inspect_copied_payment_rows('false\t' + name + '\tBank Transfer\t0.0\t0\t0', 'Bank Transfer')
            self.assertEqual(report['exact_name_count'], 0)
            self.assertFalse(report['master_absence_verified'])

    def test_bad_column_schema_partial_rows_controls_and_numbers_are_never_dropped(self):
        for text in ['', '\n', 'Cash\tPay Cash', 'true\tCash\tPay Cash\t0.0\t0\t0\n\n',
                     'true\tCash\tPay Cash\t0.0\t0\t0\nBAD',
                     'TRUE\tCash\tPay Cash\t0.0\t0\t0',
                     'true\t\tPay Cash\t0.0\t0\t0',
                     'true\tCash\tPay\x00Cash\t0.0\t0\t0',
                     'true\tCash\tPay\u202eCash\t0.0\t0\t0',
                     'true\tCash\tPay Cash\tNaN\t0\t0',
                     'true\tCash\tPay Cash\t1e2\t0\t0',
                     'true\tCash\tPay Cash\t0,0\t0\t0',
                     'true\tCash\tPay Cash\t0%\t0\t0',
                     'true\tCash\tPay Cash\t0.0\t-1\t0',
                     'true\tCash\tPay Cash\t0.0\t0.0\t0']:
            with self.subTest(text=text), self.assertRaises(ReviewRequired): parse_payment_table(text)

    def test_untrusted_text_row_and_field_size_bounds_and_unsupported_method(self):
        for text in [None, '\ud800', 'x' * (1024 * 1024 + 1),
                     ('true\tCash\tPay Cash\t0.0\t0\t0\n' * 1001),
                     'true\t' + 'x' * 1001 + '\tPay Cash\t0.0\t0\t0']:
            with self.assertRaises(ReviewRequired): parse_payment_table(text)
        with self.assertRaisesRegex(ReviewRequired, 'unsupported_method'):
            inspect_copied_payment_rows('true\tCash\tPay Cash\t0.0\t0\t0', 'Other')
