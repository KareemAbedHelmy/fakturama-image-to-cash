import unittest
from pathlib import Path

from fakturama_automation.documents_ui import parse_document_rows, verify_document_row
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.input_io import read_order


SOURCE = read_order(Path(__file__).resolve().parents[1] / 'examples' / 'order.provided.expected.json')
ORDER = ('ICON_ORDER\tPO000004\tTue Jul 14 00:00:00 EEST 2026\t'
         'Northstar Office GmbH, Marta Klein\tWEB-2026-0714-A17\t'
         'COMMAND_ORDER_PENDING\t678.3\tnull')
INVOICE = ('ICON_INVOICE\tINV000004\tSun Oct 04 00:00:00 EEST 2026\t'
           'Northstar Office GmbH, Marta Klein\tWEB-2026-0714-A17\t'
           'COMMAND_CHECKED\t678.3\tnull')


class DocumentsRowTests(unittest.TestCase):
    def test_observed_order_and_invoice_rows(self):
        order = verify_document_row(parse_document_rows(ORDER), SOURCE, 'PO000004',
                                    'order', SOURCE.order_date)
        self.assertEqual(order['state'], 'COMMAND_ORDER_PENDING')
        invoice = verify_document_row(parse_document_rows(INVOICE), SOURCE, 'INV000004',
                                      'invoice', parse_document_rows(INVOICE)[0]['date'])
        self.assertEqual(invoice['total'], '678.3')

    def test_duplicate_number_and_wrong_state_stop(self):
        with self.assertRaises(ReviewRequired):
            verify_document_row(parse_document_rows(ORDER + '\n' + ORDER), SOURCE,
                                'PO000004', 'order', SOURCE.order_date)
        with self.assertRaises(ReviewRequired):
            verify_document_row(parse_document_rows(ORDER.replace('COMMAND_ORDER_PENDING',
                                                                  'COMMAND_CHECKED')),
                                SOURCE, 'PO000004', 'order', SOURCE.order_date)

    def test_changed_copy_schema_stops(self):
        with self.assertRaises(ReviewRequired):
            parse_document_rows('PO000004\t678.3')


if __name__ == '__main__':
    unittest.main()
