import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import test_payment_copy_ui
from fakturama_automation.cli import main
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.payment_search_ui import _search, search_payment_list, search_payment_list_scope
from fakturama_automation.selector_vision import OcrWord


class PaymentSearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_payment_copy_ui.PaymentCopyTests()
        self.fixture.setUp()
        self.root, self.grid = self.fixture.root, self.fixture.grid
        self.search = _search(self.root)[0]
        r = self.grid.data['rect']
        self.grid.capture_as_image = Mock(return_value=Image.new('RGB', (r[2] - r[0], r[3] - r[1])))
        data = json.loads((Path(__file__).parent / 'fixtures/payment-list.headers.windows.json').read_text())
        self.words = [OcrWord(**w) for w in data]
        self.diagnostics = {}
        self.sleep = Mock()

    def run_search(self, method='Bank Transfer'):
        return search_payment_list_scope(self.root, method, Path('/tesseract'), self.fixture.guard,
                                        diagnostics=self.diagnostics, sleep=self.sleep)

    def test_one_literal_search_write_and_two_scoped_frames_never_authorize_save(self):
        with patch('fakturama_automation.payment_copy_ui.ocr_words', return_value=(self.words, (3054, 850))):
            report = self.run_search()
        self.assertEqual(self.search.writes, ['Bank Transfer'])
        self.assertEqual(self.fixture.writes(), 1)
        self.assertEqual(self.fixture.send.call_count, 0)
        self.fixture.driver.read_new.assert_not_called()
        self.assertEqual(report['status'], 'PAYMENT_SEARCH_OBSERVED_NOT_SAVED')
        self.assertTrue(report['query_readback_verified'])
        self.assertEqual(report['stable_frame_count'], 2)
        self.assertEqual(len(self.diagnostics['frames']), 2)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.search.iface_value.CurrentValue, 'Bank Transfer')
        for key in ['search_completion_verified', 'all_rows_enumerated', 'master_absence_verified',
                    'payment_code_verified', 'persistence_verified', 'source_order_number_verified']:
            self.assertFalse(report[key])
        with self.assertRaisesRegex(ReviewRequired, 'clear_search_manually_first'): self.run_search()
        self.assertEqual(self.search.writes, ['Bank Transfer'])

    def test_unsupported_method_readonly_or_partial_search_stops_before_write(self):
        for mode in ['unsupported', 'readonly', 'nonblank']:
            self.setUp()
            if mode == 'readonly': self.search.data['patterns']['value']['CurrentIsReadOnly'] = True
            if mode == 'nonblank': self.search.data['patterns']['value']['CurrentValue'] = 'Cash'
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired):
                self.run_search('Bank Transfer{ENTER}' if mode == 'unsupported' else 'Bank Transfer')
            self.assertEqual(self.fixture.writes(), 0)
            self.grid.capture_as_image.assert_not_called()

    def test_foreign_pid_wrong_scope_or_foreground_stops_before_search_write(self):
        for mode in ['pid', 'handle', 'foreground', 'duplicate']:
            self.setUp()
            if mode == 'pid': self.search.element_info.process_id = 999
            elif mode == 'handle': self.search.handle = 0
            elif mode == 'foreground': self.fixture.guard.side_effect = ReviewRequired('foreground_lost')
            else: self.search.parent()._children.append(self.search)
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired): self.run_search()
            self.assertEqual(self.fixture.writes(), 0)

    def test_uncertain_search_write_is_not_retried_or_restored(self):
        self.search.fail_after_write = True
        with self.assertRaisesRegex(ReviewRequired, 'write_uncertain'): self.run_search()
        self.assertEqual(self.search.writes, ['Bank Transfer'])
        self.grid.capture_as_image.assert_not_called()
        self.assertEqual(self.diagnostics['progress']['search_writes_attempted'], 1)

    def test_query_changed_during_capture_discards_frame(self):
        def changed():
            self.search.data['patterns']['value']['CurrentValue'] = 'Other'
            return self.grid.capture_as_image.return_value
        self.grid.capture_as_image.side_effect = changed
        with patch('fakturama_automation.payment_copy_ui.ocr_words', return_value=(self.words, (3054, 850))), \
             self.assertRaisesRegex(ReviewRequired, 'query_changed'): self.run_search()
        self.assertEqual(self.diagnostics['frames'], [])
        self.assertEqual(self.search.writes, ['Bank Transfer'])

    def test_geometry_or_search_replacement_stops_without_more_writes(self):
        for mode in ['geometry', 'replacement']:
            self.setUp()
            def changed():
                if mode == 'geometry': self.root.data['rect'][2] -= 1
                else: self.search.handle += 100
                return self.grid.capture_as_image.return_value
            self.grid.capture_as_image.side_effect = changed
            with self.subTest(mode=mode), patch('fakturama_automation.payment_copy_ui.ocr_words', return_value=(self.words, (3054, 850))), \
                 self.assertRaisesRegex(ReviewRequired, 'scope_changed'): self.run_search()
            self.assertEqual(self.search.writes, ['Bank Transfer'])
            self.assertEqual(self.diagnostics['frames'], [])

    def test_changed_result_images_or_ocr_failure_require_review_with_evidence(self):
        for mode in ['image', 'ocr']:
            self.setUp()
            with self.subTest(mode=mode), patch('fakturama_automation.payment_copy_ui.ocr_words') as ocr:
                if mode == 'ocr': ocr.side_effect = ReviewRequired('ocr_process_failed')
                else:
                    ocr.return_value = self.words, (3054, 850)
                    self.grid.capture_as_image.side_effect = [self.grid.capture_as_image.return_value,
                                                              Image.new('RGB', self.grid.capture_as_image.return_value.size, 'white')]
                with self.assertRaises(ReviewRequired): self.run_search()
                self.assertEqual(len(self.diagnostics['frames']), 1 if mode == 'ocr' else 2)
                self.assertEqual(self.search.writes, ['Bank Transfer'])

    def test_live_entrypoint_uses_existing_root_guard_and_no_clipboard_driver(self):
        with patch('fakturama_automation.payment_search_ui._payment_root', return_value=self.root), \
             patch('fakturama_automation.payment_copy_ui.process_executable_name', return_value='Other.exe'), \
             patch('fakturama_automation.payment_copy_ui.WindowsClipboard') as clipboard, \
             self.assertRaisesRegex(ReviewRequired, 'executable_identity_mismatch'):
            search_payment_list(51052, 658798, 'Bank Transfer', Path('/tesseract'))
        clipboard.assert_not_called()
        self.assertEqual(self.fixture.writes(), 0)

    def test_cli_intent_precedes_write_and_zip_contains_filter_evidence(self):
        with tempfile.TemporaryDirectory() as folder, patch('sys.stdout', new_callable=io.StringIO) as stdout:
            out = Path(folder) / 'search.zip'
            def entry(*args, **kwargs):
                self.assertFalse(json.loads(out.with_name(out.name + '.intent.json').read_text())['save_enabled'])
                with patch('fakturama_automation.payment_copy_ui.ocr_words', return_value=(self.words, (3054, 850))):
                    return search_payment_list_scope(self.root, 'Bank Transfer', Path('/tesseract'),
                        self.fixture.guard, diagnostics=kwargs['diagnostics'], sleep=self.sleep)
            with patch('fakturama_automation.cli.search_payment_list', side_effect=entry):
                self.assertEqual(main(['payment-search', '--pid', '51052', '--window-handle', '658798',
                    '--method', 'Bank Transfer', '--tesseract', '/tesseract', '--out', str(out)]), 0)
            with zipfile.ZipFile(out) as archive:
                report = json.loads(archive.read('evidence.json'))
                self.assertEqual(report['query'], 'Bank Transfer')
                self.assertEqual(report['progress']['search_writes_attempted'], 1)
                self.assertFalse(report['master_absence_verified'])
                self.assertEqual(len(report['frames']), 2)
            self.assertNotIn('Search:', stdout.getvalue())

    def test_cli_existing_output_or_intent_blocks_search(self):
        for mode in ['output', 'intent']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder, \
                 patch('sys.stderr', new_callable=io.StringIO), \
                 patch('fakturama_automation.cli.search_payment_list') as entry:
                out = Path(folder) / 'search.zip'
                existing = out if mode == 'output' else out.with_name(out.name + '.intent.json')
                existing.write_text('preserve')
                self.assertNotEqual(main(['payment-search', '--pid', '51052', '--window-handle', '658798',
                    '--method', 'Bank Transfer', '--tesseract', '/tesseract', '--out', str(out)]), 0)
                entry.assert_not_called()
                self.assertEqual(existing.read_text(), 'preserve')

    def test_cli_uncertain_failure_keeps_intent_and_writes_sanitized_zip(self):
        for error in [ReviewRequired('payment_search_write_uncertain'), RuntimeError('PRIVATE')]:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as folder, \
                 patch('sys.stderr', new_callable=io.StringIO) as stderr, \
                 patch('fakturama_automation.cli.search_payment_list', side_effect=error):
                out = Path(folder) / 'search.zip'
                self.assertEqual(main(['payment-search', '--pid', '51052', '--window-handle', '658798',
                    '--method', 'Bank Transfer', '--tesseract', '/tesseract', '--out', str(out)]), 2)
                self.assertTrue(out.with_name(out.name + '.intent.json').exists())
                with zipfile.ZipFile(out) as archive:
                    report = json.loads(archive.read('evidence.json'))
                    self.assertEqual(report['frames'], [])
                    self.assertEqual(report['status'], 'REVIEW_REQUIRED')
                    self.assertFalse(report['scope_rechecked'])
                    self.assertNotIn('PRIVATE', json.dumps(report) + stderr.getvalue())
