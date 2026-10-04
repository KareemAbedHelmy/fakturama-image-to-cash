import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fakturama_automation.cli import main
from fakturama_automation.demo import run_reviewer_demo
from fakturama_automation.errors import ReviewRequired

SOURCE = Path(__file__).parent.parent/'examples/order.provided.expected.json'


class ReviewerDemoTests(unittest.TestCase):
    def test_supplied_reviewed_input_runs_all_scenarios_and_retains_auditable_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'demo'
            report=run_reviewer_demo(SOURCE,out,reviewed_input=True)
            self.assertEqual(report['backend'],'SIMULATOR_ONLY')
            self.assertFalse(report['live_workflow_verified'])
            self.assertFalse(report['automatic_image_extraction_verified'])
            self.assertEqual(report['totals'],{'net':'570.00','vat':'108.30','gross':'678.30'})
            self.assertEqual([r['gross'] for r in report['product_gross']],['297.50','47.60'])
            self.assertEqual(len(report['scenarios']),8)
            self.assertTrue(all(r['passed'] for r in report['scenarios']))
            rows={r['name']:r for r in report['scenarios']}
            for name in ('missing','existing','mixed','derived_unpaid'):
                self.assertEqual(rows[name]['order_save_count'],1)
                self.assertEqual(rows[name]['invoice_save_count'],1)
                self.assertEqual(rows[name]['retained_lease_count'],0)
                self.assertTrue(all(d['separate_address_values_preserved'] for d in rows[name]['documents']))
            self.assertNotIn('create_product',rows['existing']['calls'])
            self.assertEqual(rows['mixed']['calls'].count('create_product'),1)
            unpaid=next(d for d in rows['derived_unpaid']['documents'] if d['kind']=='Invoice')
            self.assertFalse(unpaid['paid'])
            self.assertIsNone(unpaid['payment_value'])
            uncertain=rows['uncertain_order_save']
            self.assertEqual(uncertain['duplicate_replay_error'],'duplicate_job')
            self.assertEqual(uncertain['retained_lease_count'],1)
            self.assertEqual(uncertain['invoice_save_count'],0)
            self.assertIn({'stage':'save_order','status':'INTENT'},uncertain['events'])
            self.assertNotIn({'stage':'save_order','status':'OBSERVED'},uncertain['events'])
            self.assertEqual(rows['invalid_totals']['calls'],[])
            self.assertEqual(rows['invalid_totals']['journal_states'],[])
            self.assertEqual(json.loads((out/'report.json').read_text()),report)
            self.assertIn('SIMULATOR ONLY',(out/'report.html').read_text(encoding='utf-8'))
            self.assertEqual(len(list(out.glob('*.sqlite'))),8)

    def test_acknowledgement_and_valid_input_are_required_before_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'demo'
            with self.assertRaisesRegex(ReviewRequired,'reviewed_input_required'):
                run_reviewer_demo(SOURCE,out,reviewed_input=False)
            self.assertFalse(out.exists())
            bad=Path(folder)/'bad.json'
            data=json.loads(SOURCE.read_text());data['totals']['gross']='1'
            bad.write_text(json.dumps(data))
            with self.assertRaisesRegex(ReviewRequired,'source_totals_mismatch'):
                run_reviewer_demo(bad,out,reviewed_input=True)
            self.assertFalse(out.exists())

    def test_existing_directory_is_preserved_and_cannot_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'demo';out.mkdir()
            sentinel=out/'keep.txt';sentinel.write_text('keep')
            with self.assertRaisesRegex(ReviewRequired,'output_directory_exists'):
                run_reviewer_demo(SOURCE,out,reviewed_input=True)
            self.assertEqual(sentinel.read_text(),'keep')
            self.assertEqual(len(list(out.iterdir())),1)

    def test_unexpected_failure_keeps_partial_evidence_and_incomplete_marker(self):
        with tempfile.TemporaryDirectory() as folder,patch('fakturama_automation.demo._scenario',side_effect=RuntimeError('PRIVATE')):
            out=Path(folder)/'demo'
            with self.assertRaises(RuntimeError):run_reviewer_demo(SOURCE,out,reviewed_input=True)
            self.assertTrue((out/'incomplete.json').exists())
            self.assertFalse((out/'report.html').exists())
            self.assertNotIn('PRIVATE',(out/'incomplete.json').read_text())
            with self.assertRaisesRegex(ReviewRequired,'output_directory_exists'):
                run_reviewer_demo(SOURCE,out,reviewed_input=True)

    def test_arbitrary_reviewed_json_is_used_without_filename_or_image_constants(self):
        with tempfile.TemporaryDirectory() as folder:
            data=json.loads(SOURCE.read_text())
            data['external_reference']='OTHER-REF'
            data['items'][0]['sku']='OTHER-CHAIR'
            data['payment']['method']='Credit Card'
            source=Path(folder)/'arbitrary.json';source.write_text(json.dumps(data))
            report=run_reviewer_demo(source,Path(folder)/'demo',reviewed_input=True)
            self.assertEqual(report['product_gross'][0]['sku'],'OTHER-CHAIR')
            invoice=next(d for d in report['scenarios'][0]['documents'] if d['kind']=='Invoice')
            self.assertEqual(invoice['payment_method'],'Credit Card')

    def test_cli_reports_demo_backend_and_generated_report_location(self):
        with tempfile.TemporaryDirectory() as folder,patch('sys.stdout',new_callable=io.StringIO) as output:
            out=Path(folder)/'demo'
            self.assertEqual(main(['demo',str(SOURCE),'--reviewed-input','--out-dir',str(out)]),0)
            message=json.loads(output.getvalue())
            self.assertEqual(message['backend'],'SIMULATOR_ONLY')
            self.assertEqual(message['scenario_count'],8)
            self.assertEqual(message['report'],str(out/'report.html'))


if __name__=='__main__':unittest.main()
