import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fakturama_automation.cli import main
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.payment_copy_ui import _binding, copy_payment_table, copy_payment_table_scope
from fakturama_automation.payment_table import PAYMENT_COLUMNS
from fakturama_automation.windows_clipboard import ClipboardCopy
from test_selector_ui import Wrapper


class FakeClock:
    def __init__(self): self.now = 0
    def clock(self): return self.now
    def sleep(self, duration): self.now += duration


class PaymentCopyTests(unittest.TestCase):
    def setUp(self):
        tree = json.loads((Path(__file__).parent / 'fixtures/new-payment-term.uia.json').read_text())
        self.root = Wrapper(tree)
        self.root.handle = 658798
        self.root.is_active = lambda: True
        for i, control in enumerate([self.root] + self.root.descendants()):
            if control is not self.root: control.handle = i + 1
            control.rectangle = lambda c=control: SimpleNamespace(**dict(zip(
                ('left', 'top', 'right', 'bottom'), c.data['rect'])))
        self.grid = _binding(self.root)[0]
        self.current_sequence, self.focus = 100, self.grid.handle
        self.text = 'true\tCash\tPay Cash\t0.0\t0\t0\r\n'
        self.clock = FakeClock()
        self.guard = Mock()
        self.columns = Mock(return_value=[{'label': label} for label in PAYMENT_COLUMNS])
        def focus(main, grid, pid):
            if main != 658798 or grid != self.focus or pid != 51052:
                raise ReviewRequired('payment_copy_click_payment_table_first')
        def read(pid, handle, sequence):
            return ClipboardCopy(self.text, self.current_sequence, 123, 51052)
        self.driver = SimpleNamespace(assert_focus=Mock(side_effect=focus),
            sequence=Mock(side_effect=lambda: self.current_sequence), read_new=Mock(side_effect=read))
        def keys(value):
            if value == '^c': self.current_sequence += 1
        self.send = Mock(side_effect=keys)

    def copy(self):
        return copy_payment_table_scope(self.root, 'Bank Transfer', self.driver, self.send,
                                       self.guard, verify_columns=self.columns,
                                       clock=self.clock.clock, sleep=self.clock.sleep)

    def writes(self):
        return sum(c.invocations + len(c.writes) for c in self.root.descendants())

    def test_missing_or_wrong_initial_headers_stop_before_shortcuts(self):
        for result in [ReviewRequired('payment_copy_headers_missing_low_confidence_or_wrong_order'),
                       [{'label': label} for label in reversed(PAYMENT_COLUMNS)]]:
            self.columns.side_effect = result if isinstance(result, Exception) else None
            self.columns.return_value = result
            with self.subTest(result=result), self.assertRaises(ReviewRequired): self.copy()
            self.send.assert_not_called()
            self.driver.read_new.assert_not_called()
        with self.assertRaisesRegex(ReviewRequired, 'column_verifier_required'):
            copy_payment_table_scope(self.root, 'Bank Transfer', self.driver, self.send, self.guard)
        self.send.assert_not_called()

    def test_failed_final_headers_discard_copies_without_save(self):
        self.columns.side_effect = [[{'label': label} for label in PAYMENT_COLUMNS],
                                   ReviewRequired('payment_copy_headers_missing_low_confidence_or_wrong_order')]
        with self.assertRaisesRegex(ReviewRequired, 'headers_missing'): self.copy()
        self.assertEqual(self.send.call_count, 4)
        self.assertEqual(self.writes(), 0)

    def test_two_fresh_copies_use_observed_grid_and_fixed_shortcuts_without_form_writes(self):
        report = self.copy()
        self.assertEqual([c.args[0] for c in self.send.call_args_list], ['^a', '^c', '^a', '^c'])
        self.assertEqual(report['copy_sequences'], [101, 102])
        self.assertEqual(report['rows'][0]['name'], 'Cash')
        self.assertTrue(report['clipboard_provenance_verified'])
        self.assertTrue(report['column_order_verified'])
        self.assertEqual(self.columns.call_count, 2)
        for key in ['all_rows_enumerated', 'master_absence_verified', 'persistence_verified',
                    'payment_code_verified']:
            self.assertFalse(report[key])
        self.assertEqual(report['status'], 'PAYMENT_TABLE_COPIED_NOT_SAVED')
        self.assertEqual(self.writes(), 0)

    def test_wrong_table_focus_stops_before_shortcuts_or_clipboard_reads(self):
        self.focus = 999
        with self.assertRaisesRegex(ReviewRequired, 'click_payment_table_first'): self.copy()
        self.send.assert_not_called()
        self.driver.read_new.assert_not_called()

    def test_hidden_duplicate_or_foreign_grid_stops_before_keys(self):
        for mode in ['hidden', 'duplicate', 'pid']:
            self.setUp()
            if mode == 'hidden': self.grid.data['visible'] = False
            elif mode == 'duplicate': self.grid.parent()._children.append(Wrapper(self.grid.data, self.grid.parent()))
            else: self.grid.element_info.process_id = 99
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired): self.copy()
            self.send.assert_not_called()

    def test_nonblank_search_and_missing_original_tabs_stop_before_keys(self):
        for mode in ['search', 'order', 'debtor']:
            self.setUp()
            if mode == 'search':
                next(c for c in self.root.descendants('Text') if c.element_info.name == 'Search:').parent().descendants('Edit')[0].data['patterns']['value']['CurrentValue'] = 'Bank Transfer'
            else:
                name = '*New Order' if mode == 'order' else '*New Debtor'
                tab = next(c for c in self.root.descendants('TabItem') if c.element_info.name == name)
                tab.parent()._children.remove(tab)
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired): self.copy()
            self.send.assert_not_called()

    def test_focus_or_geometry_change_after_select_all_stops_before_copy(self):
        for mode in ['focus', 'rect']:
            self.setUp()
            def changed(value):
                if mode == 'focus': self.focus = 999
                else: self.grid.data['rect'][0] += 1
            self.send.side_effect = changed
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired): self.copy()
            self.assertEqual(self.send.call_count, 1)
            self.driver.read_new.assert_not_called()

    def test_key_failure_is_sanitized_and_never_retried(self):
        self.send.side_effect = RuntimeError('PRIVATE key exception')
        with self.assertRaisesRegex(ReviewRequired, 'key_action_uncertain'): self.copy()
        self.assertEqual(self.send.call_count, 1)
        self.driver.read_new.assert_not_called()

    def test_busy_clipboard_is_polled_without_repeating_shortcuts(self):
        original = self.driver.read_new.side_effect
        calls = [0]
        def busy(pid, hwnd, sequence):
            calls[0] += 1
            if calls[0] <= 2: raise ReviewRequired('clipboard_busy')
            return original(pid, hwnd, sequence)
        self.driver.read_new.side_effect = busy
        self.assertEqual(self.copy()['stable_copy_count'], 2)
        self.assertEqual(self.send.call_count, 4)
        self.assertGreater(self.clock.now, 0)

    def test_stale_foreign_or_missing_clipboard_times_out_without_key_retry(self):
        for code in ['clipboard_unchanged', 'clipboard_owner_not_fakturama', 'clipboard_owner_unavailable']:
            self.setUp()
            self.driver.read_new.side_effect = ReviewRequired(code)
            with self.subTest(code=code), self.assertRaisesRegex(ReviewRequired, 'fresh_clipboard_timeout'): self.copy()
            self.assertEqual(self.send.call_count, 2)
            self.assertLessEqual(self.clock.now, 5.1)

    def test_native_read_corruption_or_focus_loss_stops_without_another_copy(self):
        self.driver.read_new.side_effect = ReviewRequired('clipboard_changed_during_read')
        with self.assertRaisesRegex(ReviewRequired, 'changed_during_read'): self.copy()
        self.assertEqual(self.send.call_count, 2)
        self.setUp()
        original = self.driver.read_new.side_effect
        def changed(*args):
            result = original(*args)
            self.focus = 999
            return result
        self.driver.read_new.side_effect = changed
        with self.assertRaisesRegex(ReviewRequired, 'click_payment_table_first'): self.copy()
        self.assertEqual(self.send.call_count, 2)

    def test_partial_first_row_or_bad_provenance_blocks_second_snapshot(self):
        for mode in ['row', 'owner', 'sequence']:
            self.setUp()
            if mode == 'row': self.text = 'Cash\tPay Cash'
            else:
                self.driver.read_new.side_effect = lambda pid, hwnd, baseline: ClipboardCopy(
                    self.text, baseline if mode == 'sequence' else 101, 123, 99 if mode == 'owner' else pid)
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired): self.copy()
            self.assertEqual(self.send.call_count, 2)

    def test_differing_snapshots_or_duplicate_exact_method_stop_for_review(self):
        original = self.driver.read_new.side_effect
        def changed(*args):
            if self.current_sequence == 102: self.text = self.text.replace('Pay Cash', 'Other')
            return original(*args)
        self.driver.read_new.side_effect = changed
        with self.assertRaisesRegex(ReviewRequired, 'rows_changed'): self.copy()
        self.setUp()
        self.text = ('false\tBank Transfer\tBank Transfer\t0.0\t0\t0\n') * 2
        with self.assertRaisesRegex(ReviewRequired, 'ambiguous_payment'): self.copy()
        self.assertEqual(self.writes(), 0)

    def test_runtime_handles_and_positions_are_resolved_afresh(self):
        for control in [self.root] + self.root.descendants():
            control.data['rect'] = [v + 100 for v in control.data['rect']]
            control.element_info.automation_id = 'new'
        self.grid.handle += 10000
        self.focus = self.grid.handle
        self.assertEqual(self.copy()['grid_handle'], self.grid.handle)

    def test_live_entrypoint_uses_fixed_keys_and_requires_executable_foreground(self):
        desktop = SimpleNamespace(window=lambda handle: SimpleNamespace(wrapper_object=lambda: self.root))
        for mode in ['valid', 'exe', 'foreground']:
            self.setUp()
            desktop.window = lambda handle: SimpleNamespace(wrapper_object=lambda: self.root)
            keyboard = SimpleNamespace(send_keys=Mock(side_effect=lambda value, **kwargs: self.send(value)))
            with self.subTest(mode=mode), patch.dict('sys.modules', {'pywinauto': SimpleNamespace(Desktop=lambda **kwargs: desktop), 'pywinauto.keyboard': keyboard}), \
                 patch('platform.system', return_value='Windows'), \
                 patch('fakturama_automation.payment_copy_ui.WindowsClipboard', return_value=self.driver), \
                 patch('fakturama_automation.payment_copy_ui._verify_columns_image', side_effect=lambda *args: self.columns(args[0])), \
                 patch('fakturama_automation.payment_copy_ui.process_executable_name', return_value='Other.exe' if mode == 'exe' else 'Fakturama.exe'):
                self.root.is_active = lambda: mode != 'foreground'
                if mode == 'valid':
                    self.assertEqual(copy_payment_table(51052, 658798, 'Bank Transfer', Path('tesseract.exe'))['stable_copy_count'], 2)
                    self.assertEqual(keyboard.send_keys.call_args.kwargs,
                                     {'pause': 0.05, 'vk_packet': False, 'turn_off_numlock': False})
                else:
                    with self.assertRaises(ReviewRequired): copy_payment_table(51052, 658798, 'Bank Transfer', Path('tesseract.exe'))
                    keyboard.send_keys.assert_not_called()

    def test_cli_intent_precedes_shortcuts_and_success_writes_private_rows(self):
        with tempfile.TemporaryDirectory() as folder, patch('sys.stdout', new_callable=io.StringIO) as output:
            out = Path(folder) / 'copied.json'
            def entry(*args, **kwargs):
                intent = json.loads(out.with_name(out.name + '.intent.json').read_text())
                self.assertEqual(intent['status'], 'PAYMENT_COPY_INTENT')
                self.assertFalse(intent['save_enabled'])
                return self.copy()
            with patch('fakturama_automation.cli.copy_payment_table', side_effect=entry):
                self.assertEqual(main(['payment-copy', '--pid', '51052', '--window-handle', '658798',
                                      '--method', 'Bank Transfer', '--tesseract', 'tesseract.exe', '--out', str(out)]), 0)
            self.assertEqual(json.loads(out.read_text())['rows'][0]['name'], 'Cash')
            self.assertNotIn('Pay Cash', output.getvalue())
            self.assertIn('PAYMENT_TABLE_COPIED_NOT_SAVED', output.getvalue())
            self.assertEqual(self.writes(), 0)

    def test_existing_output_or_intent_stops_before_keyboard_entrypoint(self):
        for existing in ['result', 'intent', 'diagnostic']:
            with tempfile.TemporaryDirectory() as folder, patch('sys.stderr', new_callable=io.StringIO), \
                 patch('fakturama_automation.cli.copy_payment_table') as entry:
                out = Path(folder) / 'copied.json'
                path = (out if existing == 'result' else out.with_name(out.stem + '-headers.zip')
                        if existing == 'diagnostic' else out.with_name(out.name + '.intent.json'))
                path.write_text('Existing evidence')
                self.assertNotEqual(main(['payment-copy', '--pid', '51052', '--window-handle', '658798',
                                         '--method', 'Bank Transfer', '--tesseract', 'tesseract.exe', '--out', str(out)]), 0)
                entry.assert_not_called()
                self.assertEqual(path.read_text(), 'Existing evidence')

    def test_cli_failures_write_safe_evidence_and_retain_intent(self):
        for error in [ReviewRequired('payment_copy_click_payment_table_first'), RuntimeError('PRIVATE')]:
            with tempfile.TemporaryDirectory() as folder, patch('sys.stderr', new_callable=io.StringIO) as errors, \
                 patch('fakturama_automation.cli.copy_payment_table', side_effect=error):
                out = Path(folder) / 'copied.json'
                self.assertEqual(main(['payment-copy', '--pid', '51052', '--window-handle', '658798',
                                      '--method', 'Bank Transfer', '--tesseract', 'tesseract.exe', '--out', str(out)]), 2)
                self.assertTrue(out.with_name(out.name + '.intent.json').exists())
                self.assertNotIn('PRIVATE', out.read_text() + errors.getvalue())
                self.assertEqual(json.loads(out.read_text())['status'], 'REVIEW_REQUIRED')
