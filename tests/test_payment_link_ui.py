import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fakturama_automation.cli import main
from fakturama_automation.debtor_ui import DebtorMiscSession, _tab_items
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.payment_link_ui import link_debtor_payment, link_debtor_payment_scope
from fakturama_automation.saved_payment_ui import SavedPaymentSession
from test_debtor_ui import SelectionPattern
from test_selector_ui import Wrapper


class PaymentLinkTests(unittest.TestCase):
    def setUp(self):
        folder = Path(__file__).parent / 'fixtures'
        self.root = Wrapper(json.loads((folder/'bank-transfer-saved.uia.json').read_text()))
        self.debtor = Wrapper(json.loads((folder/'new-debtor-misc.uia.json').read_text()))
        self.all_controls = []
        for tree in (self.root, self.debtor):
            controls = [tree] + tree.descendants()
            for control in controls:
                control.handle = len(self.all_controls) + 1
                self.all_controls.append(control)
                control.iface_selection_item = SelectionPattern(control)
                control.iface_selection_item.Select = Mock(side_effect=lambda c=control: self.select_tab(c))
        self.root.handle = 658798
        self.guard = Mock()
        self.options = ['Pay Cash', 'Bank Transfer']
        self.index = 0
        self.debtor_fields = DebtorMiscSession(self.debtor).fields()
        # Latest live debtor-options report supersedes the older capture's '---'.
        self.debtor_fields['price_preference'].data['patterns']['value']['CurrentValue']='Gross'
        self.combo = self.debtor_fields['payment']
        self.native = SimpleNamespace(handle=self.combo.handle,
            element_info=SimpleNamespace(process_id=51052),
            class_name=lambda:'ComboBox',is_visible=lambda:True,is_enabled=lambda:True,
            item_count=lambda:len(self.options),item_texts=lambda:list(self.options),
            selected_index=lambda:self.index,window_text=lambda:self.options[self.index],
            select=Mock(side_effect=self.select_payment))
        self.factory=Mock(return_value=self.native)
        self.diagnostics={}
        # Exercise both transitions even though the historical Misc page was selected.
        self.misc=next(c for c in _tab_items(self.debtor) if c.element_info.name=='Miscellaneous')
        self.misc.data['patterns']['selection_item']['CurrentIsSelected']=0

    def select_tab(self, control):
        if control.element_info.name in {'New Debtor','*New Debtor'}:
            bank=next(c for c in _tab_items(self.root) if c.element_info.name=='Bank Transfer')
            bank.data['patterns']['selection_item']['CurrentIsSelected']=0
            self.root._children=self.debtor._children
            for c in self.root._children:c.container=self.root
            # Retained clean term tab remains present when the editor is inactive.
            bank.container=self.root
            self.root._children.append(bank)
        else:
            control.data['patterns']['selection_item']['CurrentIsSelected']=1

    def select_payment(self, index):
        self.index=index
        self.combo.data['patterns']['value']['CurrentValue']=self.options[index]

    def run_link(self, **kwargs):
        return link_debtor_payment_scope(self.root,'Bank Transfer','Cash','CUST000001',self.guard,
            native_factory=self.factory,diagnostics=self.diagnostics,**kwargs)

    def saved(self):return SavedPaymentSession(self.root,'Bank Transfer','Cash',self.guard)
    def assert_no_field_writes_or_save(self):
        self.assertEqual(sum(len(c.writes)+c.invocations for c in self.all_controls),0)

    def test_actual_reopened_capture_definition_is_read_only_and_limits_claims(self):
        report=self.saved().verify()
        self.assertEqual(report['values']['name'],'Bank Transfer')
        self.assertEqual(report['values']['code'],'Credit transfer ')
        self.assertEqual(report['standard_name'],'Cash')
        self.assertTrue(report['payment_definition_verified'])
        self.assertFalse(report['persistence_verified'])
        with self.assertRaisesRegex(ReviewRequired,'read_only_session'):self.saved().prepare('Bank Transfer')
        self.assert_no_field_writes_or_save()

    def test_links_unique_payment_preserves_id_and_all_other_values_unsaved(self):
        report=self.run_link()
        self.assertEqual(report['status'],'DEBTOR_PAYMENT_SELECTED_UNSAVED')
        self.assertEqual(report['generated_id'],'CUST000001')
        self.assertEqual(report['values']['payment'],'Bank Transfer')
        self.assertEqual(report['values']['price_preference'],'Gross')
        self.assertEqual(report['values']['alias'],'')
        self.assertEqual(report['values']['discount'],'0%')
        self.assertFalse(report['debtor_persistence_verified'])
        self.assertFalse(report['master_absence_verified'])
        self.assertFalse(report['save_reopen_lifecycle_verified'])
        self.assertEqual(self.diagnostics['progress']['tab_selections_attempted'],2)
        self.native.select.assert_called_once_with(1)
        self.assert_no_field_writes_or_save()

    def test_dirty_or_unselected_reopened_tab_stops_before_tab_or_combo_action(self):
        for mode in ('dirty','unselected'):
            self.setUp()
            tab=next(c for c in _tab_items(self.root) if c.element_info.name=='Bank Transfer')
            if mode=='dirty':tab.element_info.name='*Bank Transfer'
            else:tab.data['patterns']['selection_item']['CurrentIsSelected']=0
            with self.subTest(mode=mode),self.assertRaises(ReviewRequired):self.run_link()
            self.factory.assert_not_called()
            self.assertEqual(self.diagnostics['progress']['tab_selections_attempted'],0)

    def test_code_names_defaults_and_standard_conflicts_stop_before_any_selection(self):
        for role,value in [('name','Other'),('description','Other'),('code','Credit card '),
                           ('cash_discount','5%'),('discount_days','2'),('net_days','3'),('account','Account')]:
            self.setUp()
            self.saved().fields()[role].data['patterns']['value']['CurrentValue']=value
            with self.subTest(role=role),self.assertRaises(ReviewRequired):self.run_link()
            self.factory.assert_not_called()
        self.setUp()
        self.saved()._standard(self.saved()._pane()).data['patterns']['value']['CurrentValue']='Other'
        with self.assertRaisesRegex(ReviewRequired,'standard_mismatch'):self.run_link()

    def test_expected_generated_id_mismatch_stops_before_dropdown_access(self):
        field=next(c for c in self.debtor.descendants('Edit') if c.element_info.name=='Customer ID')
        field.data['patterns']['value']['CurrentValue']='CUST000002'
        with self.assertRaisesRegex(ReviewRequired,'generated_id_mismatch'):self.run_link()
        self.factory.assert_not_called()
        self.assert_no_field_writes_or_save()

    def test_missing_or_duplicate_method_reports_options_without_selecting(self):
        for options in (['Pay Cash'],['Pay Cash','Bank Transfer','Bank Transfer']):
            self.setUp();self.options=options
            with self.subTest(options=options),self.assertRaisesRegex(ReviewRequired,'method_missing_or_ambiguous'):self.run_link()
            self.assertEqual(self.diagnostics['observed_payment_options']['options'],options)
            self.native.select.assert_not_called()
            self.assert_no_field_writes_or_save()

    def test_dynamic_option_order_selects_current_derived_index(self):
        self.options=['Bank Transfer','Pay Cash'];self.index=1
        self.run_link()
        self.native.select.assert_called_once_with(0)

    def test_already_selected_method_needs_no_native_mutation(self):
        self.index=1
        self.combo.data['patterns']['value']['CurrentValue']='Bank Transfer'
        self.run_link()
        self.native.select.assert_not_called()
        self.assertEqual(self.diagnostics['progress']['payment_selections_attempted'],0)

    def test_uncertain_tab_action_is_not_retried_or_followed_by_combo_access(self):
        tab=next(c for c in _tab_items(self.root) if c.element_info.name=='*New Debtor')
        original=tab.iface_selection_item.Select.side_effect
        def uncertain():
            original();raise RuntimeError('PRIVATE')
        tab.iface_selection_item.Select.side_effect=uncertain
        with self.assertRaisesRegex(ReviewRequired,'tab_selection_uncertain'):self.run_link()
        tab.iface_selection_item.Select.assert_called_once()
        self.factory.assert_not_called()
        self.assert_no_field_writes_or_save()

    def test_unavailable_tab_pattern_stops_without_fallback(self):
        tab=next(c for c in _tab_items(self.root) if c.element_info.name=='*New Debtor')
        del tab.iface_selection_item
        with self.assertRaisesRegex(ReviewRequired,'selection_pattern_unavailable'):self.run_link()
        self.factory.assert_not_called()

    def test_uncertain_native_payment_action_is_not_retried(self):
        def uncertain(index):
            self.select_payment(index);raise RuntimeError('PRIVATE')
        self.native.select.side_effect=uncertain
        with self.assertRaisesRegex(ReviewRequired,'combo_selection_uncertain'):self.run_link()
        self.native.select.assert_called_once_with(1)
        self.assert_no_field_writes_or_save()

    def test_native_action_without_effect_fails_readback(self):
        self.native.select.side_effect=None
        with self.assertRaisesRegex(ReviewRequired,'selection_readback_mismatch'):self.run_link()
        self.native.select.assert_called_once_with(1)

    def test_other_debtor_values_cannot_change_during_selection(self):
        for role in ('alias','discount','price_preference'):
            self.setUp()
            field=self.debtor_fields[role]
            def changed(index):
                self.select_payment(index);field.data['patterns']['value']['CurrentValue']='Changed'
            self.native.select.side_effect=changed
            with self.subTest(role=role),self.assertRaisesRegex(ReviewRequired,'unrelated_debtor_values_changed'):self.run_link()
            self.native.select.assert_called_once_with(1)
            self.assert_no_field_writes_or_save()

    def test_generated_id_change_during_selection_is_detected(self):
        field=next(c for c in self.debtor.descendants('Edit') if c.element_info.name=='Customer ID')
        def changed(index):
            self.select_payment(index);field.data['patterns']['value']['CurrentValue']='CUST000002'
        self.native.select.side_effect=changed
        with self.assertRaisesRegex(ReviewRequired,'generated_id_changed'):self.run_link()
        self.native.select.assert_called_once_with(1)

    def test_guard_failure_prevents_all_actions(self):
        self.guard.side_effect=ReviewRequired('foreground_lost')
        with self.assertRaisesRegex(ReviewRequired,'foreground_lost'):self.run_link()
        self.factory.assert_not_called()
        self.assert_no_field_writes_or_save()

    def test_live_entrypoint_checks_foreground_executable_and_actual_native_binding(self):
        self.root.is_active=lambda:True
        def desktop(backend):
            wrapper=self.root if backend=='uia' else self.native
            return SimpleNamespace(window=lambda handle:SimpleNamespace(wrapper_object=lambda:wrapper))
        with patch.dict('sys.modules',{'pywinauto':SimpleNamespace(Desktop=desktop)}), \
             patch('platform.system',return_value='Windows'), \
             patch('fakturama_automation.payment_copy_ui.process_executable_name',return_value='Fakturama.exe') as identity:
            report=link_debtor_payment(51052,658798,'Bank Transfer','Cash','CUST000001')
            self.assertEqual(report['values']['payment'],'Bank Transfer')
            self.assertGreater(identity.call_count,2)
            self.native.select.assert_called_once_with(1)
            self.assert_no_field_writes_or_save()

    def test_live_entrypoint_blocks_wrong_executable_or_foreground_before_actions(self):
        for mode in ('executable','foreground'):
            self.setUp()
            self.root.is_active=lambda:mode!='foreground'
            desktop=SimpleNamespace(window=lambda handle:SimpleNamespace(wrapper_object=lambda:self.root))
            with patch.dict('sys.modules',{'pywinauto':SimpleNamespace(Desktop=lambda backend:desktop)}), \
                 patch('platform.system',return_value='Windows'), \
                 patch('fakturama_automation.payment_copy_ui.process_executable_name',return_value='other.exe' if mode=='executable' else 'Fakturama.exe'), \
                 self.subTest(mode=mode),self.assertRaises(ReviewRequired):
                link_debtor_payment(51052,658798,'Bank Transfer','Cash','CUST000001')
            self.native.select.assert_not_called()
            self.assert_no_field_writes_or_save()

    def assert_no_automatic_tab_actions(self):
        for control in self.all_controls:
            control.iface_selection_item.Select.assert_not_called()

    def operator_notification(self, event):
        if event['tab']=='New Debtor':
            tab=next(c for c in _tab_items(self.root) if c.element_info.name=='*New Debtor')
        else:
            tab=self.misc
        self.select_tab(tab)  # Simulates the operator, not a UIA Select call.

    def test_explicit_manual_tabs_verify_state_without_any_automatic_tab_action(self):
        events=[]
        def notify(event):
            events.append(event)
            self.operator_notification(event)
        report=self.run_link(manual_tabs=True,notify=notify)
        self.assertEqual([e['tab'] for e in events],['New Debtor','Miscellaneous'])
        self.assertTrue(all(e['timeout_seconds']==60 for e in events))
        self.assertEqual(report['tab_mode'],'manual')
        self.assertFalse(report['automatic_tab_navigation_verified'])
        self.assertEqual(report['values']['payment'],'Bank Transfer')
        self.assertEqual(self.diagnostics['progress']['tab_selections_attempted'],0)
        self.native.select.assert_called_once_with(1)
        self.assert_no_automatic_tab_actions()
        self.assert_no_field_writes_or_save()

    def test_manual_tabs_work_when_automatic_select_would_raise(self):
        for control in self.all_controls:
            control.iface_selection_item.Select.side_effect=RuntimeError('Unsupported')
        report=self.run_link(manual_tabs=True,notify=self.operator_notification)
        self.assertEqual(report['values']['payment'],'Bank Transfer')
        self.assert_no_automatic_tab_actions()

    def test_manual_timeout_stops_before_combo_access_without_mutation_or_retry(self):
        now=[0.0]
        events=[]
        def sleep(interval):now[0]+=interval
        with self.assertRaisesRegex(ReviewRequired,'manual_tab_timeout'):
            self.run_link(manual_tabs=True,notify=events.append,clock=lambda:now[0],sleep=sleep)
        self.assertEqual(len(events),1)
        self.assertGreaterEqual(now[0],60)
        self.factory.assert_not_called()
        self.assert_no_automatic_tab_actions()
        self.assert_no_field_writes_or_save()

    def test_manual_operator_wrong_tab_never_proceeds_to_dropdown(self):
        now=[0.0]
        def sleep(interval):now[0]+=30
        def notify(event):
            # No change to the expected New Debtor selection.
            unrelated=next(c for c in _tab_items(self.root) if c.element_info.name=='Cash')
            unrelated.data['patterns']['selection_item']['CurrentIsSelected']=1
        with self.assertRaisesRegex(ReviewRequired,'manual_tab_timeout'):
            self.run_link(manual_tabs=True,notify=notify,clock=lambda:now[0],sleep=sleep)
        self.factory.assert_not_called()
        self.assert_no_automatic_tab_actions()

    def test_manual_mode_without_notification_fails_before_any_action(self):
        with self.assertRaisesRegex(ReviewRequired,'manual_notification_required'):
            self.run_link(manual_tabs=True)
        self.factory.assert_not_called()
        self.assert_no_automatic_tab_actions()

    def test_manual_wait_still_checks_root_guard(self):
        def notify(event):self.guard.side_effect=ReviewRequired('foreground_lost')
        with self.assertRaisesRegex(ReviewRequired,'foreground_lost'):
            self.run_link(manual_tabs=True,notify=notify)
        self.factory.assert_not_called()
        self.assert_no_automatic_tab_actions()

    def test_uncertain_tab_exception_retains_type_and_hresult_without_message(self):
        tab=next(c for c in _tab_items(self.root) if c.element_info.name=='*New Debtor')
        exc=RuntimeError('PRIVATE message')
        exc.hresult=-2147467263
        tab.iface_selection_item.Select.side_effect=exc
        with self.assertRaisesRegex(ReviewRequired,'tab_selection_uncertain'):self.run_link()
        self.assertEqual(self.diagnostics['tab_action_diagnostic'],
                         {'role':'debtor','exception_type':'RuntimeError','hresult':-2147467263})
        self.assertNotIn('PRIVATE',json.dumps(self.diagnostics))
        tab.iface_selection_item.Select.assert_called_once()
        self.factory.assert_not_called()

    def test_missing_tab_select_callable_is_distinguished_before_possible_mutation(self):
        tab=next(c for c in _tab_items(self.root) if c.element_info.name=='*New Debtor')
        tab.iface_selection_item.Select=None
        with self.assertRaisesRegex(ReviewRequired,'tab_select_unavailable'):self.run_link()
        self.assertEqual(self.diagnostics['progress']['tab_selections_attempted'],0)
        self.factory.assert_not_called()

    def test_cli_manual_flag_records_intent_and_flushes_handoff_notice(self):
        with tempfile.TemporaryDirectory() as folder,patch('sys.stdout',new_callable=io.StringIO) as output:
            out=Path(folder)/'manual.json'
            def link(*args,**kwargs):
                intent=json.loads(out.with_name(out.name+'.intent.json').read_text())
                self.assertEqual(intent['tab_mode'],'manual')
                self.assertTrue(kwargs['manual_tabs'])
                cli_notify=kwargs['notify']
                def notify(event):
                    cli_notify(event)
                    self.operator_notification(event)
                result=self.run_link(manual_tabs=True,notify=notify)
                kwargs['diagnostics'].update(self.diagnostics)
                return result
            with patch('fakturama_automation.cli.link_debtor_payment',side_effect=link):
                self.assertEqual(main(self.args(out)+['--manual-tabs']),0)
            self.assertIn('WAITING_FOR_MANUAL_TAB_SELECTION',output.getvalue())
            report=json.loads(out.read_text())
            self.assertEqual(report['tab_mode'],'manual')
            self.assertFalse(report['automatic_tab_navigation_verified'])
            self.assert_no_automatic_tab_actions()

    def args(self,out):return ['payment-link-debtor','--pid','51052','--window-handle','658798',
        '--method','Bank Transfer','--expected-standard','Cash','--expected-id','CUST000001','--out',str(out)]

    def test_cli_records_intent_before_action_and_writes_unsaved_result(self):
        with tempfile.TemporaryDirectory() as folder,patch('sys.stdout',new_callable=io.StringIO):
            out=Path(folder)/'linked.json'
            def link(*args,**kwargs):
                self.assertFalse(json.loads(out.with_name(out.name+'.intent.json').read_text())['save_enabled'])
                kwargs['diagnostics'].update(self.diagnostics)
                result=self.run_link()
                kwargs['diagnostics'].update(self.diagnostics)
                return result
            with patch('fakturama_automation.cli.link_debtor_payment',side_effect=link):
                self.assertEqual(main(self.args(out)),0)
            self.assertEqual(json.loads(out.read_text())['status'],'DEBTOR_PAYMENT_SELECTED_UNSAVED')

    def test_cli_preserves_existing_evidence_or_intent_before_any_action(self):
        for existing in ('result','intent'):
            with tempfile.TemporaryDirectory() as folder,patch('sys.stderr',new_callable=io.StringIO),\
                 patch('fakturama_automation.cli.link_debtor_payment') as link:
                out=Path(folder)/'linked.json'
                path=out if existing=='result' else out.with_name(out.name+'.intent.json')
                path.write_text('Existing')
                self.assertNotEqual(main(self.args(out)),0)
                link.assert_not_called()
                self.assertEqual(path.read_text(),'Existing')

    def test_cli_unexpected_exception_is_sanitized_and_diagnostic_written(self):
        with tempfile.TemporaryDirectory() as folder,patch('sys.stderr',new_callable=io.StringIO) as errors,\
             patch('fakturama_automation.cli.link_debtor_payment',side_effect=RuntimeError('PRIVATE')):
            out=Path(folder)/'linked.json'
            self.assertEqual(main(self.args(out)),2)
            self.assertEqual(json.loads(out.read_text())['code'],'payment_link_unexpected_failure')
            self.assertNotIn('PRIVATE',out.read_text()+errors.getvalue())
            self.assertTrue(out.with_name(out.name+'.intent.json').exists())


if __name__=='__main__':unittest.main()
