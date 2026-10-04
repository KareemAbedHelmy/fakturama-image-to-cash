import unittest
from types import SimpleNamespace
from unittest import mock

from fakturama_automation import order_address_ui as address
from fakturama_automation.order_finalize_ui import handle_save_contact_selector
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.selector_vision import OcrWord
from test_order_finalize import SOURCE, BILLING

DELIVERY = 'Northstar Office Warehouse\r\nBeusselstrasse 44\r\nDE-10553 Berlin\r\nGermany'


class Tab:
    def __init__(self, name, state):
        self.element_info = SimpleNamespace(name=name)
        self.state, self.iface_selection_item = state, self
        self.selections = 0

    @property
    def CurrentIsSelected(self):
        return self.state['name'] == self.element_info.name

    def Select(self):
        self.state['name'] = self.element_info.name
        self.selections += 1


class AddressTests(unittest.TestCase):
    def test_full_address_matching_rejects_wrong_recipient_and_partial_postcode(self):
        self.assertTrue(address.address_matches(BILLING, SOURCE.debtor.billing, SOURCE.debtor))
        for text in (DELIVERY, BILLING.replace('10117', '101170'), BILLING.replace('Marta', 'Other'),
                     BILLING.replace('Germany', 'France')):
            self.assertFalse(address.address_matches(text, SOURCE.debtor.billing, SOURCE.debtor))

    def run_addresses(self, billing=BILLING):
        state = {'name': 'Delivery address'}
        tabs = [Tab(name, state) for name in ('Invoice address', 'Delivery address')]
        self.state, self.tabs = state, tabs
        def read(_):
            return {'values': {'address_text': billing if state['name'] == 'Invoice address' else DELIVERY}}
        with mock.patch.object(address, 'unique_control', return_value=object()), \
                mock.patch.object(address, '_tab_items', return_value=tabs), \
                mock.patch('fakturama_automation.order_finalize_ui._snapshot', return_value=(None, None)), \
                mock.patch('fakturama_automation.order_finalize_ui.read_document', side_effect=read):
            return address.verify_order_addresses(object(), SOURCE, lambda: None)

    def test_both_pages_verified_and_invoice_selected_at_end(self):
        result = self.run_addresses()
        self.assertTrue(result['billing_address_verified'])
        self.assertTrue(result['delivery_address_verified'])
        self.assertEqual(self.state['name'], 'Invoice address')

    def test_mismatch_stops_and_returns_to_invoice(self):
        with self.assertRaises(ReviewRequired):
            self.run_addresses(billing=DELIVERY)
        self.assertEqual(self.state['name'], 'Invoice address')

    def test_already_selected_does_not_select_again(self):
        state = {'name': 'Invoice address'}
        tab = Tab('Invoice address', state)
        with mock.patch.object(address, 'unique_control'), \
                mock.patch.object(address, '_tab_items', return_value=[tab]):
            address.select_address_tab(object(), 'Invoice address', lambda: None)
        self.assertEqual(tab.selections, 0)

    def test_save_popup_uses_existing_selector_once(self):
        root = SimpleNamespace(element_info=SimpleNamespace(name='Fakturama - test', process_id=123))
        diagnostics = {}
        with mock.patch('fakturama_automation.selector_live_ui._windows', return_value=[object()]), \
                mock.patch('fakturama_automation.selector_live_ui.SelectorDriver'), \
                mock.patch('fakturama_automation.selector_live_ui.check_selector_scope',
                           return_value={'status': 'SELECTED_EXISTING'}) as select:
            self.assertTrue(handle_save_contact_selector(root, SOURCE, diagnostics))
            self.assertTrue(select.call_args.kwargs['existing_dialog'])
            self.assertEqual(select.call_args.args[2], SOURCE.debtor.company)
            with self.assertRaises(ReviewRequired):
                handle_save_contact_selector(root, SOURCE, diagnostics)
            self.assertEqual(select.call_count, 1)

    def test_no_popup_does_nothing(self):
        with mock.patch('fakturama_automation.selector_live_ui._windows', return_value=[]):
            self.assertFalse(handle_save_contact_selector(object(), SOURCE, {}))

    def test_tab_caption_uses_current_pixels_and_tolerates_observed_confidence(self):
        words = [OcrWord('Invoice', 10, 17, 91, 22, 97),
                 OcrWord('address', 111, 16, 101, 23, 64)]
        self.assertEqual(address.address_tab_point(words, 'Invoice address'), (111, 28))
        with self.assertRaises(ReviewRequired):
            address.address_tab_point(words, 'Delivery address')

    def test_contact_delivery_heading_does_not_change_destination_verification(self):
        text = DELIVERY.replace('Northstar Office Warehouse', 'Northstar Office GmbH\r\nMarta Klein')
        self.assertTrue(address.address_matches(text, SOURCE.debtor.delivery, check_recipient=False))
        self.assertFalse(address.address_matches(text, SOURCE.debtor.delivery))

    def test_save_popup_no_match_stops(self):
        root = SimpleNamespace(element_info=SimpleNamespace(name='Fakturama - test', process_id=123))
        with mock.patch('fakturama_automation.selector_live_ui._windows', return_value=[object()]), \
                mock.patch('fakturama_automation.selector_live_ui.SelectorDriver'), \
                mock.patch('fakturama_automation.selector_live_ui.check_selector_scope',
                           return_value={'status': 'NOT_FOUND'}):
            with self.assertRaises(ReviewRequired):
                handle_save_contact_selector(root, SOURCE, {})


if __name__ == '__main__':
    unittest.main()
