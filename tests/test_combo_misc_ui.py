import json
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fakturama_automation.combo_ui import ComboReadRequired, read_combo_options
from fakturama_automation.debtor_ui import DebtorMiscSession
from fakturama_automation.debtor_ui import _tab_diagnostic, _tab_items
from fakturama_automation.domain import Debtor
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.input_io import read_order
from fakturama_automation.cli import main
from test_debtor_ui import SelectionPattern
from test_selector_ui import Wrapper


class MiscComboTests(unittest.TestCase):
    def setUp(self):
        tree=json.loads((Path(__file__).parent/'fixtures/new-debtor-misc.uia.json').read_text())
        self.root=Wrapper(tree)
        for i,c in enumerate([self.root]+self.root.descendants()):
            c.handle=i+1
            c.iface_selection_item=SelectionPattern(c)
        self.session=DebtorMiscSession(self.root)
        self.payment=self.session.fields()['payment']
        self.native=SimpleNamespace(handle=self.payment.handle,
            element_info=SimpleNamespace(process_id=51052),
            class_name=lambda:'ComboBox',is_visible=lambda:True,is_enabled=lambda:True,
            item_count=Mock(return_value=2),item_texts=Mock(return_value=['Pay Cash','Bank Transfer']),
            selected_index=Mock(return_value=0),window_text=Mock(return_value='Pay Cash'))
        self.factory=Mock(return_value=self.native)
        self.debtor=read_order(Path(__file__).parent.parent/'examples/order.provided.expected.json').debtor

    def test_actual_misc_fields_and_standard_combo_options_are_observed_without_mutation(self):
        r=self.session.payment_options(self.factory)
        self.assertEqual(r['generated_id'],'CUST000001')
        self.assertEqual(r['values'],{'alias':'','payment':'Pay Cash','discount':'0%','price_preference':'---'})
        self.assertEqual(r['payment_options']['options'],['Pay Cash','Bank Transfer'])
        self.assertFalse(r['source_order_number_verified'])
        self.factory.assert_called_once_with(self.payment.handle)
        self.assertEqual(sum(len(c.writes)+c.invocations for c in self.root.descendants()),0)

    def test_huge_count_rejected_before_option_allocation(self):
        self.native.item_count.return_value=1001
        with self.assertRaisesRegex(ReviewRequired,'count_out_of_range'):
            self.session.payment_options(self.factory)
        self.native.item_texts.assert_not_called()

    def test_each_read_failure_identifies_stage_without_exposing_exception_text(self):
        class BrokenValuePattern:
            @property
            def CurrentValue(self):
                raise RuntimeError('PRIVATE customer path and account')
        for stage in ['item_count','item_texts','selected_index','uia_value_pattern','window_text']:
            self.setUp()
            if stage=='uia_value_pattern':
                self.payment.iface_value=BrokenValuePattern()
            else:
                setattr(self.native,stage,Mock(side_effect=RuntimeError('PRIVATE customer path and account')))
            with self.subTest(stage=stage):
                with self.assertRaises(ComboReadRequired) as caught:
                    self.session.payment_options(self.factory)
                report=caught.exception.report()
                self.assertEqual(report['code'],'combo_'+stage+'_unavailable')
                diagnostic=report['combo_diagnostic']
                self.assertEqual(diagnostic['failed_stage'],stage)
                self.assertEqual(diagnostic['exception_type'],'RuntimeError')
                self.assertNotIn('PRIVATE',json.dumps(report))
                self.assertNotIn('Bank Transfer',json.dumps(report))
                self.assertEqual(sum(c.invocations+len(c.writes) for c in self.root.descendants()),0)

    def test_observed_single_native_option_with_no_combo_get_value_method(self):
        with self.assertRaises(AttributeError):
            self.payment.get_value()
        self.native.item_count.return_value=1
        self.native.item_texts.return_value=['Pay Cash']
        report=self.session.payment_options(self.factory)
        self.assertEqual(report['payment_options']['options'],['Pay Cash'])
        self.assertEqual(report['payment_options']['selected_index'],0)
        self.assertEqual(report['payment_options']['current_value'],'Pay Cash')
        self.assertEqual(report['values']['price_preference'],'---')
        self.native.item_count.assert_called_with()
        self.assertEqual(self.native.item_count.call_count,2)
        self.assertEqual(self.native.window_text.call_count,2)
        self.assertEqual(sum(c.invocations+len(c.writes) for c in self.root.descendants()),0)

    def test_missing_value_pattern_still_stops_instead_of_using_native_value_alone(self):
        self.payment.iface_value=None
        with self.assertRaises(ComboReadRequired) as caught:
            self.session.payment_options(self.factory)
        report=caught.exception.report()
        self.assertEqual(report['code'],'combo_uia_value_pattern_unavailable')
        self.assertEqual(report['combo_diagnostic']['exception_type'],'AttributeError')
        self.native.window_text.assert_not_called()

    def test_changed_uia_pattern_value_is_rejected(self):
        class ChangingValuePattern:
            values=iter(['Pay Cash','Bank Transfer'])
            @property
            def CurrentValue(self): return next(self.values)
        self.payment.iface_value=ChangingValuePattern()
        with self.assertRaisesRegex(ComboReadRequired,'combo_selected_value_mismatch'):
            self.session.payment_options(self.factory)

    def test_cli_writes_successful_single_option_report_using_value_pattern(self):
        self.native.item_count.return_value=1
        self.native.item_texts.return_value=['Pay Cash']
        self.root.handle=658798
        def desktop(backend):
            wrapper=self.root if backend=='uia' else self.native
            return SimpleNamespace(window=lambda handle:SimpleNamespace(wrapper_object=lambda:wrapper))
        module=SimpleNamespace(Desktop=desktop)
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules',{'pywinauto':module}), \
             patch('platform.system',return_value='Windows'),patch('sys.stdout',new_callable=io.StringIO) as output:
            out=Path(folder)/'options.json'
            status=main(['debtor-options','--pid','51052','--window-handle','658798','--out',str(out)])
            self.assertEqual(status,0)
            report=json.loads(out.read_text())
            self.assertEqual(report['payment_options']['options'],['Pay Cash'])
            self.assertEqual(report['generated_id'],'CUST000001')
            self.assertIn('READ_ONLY_DEBTOR_OPTIONS_WRITTEN',output.getvalue())
            self.assertEqual(sum(c.invocations+len(c.writes) for c in self.root.descendants()),0)

    def test_generic_native_wrapper_records_missing_method_without_guessing_options(self):
        del self.native.item_count
        with self.assertRaises(ComboReadRequired) as caught:
            self.session.payment_options(self.factory)
        diagnostic=caught.exception.report()['combo_diagnostic']
        self.assertEqual(diagnostic['native_wrapper_type'],'SimpleNamespace')
        self.assertFalse(diagnostic['callable_native_methods']['item_count'])
        self.assertEqual(diagnostic['exception_type'],'AttributeError')
        self.assertEqual(diagnostic['completed_reads'],[])
        self.native.item_texts.assert_not_called()

    def test_second_snapshot_failure_is_identified_and_does_not_report_options_as_verified(self):
        self.native.item_count.side_effect=[2,RuntimeError('PRIVATE')]
        with self.assertRaises(ComboReadRequired) as caught:
            self.session.payment_options(self.factory)
        report=caught.exception.report()
        self.assertEqual(report['combo_diagnostic']['snapshot'],2)
        self.assertEqual(report['status'],'REVIEW_REQUIRED')
        self.assertNotIn('options',report)

    def test_live_read_entrypoint_writes_combo_diagnostic_and_returns_review(self):
        self.native.item_texts.side_effect=RuntimeError('PRIVATE')
        self.root.handle=658798
        def desktop(backend):
            wrapper=self.root if backend=='uia' else self.native
            return SimpleNamespace(window=lambda handle:SimpleNamespace(wrapper_object=lambda:wrapper))
        module=SimpleNamespace(Desktop=desktop)
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules',{'pywinauto':module}), \
             patch('platform.system',return_value='Windows'),patch('sys.stderr',new_callable=io.StringIO) as errors:
            out=Path(folder)/'options.json'
            status=main(['debtor-options','--pid','51052','--window-handle','658798','--out',str(out)])
            self.assertEqual(status,2)
            report=json.loads(out.read_text())
            self.assertEqual(report['code'],'combo_item_texts_unavailable')
            self.assertEqual(report['combo_diagnostic']['option_count'],2)
            self.assertIn(report['code'],errors.getvalue())
            self.assertNotIn('PRIVATE',out.read_text()+errors.getvalue())
            self.assertEqual(sum(c.invocations+len(c.writes) for c in self.root.descendants()),0)

    def test_cross_process_or_wrong_class_binding_is_rejected(self):
        for mode in ['process','class','handle']:
            self.setUp()
            if mode=='process':self.native.element_info.process_id=777
            elif mode=='class':self.native.class_name=lambda:'SWT_Window0'
            else:self.native.handle+=1000
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ReviewRequired,'scope_or_class_mismatch'):
                    self.session.payment_options(self.factory)
                self.native.item_texts.assert_not_called()

    def test_options_changing_during_read_are_rejected(self):
        self.native.item_texts.side_effect=[['Pay Cash','Bank Transfer'],['Pay Cash','Credit Card']]
        with self.assertRaisesRegex(ReviewRequired,'changed_during_read'):
            self.session.payment_options(self.factory)

    def test_current_value_or_count_disagreement_is_rejected(self):
        for mode in ['value','count','index']:
            self.setUp()
            if mode=='value':self.native.window_text.return_value='Credit Card'
            elif mode=='count':self.native.item_count.return_value=3
            else:self.native.selected_index.return_value=3
            with self.subTest(mode=mode):
                with self.assertRaises(ReviewRequired):self.session.payment_options(self.factory)

    def test_absent_selection_is_observation_not_proof_of_missing_term(self):
        self.native.item_count.return_value=0
        self.native.item_texts.return_value=[]
        self.native.selected_index.return_value=-1
        self.native.window_text.return_value=''
        self.payment.data['patterns']['value']['CurrentValue']=''
        r=self.session.payment_options(self.factory)
        self.assertEqual(r['payment_options']['options'],[])
        self.assertEqual(r['payment_options']['status'],'OBSERVED_NATIVE_COMBO_OPTIONS_ONLY')

    def test_alias_is_literal_never_payment_id_or_save(self):
        values=self.debtor.model_dump()
        values['alias']='NORTHSTAR %{F4} ^a'
        debtor=Debtor.model_validate(values)
        r=self.session.fill_alias(debtor)
        self.assertEqual(r['values']['alias'],debtor.alias)
        self.assertEqual(r['generated_id'],'CUST000001')
        self.assertEqual(r['values']['payment'],'Pay Cash')
        self.assertEqual(sum(len(c.writes) for c in self.root.descendants()),1)
        self.assertEqual(sum(c.invocations for c in self.root.descendants()),0)

    def test_uncertain_alias_write_is_not_retried(self):
        alias=self.session.fields()['alias']
        alias.fail_after_write=True
        with self.assertRaisesRegex(ReviewRequired,'alias_write_uncertain'):
            self.session.fill_alias(self.debtor)
        self.assertEqual(alias.writes,[self.debtor.alias])

    def test_ambiguous_payment_control_stops_before_native_access(self):
        self.payment.parent()._children.append(Wrapper(self.payment.data,self.payment.parent()))
        with self.assertRaisesRegex(ReviewRequired,'missing_or_ambiguous'):
            self.session.payment_options(self.factory)
        self.factory.assert_not_called()

    def test_source_order_tab_or_misc_page_change_blocks_options(self):
        for mode in ['order','page']:
            self.setUp()
            if mode=='order':
                tab=next(c for c in self.root.descendants('TabItem') if c.element_info.name=='*New Order')
                tab.parent()._children.remove(tab)
            else:
                tab=next(c for c in self.root.descendants('TabItem') if c.element_info.name=='Miscellaneous')
                tab.data['patterns']['selection_item']['CurrentIsSelected']=0
            with self.subTest(mode=mode):
                with self.assertRaises(ReviewRequired):self.session.payment_options(self.factory)
                self.factory.assert_not_called()

    def test_tab_binding_does_not_depend_on_filtered_descendant_results(self):
        # Emulate a backend query that omits SWT's invisible TabItem headers.
        for c in [self.root]+self.root.descendants():
            original=c.descendants
            c.descendants=lambda control_type=None, original=original: [
                node for node in original(control_type) if node.element_info.control_type!='TabItem']
        session=DebtorMiscSession(self.root)
        self.assertEqual(session.payment_options(self.factory)['generated_id'],'CUST000001')
        self.assertEqual(sum(len(c.writes)+c.invocations for c in _tab_items(self.root)),0)

    def test_duplicate_editor_tab_remains_ambiguous(self):
        tab=next(c for c in _tab_items(self.root) if c.element_info.name=='New Debtor')
        tab.parent()._children.append(Wrapper(tab.data,tab.parent()))
        with self.assertRaisesRegex(ReviewRequired,'new_debtor_tab_missing_or_ambiguous'):
            DebtorMiscSession(self.root)
        self.factory.assert_not_called()

    def test_observed_starred_debtor_caption_reads_same_editor_without_mutation(self):
        tab=next(c for c in _tab_items(self.root) if c.element_info.name=='New Debtor')
        tab.element_info.name='*New Debtor'
        r=self.session.payment_options(self.factory)
        self.assertEqual(r['generated_id'],'CUST000001')
        self.assertEqual(r['payment_options']['options'],['Pay Cash','Bank Transfer'])
        self.assertEqual(sum(c.invocations+len(c.writes) for c in self.root.descendants()),0)

    def test_starred_and_unstarred_duplicate_debtors_are_not_disambiguated_by_selection(self):
        tab=next(c for c in _tab_items(self.root) if c.element_info.name=='New Debtor')
        duplicate=Wrapper(tab.data,tab.parent())
        duplicate.element_info.name='*New Debtor'
        tab.parent()._children.append(duplicate)
        with self.assertRaisesRegex(ReviewRequired,'new_debtor_tab_missing_or_ambiguous'):
            self.session.payment_options(self.factory)
        self.factory.assert_not_called()

    def test_only_exact_observed_editor_caption_variants_are_accepted(self):
        for name in ['**New Debtor','New Debtor ','Saved New Debtor','*Miscellaneous']:
            self.setUp()
            tab=next(c for c in _tab_items(self.root) if c.element_info.name=='New Debtor')
            tab.element_info.name=name
            with self.subTest(name=name):
                with self.assertRaisesRegex(ReviewRequired,'new_debtor_tab_missing_or_ambiguous'):
                    self.session.payment_options(self.factory)
                self.factory.assert_not_called()

    def test_missing_page_error_identifies_role_and_diagnostic_includes_invisible_tabs(self):
        tab=next(c for c in _tab_items(self.root) if c.element_info.name=='Miscellaneous')
        tab.element_info.name='Other page'
        with self.assertRaisesRegex(ReviewRequired,'miscellaneous_tab_missing_or_ambiguous'):
            self.session.payment_options(self.factory)
        report=_tab_diagnostic(self.root,'debtor_editor_miscellaneous_tab_missing_or_ambiguous')
        self.assertEqual(report['status'],'REVIEW_REQUIRED')
        self.assertTrue(any(t['name']=='*New Order' and t['visible'] is False for t in report['observed_tabs']))
        self.factory.assert_not_called()

    def test_tab_child_traversal_is_bounded(self):
        self.root._children=[self.root._children[0]]*501
        with self.assertRaisesRegex(ReviewRequired,'tab_tree_truncated'):
            _tab_items(self.root)

    def test_live_read_entrypoint_writes_tab_diagnostic_but_still_returns_review(self):
        tab=next(c for c in _tab_items(self.root) if c.element_info.name=='Miscellaneous')
        tab.element_info.name='Other page'
        self.root.handle=658798
        desktop=SimpleNamespace(window=lambda handle:SimpleNamespace(wrapper_object=lambda:self.root))
        module=SimpleNamespace(Desktop=lambda backend:desktop)
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules',{'pywinauto':module}), \
             patch('platform.system',return_value='Windows'),patch('sys.stderr',new_callable=io.StringIO) as errors:
            out=Path(folder)/'options.json'
            status=main(['debtor-options','--pid','51052','--window-handle','658798','--out',str(out)])
            self.assertEqual(status,2)
            report=json.loads(out.read_text())
            self.assertEqual(report['code'],'debtor_editor_miscellaneous_tab_missing_or_ambiguous')
            self.assertEqual(report['status'],'REVIEW_REQUIRED')
            self.assertIn(report['code'],errors.getvalue())
            self.assertEqual(sum(c.invocations+len(c.writes) for c in self.root.descendants()),0)
