import unittest
from pathlib import Path
from unittest import mock

from fakturama_automation import invoice_ui
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.input_io import read_order


SOURCE = read_order(Path(__file__).resolve().parents[1] / 'examples' / 'order.provided.expected.json')


class InvoicePaymentMethodTests(unittest.TestCase):
    def state(self, method):
        return {'doc': {'editor_name': 'New Invoice', 'tabs': ['*New Invoice'], 'values': {
            'number': 'INV000010', 'date_text': '14 Jul 2026',
            'reference': SOURCE.external_reference, 'price_mode': 'Net', 'vat_mode': 'With VAT',
            'subtotal_text': '570,00 EUR', 'vat_total_text': '108,30 EUR',
            'total_text': '678,30 EUR'}}, 'paid': True, 'payment': method,
            'paid_date_text': '18 Jul 2026', 'value_text': '678,30 EUR'}

    def test_selects_exact_available_method_before_reporting_ready(self):
        current = ['Cash']
        controls = {'payment': mock.Mock(handle=45), 'editor': mock.Mock(),
                    'box': mock.Mock(), 'reference': mock.Mock(), 'paid_date': mock.Mock()}
        controls['editor'].handle = 46

        def state(_):
            return self.state(current[0]), controls

        def select(_scope, _control, method, guard):
            self.assertEqual(method, SOURCE.payment.method)
            guard()
            current[0] = method
            guard()

        with (mock.patch.object(invoice_ui, '_state', side_effect=state),
              mock.patch.object(invoice_ui, 'read_combo_options', return_value={
                  'options': ['Cash', SOURCE.payment.method]}),
              mock.patch.object(invoice_ui, 'select_combo_exact', side_effect=select) as action):
            result = invoice_ui.pay_invoice_scope(mock.Mock(), SOURCE, lambda: None)
        self.assertEqual(result['status'], 'INVOICE_PAYMENT_APPLIED_UNSAVED')
        self.assertEqual(result['payment'], SOURCE.payment.method)
        action.assert_called_once()

    def test_missing_method_stops_before_selection(self):
        controls = {'payment': mock.Mock(handle=45), 'editor': mock.Mock()}
        with (mock.patch.object(invoice_ui, '_state', return_value=(self.state('Cash'), controls)),
              mock.patch.object(invoice_ui, 'read_combo_options', return_value={'options': ['Cash']}),
              mock.patch.object(invoice_ui, 'select_combo_exact') as action):
            with self.assertRaisesRegex(ReviewRequired, 'invoice_payment_method_missing_or_ambiguous'):
                invoice_ui.pay_invoice_scope(mock.Mock(), SOURCE, lambda: None)
        action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
