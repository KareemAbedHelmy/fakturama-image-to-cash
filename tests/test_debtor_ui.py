import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from fakturama_automation.debtor_ui import DebtorMainAddressSession
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.input_io import read_order
from test_selector_ui import Wrapper


class SelectionPattern:
    def __init__(self, wrapper): self.wrapper = wrapper
    @property
    def CurrentIsSelected(self):
        return self.wrapper.data.get('patterns',{}).get('selection_item',{}).get('CurrentIsSelected',0)


class DebtorUITests(unittest.TestCase):
    def setUp(self):
        self.root = Wrapper(json.loads((Path(__file__).parent / 'fixtures/new-debtor.uia.json').read_text()))
        for i,c in enumerate([self.root]+self.root.descendants()):
            c.handle = i+1
            c.iface_selection_item = SelectionPattern(c)
            c.rectangle = lambda c=c: SimpleNamespace(**dict(zip(
                ('left','top','right','bottom'),c.data['rect'])))
        self.session = DebtorMainAddressSession(self.root)
        self.debtor = read_order(Path(__file__).parent.parent/'examples/order.provided.expected.json').debtor

    def country_ready(self):
        field = self.session.fields()['country']
        field.data['patterns']['value']['CurrentValue'] = self.debtor.billing.country

    def writes(self):
        return sum(len(c.writes) for c in self.root.descendants())

    def test_actual_capture_preserves_generated_id_and_observes_country_default(self):
        report = self.session.observe()
        self.assertEqual(report['generated_id'],'CUST000001')
        self.assertEqual(report['values']['country'],'United Arab Emirates')
        self.assertTrue(report['source_order_tab_present'])
        self.assertFalse(report['source_order_number_verified'])
        self.assertEqual(self.writes(),0)

    def test_country_mismatch_blocks_all_writes(self):
        with self.assertRaisesRegex(ReviewRequired,'country_selection_required'):
            self.session.fill_main_fields(self.debtor)
        self.assertEqual(self.writes(),0)

    def test_literal_field_fill_never_writes_id_or_saves(self):
        self.country_ready()
        result = self.session.fill_main_fields(self.debtor)
        self.assertEqual(result['values']['first_name'],'Marta')
        self.assertEqual(result['values']['last_name'],'Klein')
        self.assertEqual(result['values']['zip'],'10117')
        self.assertEqual(result['values']['city'],'Berlin')
        id_field = next(c for c in self.root.descendants('Edit') if c.element_info.name=='Customer ID')
        self.assertEqual(id_field.writes,[])
        self.assertEqual(result['generated_id'],'CUST000001')
        self.assertEqual(sum(c.invocations for c in self.root.descendants()),0)
        self.assertEqual(self.session.fields()['country'].writes,[])

    def test_late_field_conflict_or_read_only_preflight_blocks_earlier_writes(self):
        for mode in ['conflict','read_only']:
            self.setUp()
            self.country_ready()
            phone = self.session.fields()['telephone']
            if mode=='conflict': phone.data['patterns']['value']['CurrentValue']='other phone'
            else: phone.data['patterns']['value']['CurrentIsReadOnly']=1
            with self.subTest(mode=mode):
                with self.assertRaises(ReviewRequired): self.session.fill_main_fields(self.debtor)
                self.assertEqual(self.writes(),0)

    def test_uncertain_write_is_not_retried_or_followed_by_next_field(self):
        self.country_ready()
        company = self.session.fields()['company']
        company.fail_after_write = True
        with self.assertRaisesRegex(ReviewRequired,'write_uncertain'):
            self.session.fill_main_fields(self.debtor)
        self.assertEqual(company.writes,[self.debtor.company])
        self.assertEqual(self.writes(),1)

    def test_original_order_tab_must_remain_present(self):
        order = next(c for c in self.root.descendants('TabItem') if c.element_info.name=='*New Order')
        order.parent()._children.remove(order)
        with self.assertRaisesRegex(ReviewRequired,'source_order_tab_missing'):
            self.session.observe()

    def test_selected_page_change_blocks_binding(self):
        tab = next(c for c in self.root.descendants('TabItem') if c.element_info.name=='Addresses')
        tab.data['patterns']['selection_item']['CurrentIsSelected']=0
        with self.assertRaisesRegex(ReviewRequired,'tab_not_selected'):
            self.session.observe()

    def test_anonymous_pair_reordering_is_rejected(self):
        fields = self.session.fields()
        first, last = fields['first_name'],fields['last_name']
        p=first.parent()
        p._children=[last,first]
        with self.assertRaisesRegex(ReviewRequired,'pair_order_unverified'):
            self.session.observe()

    def test_changed_editor_or_generated_id_is_rejected(self):
        for mode in ['editor','id']:
            self.setUp()
            if mode=='editor': self.session._pane().handle+=1000
            else:
                f=next(c for c in self.root.descendants('Edit') if c.element_info.name=='Customer ID')
                f.data['patterns']['value']['CurrentValue']='CUST000002'
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ReviewRequired,'changed'): self.session.observe()

    def test_capture_ids_and_position_offsets_are_not_locators(self):
        for i,c in enumerate(self.root.descendants()):
            c.element_info.automation_id=f'next-{i}'
            r=c.data['rect']
            c.data['rect']=[r[0]+100,r[1]+100,r[2]+100,r[3]+100]
        self.assertEqual(self.session.observe()['generated_id'],'CUST000001')
