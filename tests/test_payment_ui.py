import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fakturama_automation.cli import main
from fakturama_automation.combo_ui import select_combo_exact
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.payment_ui import CODE_LABEL, PaymentEditorSession, prepare_payment_editor
from test_debtor_ui import SelectionPattern
from test_selector_ui import Wrapper


class PaymentEditorTests(unittest.TestCase):
    def setUp(self):
        self.root=Wrapper(json.loads((Path(__file__).parent/'fixtures/new-payment-term.uia.json').read_text()))
        for i,c in enumerate([self.root]+self.root.descendants()):
            c.handle=i+1
            c.iface_selection_item=SelectionPattern(c)
        self.session=PaymentEditorSession(self.root)
        self.code=self.session.fields()['code']
        # These option labels model the expected code mapping, not live evidence.
        self.options=['Mutually defined ','Credit transfer ','Credit card ','SEPA direct debit ']
        self.index=0
        def select(index):
            self.index=index
            self.code.data['patterns']['value']['CurrentValue']=self.options[index]
        self.native=SimpleNamespace(handle=self.code.handle,
            element_info=SimpleNamespace(process_id=51052),
            class_name=lambda:'ComboBox',is_visible=lambda:True,is_enabled=lambda:True,
            item_count=Mock(side_effect=lambda:len(self.options)),
            item_texts=Mock(side_effect=lambda:list(self.options)),
            selected_index=Mock(side_effect=lambda:self.index),
            window_text=Mock(side_effect=lambda:self.options[self.index]),select=Mock(side_effect=select))
        self.factory=Mock(return_value=self.native)

    def value(self, role, value):
        self.session.fields()[role].data['patterns']['value']['CurrentValue']=value

    def writes(self): return sum(len(c.writes) for c in self.root.descendants())
    def invocations(self): return sum(c.invocations for c in self.root.descendants())

    @contextmanager
    def live_context(self):
        observed=json.loads((Path(__file__).parent/'fixtures/payment-code-options.windows.json').read_text())
        self.options=observed['payment_code_options']['options']
        self.index=observed['payment_code_options']['selected_index']
        self.root.handle=658798
        self.root.is_active=lambda:True
        def desktop(backend):
            wrapper=self.root if backend=='uia' else self.native
            return SimpleNamespace(window=lambda handle:SimpleNamespace(wrapper_object=lambda:wrapper))
        with patch.dict('sys.modules',{'pywinauto':SimpleNamespace(Desktop=desktop)}), \
             patch('platform.system',return_value='Windows'), \
             patch('fakturama_automation.payment_ui.process_executable_name',return_value='Fakturama.exe') as identity:
            yield identity

    def test_capture_fields_include_opaque_code_label_and_read_only_standard(self):
        report=self.session.code_options(self.factory)
        self.assertEqual(report['standard_name'],'Cash')
        self.assertEqual(report['values']['code'],'Mutually defined ')
        self.assertEqual(report['values']['cash_discount'],'0%')
        self.assertEqual(report['values']['discount_days'],'0')
        self.assertEqual(report['values']['net_days'],'0')
        self.assertTrue(report['source_order_tab_present'])
        self.assertTrue(report['source_debtor_tab_present'])
        self.assertFalse(report['source_order_number_verified'])
        self.assertFalse(report['source_debtor_id_verified'])
        self.assertEqual(self.code.element_info.name,CODE_LABEL)
        self.assertEqual(self.writes()+self.invocations(),0)
        self.native.select.assert_not_called()

    def test_all_supported_methods_prepare_unsaved_and_preserve_standard(self):
        for method,index in [('Bank Transfer',1),('Credit Card',2),('SEPA Direct Debit',3)]:
            self.setUp()
            with self.subTest(method=method):
                report=self.session.prepare(method,self.factory)
                self.assertEqual(report['status'],'PAYMENT_EDITOR_PREPARED_UNSAVED')
                self.assertEqual(report['values']['name'],method)
                self.assertEqual(report['values']['description'],method)
                self.assertEqual(report['values']['code'],self.options[index])
                self.assertEqual(report['standard_name'],'Cash')
                self.native.select.assert_called_once_with(index)
                self.assertEqual(self.writes(),2)
                self.assertEqual(self.invocations(),0)
                for role in ['account','cash_discount','discount_days','net_days','unpaid_text','deposit_text','paid_text']:
                    self.assertEqual(self.session.fields()[role].writes,[])

    def test_existing_exact_values_skip_selection_and_writes(self):
        self.index=1
        self.value('code',self.options[1])
        self.value('name','Bank Transfer')
        self.value('description','Bank Transfer')
        self.session.prepare('Bank Transfer',self.factory)
        self.native.select.assert_not_called()
        self.assertEqual(self.writes()+self.invocations(),0)

    def test_dirty_tab_caption_is_accepted_without_fuzzy_matching(self):
        tab=next(c for c in self.root.descendants('TabItem') if c.element_info.name=='New Term of Payment')
        tab.element_info.name='*New Term of Payment'
        self.assertEqual(self.session.prepare('Bank Transfer',self.factory)['standard_name'],'Cash')
        tab.element_info.name='**New Term of Payment'
        with self.assertRaisesRegex(ReviewRequired,'tab_missing_or_ambiguous'):
            self.session.observe()

    def test_conflicts_and_nonzero_defaults_block_before_any_mutation(self):
        for role,value in [('name','Existing term'),('description','Other'),('cash_discount','2%'),
                           ('cash_discount','NaN%'),('discount_days','5'),('net_days','30'),
                           ('net_days','-1'),('account','Account 7'),('paid_text','Template')]:
            self.setUp()
            self.value(role,value)
            with self.subTest(role=role,value=value):
                with self.assertRaises(ReviewRequired):self.session.prepare('Bank Transfer',self.factory)
                self.factory.assert_not_called()
                self.native.select.assert_not_called()
                self.assertEqual(self.writes()+self.invocations(),0)

    def test_read_only_field_blocks_before_code_selection_or_earlier_writes(self):
        self.session.fields()['description'].data['patterns']['value']['CurrentIsReadOnly']=1
        with self.assertRaisesRegex(ReviewRequired,'field_read_only'):
            self.session.prepare('Bank Transfer',self.factory)
        self.factory.assert_not_called()
        self.assertEqual(self.writes(),0)

    def test_duplicate_code_or_missing_exact_case_stops_without_selection(self):
        for labels in [['Credit transfer ','Credit transfer'],['credit transfer'],['Credit transfer\t'],[]]:
            self.setUp()
            self.options=['Mutually defined ']+labels
            with self.subTest(labels=labels):
                with self.assertRaises(ReviewRequired):self.session.prepare('Bank Transfer',self.factory)
                self.native.select.assert_not_called()
                self.assertEqual(self.writes()+self.invocations(),0)

    def test_unsupported_method_stops_before_native_access(self):
        with self.assertRaisesRegex(ReviewRequired,'unsupported_method'):
            self.session.prepare('Wire',self.factory)
        self.factory.assert_not_called()

    def test_standard_or_editor_identity_change_blocks_writes(self):
        for mode in ['standard','editor','read_only']:
            self.setUp()
            if mode=='editor':self.session._pane().handle+=5000
            else:
                standard=self.session._standard(self.session._pane())
                key='CurrentValue' if mode=='standard' else 'CurrentIsReadOnly'
                standard.data['patterns']['value'][key]='Other' if mode=='standard' else 0
            with self.subTest(mode=mode):
                with self.assertRaises(ReviewRequired):self.session.prepare('Bank Transfer',self.factory)
                self.factory.assert_not_called()
                self.assertEqual(self.writes()+self.invocations(),0)

    def test_retained_order_and_debtor_tabs_must_be_unique_and_present(self):
        for name in ['*New Order','*New Debtor']:
            self.setUp()
            tab=next(c for c in self.root.descendants('TabItem') if c.element_info.name==name)
            tab.parent()._children.remove(tab)
            with self.subTest(name=name):
                with self.assertRaises(ReviewRequired):self.session.prepare('Bank Transfer',self.factory)
                self.factory.assert_not_called()
                self.assertEqual(self.writes(),0)

    def test_uncertain_selection_is_not_retried_or_followed_by_field_writes(self):
        original=self.native.select.side_effect
        def uncertain(index):
            original(index)
            raise RuntimeError('PRIVATE')
        self.native.select.side_effect=uncertain
        with self.assertRaisesRegex(ReviewRequired,'combo_selection_uncertain'):
            self.session.prepare('Bank Transfer',self.factory)
        with self.assertRaisesRegex(ReviewRequired,'write_uncertain_reconcile'):
            self.session.prepare('Bank Transfer',self.factory)
        self.native.select.assert_called_once_with(1)
        self.assertEqual(self.writes()+self.invocations(),0)

    def test_uncertain_first_write_blocks_later_writes_and_session_reuse(self):
        name=self.session.fields()['name']
        name.fail_after_write=True
        with self.assertRaisesRegex(ReviewRequired,'payment_editor_write_uncertain'):
            self.session.prepare('Bank Transfer',self.factory)
        with self.assertRaisesRegex(ReviewRequired,'write_uncertain_reconcile'):
            self.session.prepare('Bank Transfer',self.factory)
        self.assertEqual(name.writes,['Bank Transfer'])
        self.assertEqual(self.session.fields()['description'].writes,[])
        self.assertEqual(self.invocations(),0)

    def test_write_readback_mismatch_blocks_remaining_fields(self):
        self.session.fields()['name'].iface_value.SetValue=Mock()
        with self.assertRaisesRegex(ReviewRequired,'write_readback_mismatch'):
            self.session.prepare('Bank Transfer',self.factory)
        self.assertEqual(self.session.fields()['description'].writes,[])

    def test_nonzero_days_after_selection_stop_before_name_write(self):
        original=self.native.select.side_effect
        def changed(index):
            original(index)
            self.value('net_days','30')
        self.native.select.side_effect=changed
        with self.assertRaisesRegex(ReviewRequired,'nonzero_or_invalid_days'):
            self.session.prepare('Bank Transfer',self.factory)
        self.assertEqual(self.writes()+self.invocations(),0)

    def test_cli_live_entrypoint_writes_report_without_selection(self):
        self.root.handle=658798
        def desktop(backend):
            wrapper=self.root if backend=='uia' else self.native
            return SimpleNamespace(window=lambda handle:SimpleNamespace(wrapper_object=lambda:wrapper))
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules',{'pywinauto':SimpleNamespace(Desktop=desktop)}), \
             patch('platform.system',return_value='Windows'),patch('sys.stdout',new_callable=io.StringIO) as output:
            out=Path(folder)/'payment.json'
            self.assertEqual(main(['payment-editor','--pid','51052','--window-handle','658798','--out',str(out)]),0)
            report=json.loads(out.read_text())
            self.assertEqual(report['standard_name'],'Cash')
            self.assertIn('READ_ONLY_PAYMENT_EDITOR_WRITTEN',output.getvalue())
            self.native.select.assert_not_called()
            self.assertEqual(self.writes()+self.invocations(),0)

    def test_cli_read_failure_writes_safe_diagnostic_and_returns_review(self):
        self.native.item_count.side_effect=RuntimeError('PRIVATE')
        self.root.handle=658798
        def desktop(backend):
            wrapper=self.root if backend=='uia' else self.native
            return SimpleNamespace(window=lambda handle:SimpleNamespace(wrapper_object=lambda:wrapper))
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules',{'pywinauto':SimpleNamespace(Desktop=desktop)}), \
             patch('platform.system',return_value='Windows'),patch('sys.stderr',new_callable=io.StringIO) as errors:
            out=Path(folder)/'payment.json'
            self.assertEqual(main(['payment-editor','--pid','51052','--window-handle','658798','--out',str(out)]),2)
            report=json.loads(out.read_text())
            self.assertEqual(report['code'],'combo_item_count_unavailable')
            self.assertNotIn('PRIVATE',out.read_text()+errors.getvalue())
            self.assertEqual(self.writes()+self.invocations(),0)

    def test_native_selection_does_not_use_fuzzy_label_matching(self):
        with self.assertRaisesRegex(ReviewRequired,'selection_missing_or_ambiguous'):
            select_combo_exact(self.session._pane(),self.code,'Credit transfer',self.factory)
        self.native.select.assert_not_called()

    def test_native_selection_stale_index_is_rejected_before_action(self):
        self.native.item_texts.side_effect=[list(self.options),list(self.options),
                                          ['Mutually defined ','Credit card ','Credit transfer ','SEPA direct debit '],
                                          ['Mutually defined ','Credit card ','Credit transfer ','SEPA direct debit ']]
        with self.assertRaisesRegex(ReviewRequired,'changed_before_selection'):
            select_combo_exact(self.session._pane(),self.code,'Credit transfer ',self.factory)
        self.native.select.assert_not_called()

    def test_native_selection_no_effect_is_not_retried(self):
        self.native.select.side_effect=None
        with self.assertRaisesRegex(ReviewRequired,'selection_readback_mismatch'):
            self.session.prepare('Bank Transfer',self.factory)
        self.native.select.assert_called_once_with(1)
        self.assertEqual(self.writes(),0)

    def test_native_selection_wrong_process_rejected_before_action(self):
        self.native.element_info.process_id=999
        with self.assertRaisesRegex(ReviewRequired,'scope_or_class_mismatch'):
            self.session.prepare('Bank Transfer',self.factory)
        self.native.select.assert_not_called()
        self.assertEqual(self.writes(),0)

    def test_changed_capture_ids_and_positions_do_not_change_field_binding(self):
        for i,c in enumerate(self.root.descendants()):
            c.element_info.automation_id='new-session-'+str(i)
            c.handle+=10000
            if 'rect' in c.data:c.data['rect']=[v+100 for v in c.data['rect']]
        session=PaymentEditorSession(self.root)
        self.assertEqual(session.observe()['standard_name'],'Cash')
        self.assertEqual(session.observe()['values']['name'],'')

    def test_unselected_editor_tab_and_duplicate_fields_block_native_access(self):
        for mode in ['selected','duplicate','process']:
            self.setUp()
            if mode=='selected':
                tab=next(c for c in self.root.descendants('TabItem') if c.element_info.name=='New Term of Payment')
                tab.data['patterns']['selection_item']['CurrentIsSelected']=0
            elif mode=='duplicate':
                name=self.session.fields()['name']
                name.parent()._children.append(Wrapper(name.data,name.parent()))
            else:self.session.fields()['description'].element_info.process_id=999
            with self.subTest(mode=mode):
                with self.assertRaises(ReviewRequired):self.session.prepare('Bank Transfer',self.factory)
                self.factory.assert_not_called()
                self.assertEqual(self.writes()+self.invocations(),0)

    def test_standard_change_after_selection_stops_before_name_write(self):
        standard=self.session._standard(self.session._pane())
        original=self.native.select.side_effect
        def changed(index):
            original(index)
            standard.data['patterns']['value']['CurrentValue']='Other'
        self.native.select.side_effect=changed
        with self.assertRaisesRegex(ReviewRequired,'standard_changed'):
            self.session.prepare('Bank Transfer',self.factory)
        self.assertEqual(self.writes()+self.invocations(),0)

    def test_code_change_during_name_write_blocks_description(self):
        name=self.session.fields()['name']
        original=name.iface_value.SetValue
        def changed(value):
            original(value)
            self.value('code','Mutually defined ')
        name.iface_value.SetValue=changed
        with self.assertRaisesRegex(ReviewRequired,'code_changed_before_write'):
            self.session.prepare('Bank Transfer',self.factory)
        self.assertEqual(self.session.fields()['description'].writes,[])

    def test_live_prepare_entrypoint_replays_observed_twelve_options_without_saving(self):
        with self.live_context():
            report=prepare_payment_editor(51052,658798,'Bank Transfer')
            self.assertEqual(report['status'],'PAYMENT_EDITOR_PREPARED_UNSAVED')
            self.assertEqual(report['values']['name'],'Bank Transfer')
            self.assertEqual(report['values']['description'],'Bank Transfer')
            self.assertEqual(report['payment_code_options']['current_value'],'Credit transfer ')
            self.assertEqual(report['standard_name'],'Cash')
            self.assertFalse(report['master_absence_verified'])
            self.assertFalse(report['persistence_verified'])
            self.assertEqual(self.writes(),2)
            self.assertEqual(self.invocations(),0)

    def test_standalone_prepare_rejects_partial_or_already_prepared_forms_before_mutation(self):
        for role,value in [('name','Bank Transfer'),('description','Bank Transfer'),('code','Credit transfer ')]:
            self.setUp()
            with self.live_context():
                self.value(role,value)
                if role=='code':self.index=1
                with self.subTest(role=role):
                    with self.assertRaisesRegex(ReviewRequired,'blank_form_required_reconcile'):
                        prepare_payment_editor(51052,658798,'Bank Transfer')
                    self.native.select.assert_not_called()
                    self.assertEqual(self.writes(),0)

    def test_write_entrypoint_requires_native_executable_and_foreground(self):
        for mode in ['exe','foreground','title']:
            self.setUp()
            with self.live_context() as identity:
                if mode=='exe':identity.return_value='other.exe'
                elif mode=='foreground':self.root.is_active=lambda:False
                else:self.root.element_info.name='Other workspace'
                with self.subTest(mode=mode):
                    with self.assertRaises(ReviewRequired):prepare_payment_editor(51052,658798,'Bank Transfer')
                    self.native.select.assert_not_called()
                    self.assertEqual(self.writes(),0)

    def test_foreground_loss_after_selection_stops_before_name_write(self):
        with self.live_context():
            original=self.native.select.side_effect
            def lose_foreground(index):
                original(index)
                self.root.is_active=lambda:False
            self.native.select.side_effect=lose_foreground
            with self.assertRaisesRegex(ReviewRequired,'must_remain_in_foreground'):
                prepare_payment_editor(51052,658798,'Bank Transfer')
            self.native.select.assert_called_once_with(1)
            self.assertEqual(self.writes(),0)

    def test_cli_records_intent_before_preparation_and_writes_unsaved_result(self):
        with self.live_context(),tempfile.TemporaryDirectory() as folder, \
             patch('sys.stdout',new_callable=io.StringIO) as output:
            out=Path(folder)/'prepared.json'
            original=self.native.select.side_effect
            def check_intent(index):
                intent=json.loads(out.with_name(out.name+'.intent.json').read_text())
                self.assertEqual(intent['status'],'PAYMENT_PREPARATION_INTENT')
                self.assertFalse(intent['save_enabled'])
                original(index)
            self.native.select.side_effect=check_intent
            self.assertEqual(main(['payment-prepare','--method','Bank Transfer','--pid','51052',
                                  '--window-handle','658798','--out',str(out)]),0)
            report=json.loads(out.read_text())
            self.assertEqual(report['status'],'PAYMENT_EDITOR_PREPARED_UNSAVED')
            self.assertEqual(report['values']['description'],'Bank Transfer')
            self.assertIn('PAYMENT_EDITOR_PREPARED_UNSAVED',output.getvalue())
            self.assertEqual(self.invocations(),0)

    def test_existing_output_or_intent_blocks_before_native_preparation(self):
        for existing in ['result','intent']:
            with tempfile.TemporaryDirectory() as folder, patch('sys.stderr',new_callable=io.StringIO), \
                 patch('fakturama_automation.cli.prepare_payment_editor') as prepare:
                out=Path(folder)/'prepared.json'
                path=out if existing=='result' else out.with_name(out.name+'.intent.json')
                path.write_text('Existing evidence')
                self.assertNotEqual(main(['payment-prepare','--method','Bank Transfer','--pid','51052',
                                         '--window-handle','658798','--out',str(out)]),0)
                prepare.assert_not_called()
                self.assertEqual(path.read_text(),'Existing evidence')

    def test_uncertain_cli_mutation_keeps_intent_and_writes_sanitized_review(self):
        with self.live_context(),tempfile.TemporaryDirectory() as folder, \
             patch('sys.stderr',new_callable=io.StringIO) as errors:
            out=Path(folder)/'prepared.json'
            original=self.native.select.side_effect
            def uncertain(index):
                original(index)
                raise RuntimeError('PRIVATE exception text')
            self.native.select.side_effect=uncertain
            self.assertEqual(main(['payment-prepare','--method','Bank Transfer','--pid','51052',
                                  '--window-handle','658798','--out',str(out)]),2)
            self.assertTrue(out.with_name(out.name+'.intent.json').exists())
            report=json.loads(out.read_text())
            self.assertEqual(report['code'],'combo_selection_uncertain')
            self.assertNotIn('PRIVATE',out.read_text()+errors.getvalue())
            self.assertEqual(self.writes()+self.invocations(),0)

    def test_reordered_observed_labels_select_derived_index(self):
        with self.live_context():
            self.options[1],self.options[9]=self.options[9],self.options[1]
            report=prepare_payment_editor(51052,658798,'Bank Transfer')
            self.native.select.assert_called_once_with(9)
            self.assertEqual(report['payment_code_options']['current_value'],'Credit transfer ')
