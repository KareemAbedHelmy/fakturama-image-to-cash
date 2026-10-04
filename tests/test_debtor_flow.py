import unittest
from pathlib import Path
from unittest import mock

from fakturama_automation import debtor_flow_ui
from fakturama_automation.input_io import read_order


SOURCE = read_order(Path(__file__).resolve().parents[1] / 'examples' / 'order.provided.expected.json')


class DebtorFlowTests(unittest.TestCase):
    def test_role_opener_uses_native_click_and_observes_child_popup(self):
        popup = mock.Mock()
        popup.element_info.class_name = '#32770'
        popup.is_visible.return_value = True
        root = mock.Mock()
        root.children.side_effect = [[], [popup]]
        button = mock.Mock()
        progress = {'role_popup_opens_attempted': 0}
        with mock.patch.object(debtor_flow_ui, '_role_controls', return_value=(mock.Mock(), button)):
            debtor_flow_ui._open_role_popup(root, 'Main address', lambda: None, progress)
        button.click_input.assert_called_once_with()
        button.iface_invoke.Invoke.assert_not_called()
        self.assertEqual(progress['role_popup_opens_attempted'], 1)

    def test_role_closer_handles_checkbox_map_and_confirms_committed_text(self):
        popup = mock.Mock()
        popup.handle = 1234
        popup.element_info.class_name = '#32770'
        popup.is_visible.return_value = True
        root = mock.Mock()
        root.children.side_effect = [[popup], []]
        boxes = {'Invoice address': mock.Mock(), 'Delivery address': mock.Mock()}
        field = mock.Mock()
        progress = {'role_popup_closes_attempted': 0}
        with (mock.patch.object(debtor_flow_ui, '_popup', return_value=boxes),
              mock.patch.object(debtor_flow_ui, '_states', return_value={
                  'Invoice address': 1, 'Delivery address': 0}),
              mock.patch.object(debtor_flow_ui, '_role_controls', return_value=(field, mock.Mock())),
              mock.patch.object(debtor_flow_ui, '_value', return_value='Invoice address'),
              mock.patch('win32gui.GetForegroundWindow', return_value=1234),
              mock.patch('pywinauto.keyboard.send_keys') as send):
            text = debtor_flow_ui._close_role_popup(root, 'Main address',
                {'Invoice address': 1, 'Delivery address': 0}, lambda: None, progress)
        send.assert_called_once()
        self.assertEqual(text, 'Invoice address')
        self.assertEqual(progress['role_popup_closes_attempted'], 1)

    def test_separate_delivery_prepares_both_roles_and_misc_without_save(self):
        calls = []
        fake_editor = mock.Mock()
        fake_editor.generated_id = 'CUST000010'

        def page(_root, _source, _id, page, _guard, diagnostics):
            calls.append(page)
            return {'values': {'payment': SOURCE.payment.method}} if page == 'misc' else {'values': {}}

        def close(_root, tab, roles, _guard, _progress):
            calls.append('close:' + tab)
            self.assertEqual(sum(roles.values()), 1)
            return next(k for k, v in roles.items() if v)

        with (mock.patch.object(debtor_flow_ui, 'DebtorMainAddressSession', return_value=fake_editor),
              mock.patch.object(debtor_flow_ui, 'DeliveryAddressSession', return_value=fake_editor),
              mock.patch.object(debtor_flow_ui, '_read_prepared_main', return_value={
                  'status': 'MAIN_FIELDS_ALREADY_MATCH_SOURCE'}),
              mock.patch.object(debtor_flow_ui, 'prepare_debtor_page_scope', side_effect=page),
              mock.patch.object(debtor_flow_ui, 'prepare_billing_role_scope', return_value={}),
              mock.patch.object(debtor_flow_ui, 'prepare_delivery_scope', return_value={}),
              mock.patch.object(debtor_flow_ui, '_open_role_popup'),
              mock.patch.object(debtor_flow_ui, '_close_role_popup', side_effect=close),
              mock.patch.object(debtor_flow_ui, '_add_delivery_tab'),
              mock.patch.object(debtor_flow_ui, 'unique_control', return_value=mock.Mock()),
              mock.patch.object(debtor_flow_ui, 'select_tab') as select):
            result = debtor_flow_ui.prepare_new_debtor_scope(mock.Mock(), SOURCE,
                                                              'CUST000010', lambda: None)
        self.assertEqual(result['status'], 'NEW_DEBTOR_PREPARED_UNSAVED')
        self.assertFalse(result['save_invoked'])
        self.assertEqual(calls, ['close:Main address', 'close:additional address #1', 'misc'])
        select.assert_called_once()


if __name__ == '__main__':
    unittest.main()
