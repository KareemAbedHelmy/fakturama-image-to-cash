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
from fakturama_automation.payment_copy_ui import (
    _verify_columns_image, capture_payment_headers, capture_payment_headers_scope,
    copy_payment_table_scope, payment_header_bundle,
)
from fakturama_automation.selector_vision import OcrWord


class PaymentHeaderDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_payment_copy_ui.PaymentCopyTests()
        self.fixture.setUp()
        self.root, self.grid = self.fixture.root, self.fixture.grid
        self.pane = next(c for c in self.root.descendants('Pane') if c.element_info.name == 'terms of payment')
        for control in (self.grid, self.pane):
            r = control.data['rect']
            control.capture_as_image = Mock(return_value=Image.new('RGB', (r[2] - r[0], r[3] - r[1])))
        data = json.loads((Path(__file__).parent / 'fixtures/payment-list.headers.windows.json').read_text())
        self.words = [OcrWord(**w) for w in data]

    def capture(self):
        return capture_payment_headers_scope(self.root, Path('/tesseract'), self.fixture.guard)

    def test_exact_grid_and_surrounding_panel_are_captured_without_keyboard_or_clipboard(self):
        with patch('fakturama_automation.payment_copy_ui.ocr_words', return_value=(self.words, (3054, 850))), \
             patch('fakturama_automation.payment_copy_ui.WindowsClipboard', side_effect=AssertionError('not read-only')):
            frames, report = self.capture()
        self.assertEqual([f['name'] for f in frames], ['grid', 'surrounding_panel'])
        self.assertEqual(report['status'], 'READ_ONLY_PAYMENT_HEADERS_WRITTEN')
        self.assertTrue(report['scope_rechecked'])
        for key in ['all_rows_enumerated', 'master_absence_verified', 'persistence_verified']:
            self.assertFalse(report[key])
        self.assertEqual(self.fixture.writes(), 0)
        self.fixture.send.assert_not_called()
        self.fixture.driver.assert_focus.assert_not_called()
        with zipfile.ZipFile(io.BytesIO(payment_header_bundle(frames, report))) as archive:
            self.assertEqual(set(archive.namelist()), {'header-1.png', 'header-2.png', 'evidence.json'})
            evidence = json.loads(archive.read('evidence.json'))
            self.assertEqual(evidence['frames'][0]['words'][0]['text'], 'Search:')
            self.assertEqual(len(evidence['frames'][0]['headers']), 6)
            self.assertNotIn('png', evidence['frames'][0])

    def test_ocr_or_recognition_failure_preserves_pixels_and_actual_words(self):
        for mode in ['ocr', 'headers']:
            with self.subTest(mode=mode), patch('fakturama_automation.payment_copy_ui.ocr_words') as ocr:
                if mode == 'ocr': ocr.side_effect = ReviewRequired('ocr_process_failed')
                else: ocr.return_value = (self.words[:2], (3054, 850))
                frames, report = self.capture()
                for frame in frames:
                    self.assertEqual(frame['evidence']['status'], 'REVIEW_REQUIRED')
                    self.assertEqual(frame['evidence']['words'], [] if mode == 'ocr' else
                                     json.loads((Path(__file__).parent / 'fixtures/payment-list.headers.windows.json').read_text())[:2])
                    self.assertTrue(frame['png'].startswith(b'\x89PNG'))
                self.assertTrue(report['scope_rechecked'])

    def test_changed_scope_discards_diagnostics_and_scale_mismatch_stops(self):
        for mode in ['query', 'geometry', 'handle', 'scale']:
            self.setUp()
            def capture():
                image = self.grid.capture_as_image.return_value
                if mode == 'query':
                    next(c for c in self.pane.descendants('Edit')).data['patterns']['value']['CurrentValue'] = 'Other'
                elif mode == 'geometry': self.pane.data['rect'][0] += 1
                elif mode == 'handle': self.grid.handle += 1
                else: image = Image.new('RGB', (99, 100))
                return image
            self.grid.capture_as_image.side_effect = capture
            with self.subTest(mode=mode), patch('fakturama_automation.payment_copy_ui.ocr_words', return_value=(self.words, (3054, 850))), \
                 self.assertRaises(ReviewRequired): self.capture()
            self.pane.capture_as_image.assert_not_called()
            self.assertEqual(self.fixture.writes(), 0)

    def test_nonempty_search_or_lost_foreground_stops_before_pixels(self):
        for mode in ['query', 'foreground']:
            self.setUp()
            if mode == 'query':
                next(c for c in self.pane.descendants('Edit')).data['patterns']['value']['CurrentValue'] = 'Other'
            else: self.fixture.guard.side_effect = ReviewRequired('foreground_lost')
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired): self.capture()
            self.grid.capture_as_image.assert_not_called()
            self.pane.capture_as_image.assert_not_called()

    def test_readonly_entrypoint_checks_executable_and_foreground_without_clipboard_driver(self):
        for mode in ['valid', 'exe', 'foreground']:
            self.root.is_active = lambda: mode != 'foreground'
            with self.subTest(mode=mode), \
                 patch('fakturama_automation.payment_copy_ui._payment_root', return_value=self.root), \
                 patch('fakturama_automation.payment_copy_ui.process_executable_name', return_value='Other.exe' if mode == 'exe' else 'Fakturama.exe'), \
                 patch('fakturama_automation.payment_copy_ui.ocr_words', return_value=(self.words, (3054, 850))), \
                 patch('fakturama_automation.payment_copy_ui.WindowsClipboard') as clipboard:
                if mode == 'valid':
                    frames, report = capture_payment_headers(51052, 658798, Path('/tesseract'))
                    self.assertEqual(len(frames), 2)
                else:
                    with self.assertRaises(ReviewRequired): capture_payment_headers(51052, 658798, Path('/tesseract'))
                clipboard.assert_not_called()

    def test_copy_failure_diagnostics_identify_initial_or_final_phase_and_shortcut_count(self):
        for phase in ['initial', 'final']:
            self.fixture.setUp()
            diagnostics = {}
            observations = [ReviewRequired('header_missing')] if phase == 'initial' else [self.fixture.columns.return_value, ReviewRequired('header_missing')]
            with self.subTest(phase=phase), self.assertRaisesRegex(ReviewRequired, 'header_missing'):
                copy_payment_table_scope(self.fixture.root, 'Bank Transfer', self.fixture.driver,
                    self.fixture.send, self.fixture.guard, verify_columns=Mock(side_effect=observations),
                    diagnostics=diagnostics, clock=self.fixture.clock.clock, sleep=self.fixture.clock.sleep)
            self.assertEqual(diagnostics['progress'], {'phase': phase + '_headers',
                                                      'shortcuts_attempted': 0 if phase == 'initial' else 4})
            self.assertEqual(self.fixture.writes(), 0)

    def test_header_failure_collector_preserves_exact_frame_without_accepting_headers(self):
        diagnostics = {}
        with patch('fakturama_automation.payment_copy_ui.ocr_words', return_value=(self.words[:2], (3054, 850))), \
             self.assertRaisesRegex(ReviewRequired, 'headers_missing'):
            _verify_columns_image(self.grid, Path('/tesseract'), diagnostics)
        self.assertEqual(len(diagnostics['frames']), 1)
        self.assertEqual(diagnostics['frames'][0]['name'], 'grid-1')
        self.assertEqual(len(diagnostics['frames'][0]['evidence']['words']), 2)

    def test_cli_readonly_bundle_contains_ocr_failure_and_blocks_overwrite(self):
        with patch('fakturama_automation.payment_copy_ui.ocr_words', side_effect=ReviewRequired('ocr_process_failed')):
            frames, report = self.capture()
        with tempfile.TemporaryDirectory() as folder, patch('sys.stdout', new_callable=io.StringIO) as stdout, \
             patch('sys.stderr', new_callable=io.StringIO), \
             patch('fakturama_automation.cli.capture_payment_headers', return_value=(frames, report)) as capture:
            out = Path(folder) / 'headers.zip'
            args = ['payment-headers', '--pid', '51052', '--window-handle', '658798',
                    '--tesseract', '/tesseract', '--out', str(out)]
            self.assertEqual(main(args), 0)
            self.assertIn('ocr_process_failed', stdout.getvalue())
            self.assertNotIn('Search:', stdout.getvalue())
            with zipfile.ZipFile(out) as archive:
                self.assertEqual(json.loads(archive.read('evidence.json'))['frames'][0]['code'], 'ocr_process_failed')
            self.assertEqual(main(args), 2)
            self.assertEqual(capture.call_count, 1)
            self.assertFalse(out.with_name(out.name + '.intent.json').exists())

    def test_cli_copy_failure_writes_diagnostic_zip_and_retains_original_error(self):
        with patch('fakturama_automation.payment_copy_ui.ocr_words', return_value=(self.words[:2], (3054, 850))):
            frames, _ = self.capture()
        def copy(*args, **kwargs):
            kwargs['diagnostics'].update(frames=[frames[0]], progress={'phase': 'initial_headers', 'shortcuts_attempted': 0})
            raise ReviewRequired('payment_copy_headers_missing_low_confidence_or_wrong_order')
        with tempfile.TemporaryDirectory() as folder, patch('sys.stderr', new_callable=io.StringIO), \
             patch('fakturama_automation.cli.copy_payment_table', side_effect=copy):
            out = Path(folder) / 'copy.json'
            self.assertEqual(main(['payment-copy', '--pid', '51052', '--window-handle', '658798',
                                  '--method', 'Bank Transfer', '--tesseract', '/tesseract', '--out', str(out)]), 2)
            evidence = json.loads(out.read_text())
            self.assertEqual(evidence['progress']['shortcuts_attempted'], 0)
            self.assertTrue(out.with_name(out.name + '.intent.json').exists())
            with zipfile.ZipFile(evidence['diagnostic_bundle']) as archive:
                report = json.loads(archive.read('evidence.json'))
                self.assertFalse(report['scope_rechecked'])
                self.assertEqual(report['code'], evidence['code'])
                self.assertEqual(len(report['frames'][0]['words']), 2)
