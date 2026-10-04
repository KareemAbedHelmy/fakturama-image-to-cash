import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from fakturama_automation.cli import main
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.payment_list_ui import capture_payment_list, capture_payment_list_scope
from fakturama_automation.selector_vision import OcrWord
from test_native_ui import NativeControl
from test_selector_ui import Wrapper


class PaymentListCaptureTests(unittest.TestCase):
    def setUp(self):
        tree = json.loads((Path(__file__).parent / 'fixtures/new-payment-term.uia.json').read_text())
        self.root = Wrapper(tree)
        self.root.handle = 658798
        self.root.is_active = lambda: True
        for i, control in enumerate([self.root] + self.root.descendants()):
            if control is not self.root:
                control.handle = i + 1
            control.rectangle = lambda c=control: SimpleNamespace(**dict(zip(
                ('left', 'top', 'right', 'bottom'), c.data['rect'])))
        self.pane = next(c for c in self.root.descendants('Pane') if c.element_info.name == 'terms of payment')
        r = self.pane.data['rect']
        self.size = (r[2] - r[0], r[3] - r[1])
        self.pane.capture_as_image = Mock(return_value=Image.new('RGB', self.size))
        self.native = NativeControl('SWT_Window0', pid=51052)
        self.native.handle = self.pane.handle
        self.factory = Mock(return_value=self.native)
        self.guard = Mock()

    def capture(self):
        return capture_payment_list_scope(self.root, self.factory, self.guard)

    def mutations(self):
        return sum(c.invocations + len(c.writes) for c in self.root.descendants())

    def test_actual_capture_structure_yields_unverified_frame_and_metadata_without_actions(self):
        png, report = self.capture()
        self.assertEqual(Image.open(io.BytesIO(png)).size, self.size)
        self.assertEqual(report['query'], '')
        self.assertEqual(report['uia']['tree']['name'], 'terms of payment')
        self.assertFalse(report['uia']['truncated'])
        self.assertFalse(report['win32']['truncated'])
        for key in ['all_rows_enumerated', 'master_absence_verified', 'persistence_verified']:
            self.assertFalse(report[key])
        self.factory.assert_called_once_with(self.pane.handle)
        self.assertEqual(self.mutations(), 0)

    def test_collapsed_or_ambiguous_list_stops_before_native_reads_or_pixels(self):
        for mode in ['hidden', 'duplicate', 'tiny']:
            self.setUp()
            if mode == 'hidden':
                self.pane.data['visible'] = False
            elif mode == 'duplicate':
                self.pane.parent()._children.append(Wrapper(self.pane.data, self.pane.parent()))
            else:
                self.pane.data['rect'][3] = self.pane.data['rect'][1] + 1
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired):
                self.capture()
            self.factory.assert_not_called()
            self.pane.capture_as_image.assert_not_called()

    def test_retained_source_tabs_required_without_relying_on_uia_visibility(self):
        for name in ['*New Order', '*New Debtor']:
            self.setUp()
            tab = next(c for c in self.root.descendants('TabItem') if c.element_info.name == name)
            tab.parent()._children.remove(tab)
            with self.subTest(name=name), self.assertRaisesRegex(ReviewRequired, 'tab_missing_or_ambiguous'):
                self.capture()
            self.factory.assert_not_called()

    def test_foreign_process_metadata_never_read(self):
        child = self.pane.children()[0]
        child.element_info.process_id = 99
        child.rectangle = Mock(side_effect=AssertionError('foreign control read'))
        with self.assertRaisesRegex(ReviewRequired, 'process_mismatch'):
            self.capture()
        child.rectangle.assert_not_called()
        self.factory.assert_not_called()

    def test_native_scope_must_match_pinned_handle_process_and_class(self):
        for mode in ['handle', 'pid', 'class', 'visible', 'enabled']:
            self.setUp()
            if mode == 'handle': self.native.handle += 500
            elif mode == 'pid': self.native.element_info.process_id = 99
            elif mode == 'class': self.native.kind = 'Other'
            elif mode == 'visible': self.native.is_visible = lambda: False
            else: self.native.is_enabled = lambda: False
            with self.subTest(mode=mode), self.assertRaisesRegex(ReviewRequired, 'native_identity_mismatch'):
                self.capture()
            self.assertEqual(self.native.text_reads, 0)
            self.pane.capture_as_image.assert_not_called()

    def test_frame_discarded_on_panel_replacement_query_change_or_window_resize(self):
        for mode in ['handle', 'query', 'rect']:
            self.setUp()
            def changed():
                if mode == 'handle': self.pane.handle += 1
                elif mode == 'query':
                    search = next(c for c in self.pane.descendants('Edit'))
                    search.data['patterns']['value']['CurrentValue'] = 'Other'
                else: self.root.data['rect'][2] -= 1
                return Image.new('RGB', self.size)
            self.pane.capture_as_image.side_effect = changed
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired): self.capture()
            self.assertEqual(self.mutations(), 0)

    def test_guard_failure_stops_before_capture_and_after_capture_discards_frame(self):
        for call in [1, 3]:
            self.setUp()
            self.guard.side_effect = [None] * (call - 1) + [ReviewRequired('foreground_lost')]
            with self.subTest(call=call), self.assertRaisesRegex(ReviewRequired, 'foreground_lost'):
                self.capture()
            self.assertEqual(self.pane.capture_as_image.call_count, int(call == 3))

    def test_native_change_during_read_stops_before_pixels(self):
        original = self.native.window_text
        def changed():
            self.pane.handle += 1
            return original()
        self.native.window_text = changed
        with self.assertRaisesRegex(ReviewRequired, 'changed_during_capture'): self.capture()
        self.pane.capture_as_image.assert_not_called()

    def test_scale_mismatch_does_not_return_misplaced_pixels(self):
        self.pane.capture_as_image.return_value = Image.new('RGB', (100, 100))
        with self.assertRaisesRegex(ReviewRequired, 'scale_mismatch'): self.capture()

    def test_native_item_count_never_proves_absence(self):
        child = NativeControl('SysListView32', pid=51052)
        child.item_count = lambda: 0
        self.native._children.append(child)
        _, report = self.capture()
        self.assertEqual(report['win32']['nodes'][1]['native_item_count'], 0)
        self.assertFalse(report['master_absence_verified'])

    def test_nonblank_filter_is_recorded_without_searching_or_inferring_absence(self):
        next(c for c in self.pane.descendants('Edit')).data['patterns']['value']['CurrentValue'] = 'Bank Transfer'
        _, report = self.capture()
        self.assertEqual(report['query'], 'Bank Transfer')
        self.assertFalse(report['master_absence_verified'])
        self.assertEqual(self.mutations(), 0)

    def test_runtime_handles_and_positions_do_not_act_as_saved_locators(self):
        for control in [self.root] + self.root.descendants():
            control.element_info.automation_id = 'changed'
            control.data['rect'] = [v + 100 for v in control.data['rect']]
        self.pane.handle += 9999
        self.native.handle = self.pane.handle
        self.assertEqual(self.capture()[1]['image_size'], list(self.size))

    def test_live_entrypoint_requires_real_executable_and_foreground(self):
        def desktop(backend):
            wrapper = self.root if backend == 'uia' else self.native
            return SimpleNamespace(window=lambda handle: SimpleNamespace(wrapper_object=lambda: wrapper))
        for mode in ['valid', 'exe', 'foreground']:
            with self.subTest(mode=mode), patch.dict('sys.modules', {'pywinauto': SimpleNamespace(Desktop=desktop)}), \
                 patch('platform.system', return_value='Windows'), \
                 patch('fakturama_automation.payment_list_ui.process_executable_name',
                       return_value='Other.exe' if mode == 'exe' else 'Fakturama.exe'):
                self.root.is_active = lambda: mode != 'foreground'
                if mode == 'valid': self.assertEqual(capture_payment_list(51052, 658798)[1]['pid'], 51052)
                else:
                    with self.assertRaises(ReviewRequired): capture_payment_list(51052, 658798)

    def test_cli_writes_frame_and_ocr_candidates_and_preserves_frames_on_ocr_failure(self):
        png, report = self.capture()
        for fail in [False, True]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as folder, \
                 patch('fakturama_automation.cli.capture_payment_list', return_value=(png, report)), \
                 patch('fakturama_automation.cli.ocr_words') as ocr, \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                if fail: ocr.side_effect = ReviewRequired('ocr_process_failed')
                else: ocr.return_value = ([OcrWord('Cash', 10, 10, 40, 15, 95)], self.size)
                out = Path(folder) / 'list.zip'
                self.assertEqual(main(['payment-list', '--pid', '51052', '--window-handle', '658798',
                                      '--tesseract', str(Path(folder) / 'tesseract.exe'), '--out', str(out)]), 0)
                with zipfile.ZipFile(out) as archive:
                    self.assertEqual(set(archive.namelist()), {'payment-list.png', 'evidence.json'})
                    saved = json.loads(archive.read('evidence.json'))
                    self.assertFalse(saved['master_absence_verified'])
                    if fail: self.assertEqual(saved['ocr']['code'], 'ocr_process_failed')
                    else: self.assertEqual(saved['ocr']['words'][0]['text'], 'Cash')
                self.assertIn('READ_ONLY_PAYMENT_LIST_EVIDENCE_WRITTEN', output.getvalue())

    def test_existing_evidence_blocks_before_ui_capture(self):
        with tempfile.TemporaryDirectory() as folder, patch('fakturama_automation.cli.capture_payment_list') as capture, \
             patch('sys.stderr', new_callable=io.StringIO):
            out = Path(folder) / 'list.zip'
            out.write_bytes(b'previous evidence')
            self.assertEqual(main(['payment-list', '--pid', '51052', '--window-handle', '658798',
                                  '--tesseract', str(Path(folder) / 'tesseract.exe'), '--out', str(out)]), 2)
            capture.assert_not_called()
            self.assertEqual(out.read_bytes(), b'previous evidence')

