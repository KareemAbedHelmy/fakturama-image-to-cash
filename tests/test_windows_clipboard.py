import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.windows_clipboard import (
    DWORD, GUITHREADINFO, MAX_CLIPBOARD_BYTES, WindowsClipboard, decode_clipboard_text,
)


class WindowsClipboardTests(unittest.TestCase):
    def setUp(self):
        self.pid, self.main, self.grid, self.owner = 51052, 658798, 855816, 123
        self.owner_pid, self.current_sequence = self.pid, 101
        self.raw = ('true\tCash\tPay Cash\t0.0\t0\t0\x00').encode('utf-16-le')
        self.buffer = ctypes.create_string_buffer(self.raw)
        self.user = SimpleNamespace(**{name: Mock() for name in (
            'GetForegroundWindow', 'GetWindowThreadProcessId', 'GetGUIThreadInfo', 'IsChild',
            'GetAsyncKeyState', 'GetClipboardSequenceNumber', 'GetClipboardOwner',
            'OpenClipboard', 'CloseClipboard', 'IsClipboardFormatAvailable', 'GetClipboardData')})
        self.kernel = SimpleNamespace(**{name: Mock() for name in ('GlobalSize', 'GlobalLock', 'GlobalUnlock')})
        self.user.GetForegroundWindow.return_value = self.main
        self.user.IsChild.return_value = 1
        self.user.GetAsyncKeyState.return_value = 0
        self.user.GetClipboardSequenceNumber.side_effect = lambda: self.current_sequence
        self.user.GetClipboardOwner.return_value = self.owner
        self.user.OpenClipboard.return_value = self.user.CloseClipboard.return_value = 1
        self.user.IsClipboardFormatAvailable.return_value = 1
        self.user.GetClipboardData.return_value = 99
        self.kernel.GlobalSize.return_value = len(self.raw)
        self.kernel.GlobalLock.return_value = ctypes.addressof(self.buffer)
        self.kernel.GlobalUnlock.return_value = 0  # Reaching lock count zero is success too.
        def identity(hwnd, pointer):
            ctypes.cast(pointer, ctypes.POINTER(DWORD)).contents.value = self.owner_pid if hwnd == self.owner else self.pid
            return 77
        self.user.GetWindowThreadProcessId.side_effect = identity
        def focus(thread, pointer):
            info = ctypes.cast(pointer, ctypes.POINTER(GUITHREADINFO)).contents
            self.assertEqual(info.cbSize, ctypes.sizeof(GUITHREADINFO))
            info.hwndActive, info.hwndFocus = self.main, self.grid
            return 1
        self.user.GetGUIThreadInfo.side_effect = focus
        self.driver = WindowsClipboard(user32=self.user, kernel32=self.kernel)

    def test_gui_abi_and_focus_use_foreground_thread_not_python_thread(self):
        self.assertEqual(ctypes.sizeof(DWORD), 4)
        self.assertEqual(ctypes.sizeof(GUITHREADINFO), 72 if ctypes.sizeof(ctypes.c_void_p) == 8 else 48)
        self.driver.assert_focus(self.main, self.grid, self.pid)
        self.user.GetGUIThreadInfo.assert_called_once()
        self.assertEqual(self.user.GetGUIThreadInfo.call_args.args[0], 77)
        self.user.GetClipboardData.assert_not_called()

    def test_wrong_foreground_pid_child_focus_menu_or_modifier_blocks(self):
        for mode in ['foreground', 'pid', 'child', 'focus', 'menu', 'modifier']:
            self.setUp()
            expected_pid = self.pid
            if mode == 'foreground': self.user.GetForegroundWindow.return_value = 999
            elif mode == 'pid': expected_pid += 1
            elif mode == 'child': self.user.IsChild.return_value = 0
            elif mode == 'modifier': self.user.GetAsyncKeyState.return_value = 0x8000
            else:
                original = self.user.GetGUIThreadInfo.side_effect
                def changed(thread, pointer):
                    original(thread, pointer)
                    info = ctypes.cast(pointer, ctypes.POINTER(GUITHREADINFO)).contents
                    if mode == 'focus': info.hwndFocus = 999
                    else: info.hwndMenuOwner = 999
                    return 1
                self.user.GetGUIThreadInfo.side_effect = changed
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired):
                self.driver.assert_focus(self.main, self.grid, expected_pid)
            self.user.GetClipboardData.assert_not_called()

    def test_new_application_owned_unicode_copy_is_bounded_unlocked_and_closed(self):
        result = self.driver.read_new(self.pid, self.main, 100)
        self.assertEqual(result.text, 'true\tCash\tPay Cash\t0.0\t0\t0')
        self.assertEqual((result.sequence, result.owner_handle, result.owner_pid), (101, self.owner, self.pid))
        self.kernel.GlobalUnlock.assert_called_once_with(99)
        self.user.CloseClipboard.assert_called_once_with()
        self.user.GetClipboardData.assert_called_once_with(13)

    def test_delayed_rendering_can_advance_sequence_during_get_data(self):
        self.current_sequence = 100
        def rendered(format):
            self.current_sequence = 101
            return 99
        self.user.GetClipboardData.side_effect = rendered
        self.assertEqual(self.driver.read_new(self.pid, self.main, 100).sequence, 101)

    def test_foreign_owner_never_opens_clipboard_or_reads_memory(self):
        self.owner_pid += 1
        with self.assertRaisesRegex(ReviewRequired, 'owner_not_fakturama'):
            self.driver.read_new(self.pid, self.main, 100)
        self.user.OpenClipboard.assert_not_called()
        self.user.GetClipboardData.assert_not_called()
        self.kernel.GlobalLock.assert_not_called()

    def test_unchanged_or_mid_read_replaced_data_is_not_returned(self):
        for mode in ['unchanged', 'sequence', 'owner']:
            self.setUp()
            if mode == 'unchanged': self.current_sequence = 100
            elif mode == 'sequence': self.user.GetClipboardSequenceNumber.side_effect = [101, 102]
            else: self.user.GetClipboardOwner.side_effect = [self.owner, self.owner, self.owner, self.owner + 1]
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired):
                self.driver.read_new(self.pid, self.main, 100)
            self.kernel.GlobalUnlock.assert_called_once()
            self.user.CloseClipboard.assert_called_once()

    def test_invalid_memory_sizes_never_lock_or_read_and_always_close(self):
        for size in [0, 1, 3, True, MAX_CLIPBOARD_BYTES + 2]:
            self.setUp()
            self.kernel.GlobalSize.return_value = size
            with self.subTest(size=size), self.assertRaisesRegex(ReviewRequired, 'invalid_unicode_size'):
                self.driver.read_new(self.pid, self.main, 100)
            self.kernel.GlobalLock.assert_not_called()
            self.user.CloseClipboard.assert_called_once()

    def test_busy_missing_data_or_lock_failure_cleanup(self):
        for mode in ['busy', 'format', 'data', 'lock', 'close']:
            self.setUp()
            if mode == 'busy': self.user.OpenClipboard.return_value = 0
            elif mode == 'format': self.user.IsClipboardFormatAvailable.return_value = 0
            elif mode == 'data': self.user.GetClipboardData.return_value = 0
            elif mode == 'lock': self.kernel.GlobalLock.return_value = 0
            else: self.user.CloseClipboard.return_value = 0
            with self.subTest(mode=mode), self.assertRaises(ReviewRequired):
                self.driver.read_new(self.pid, self.main, 100)
            self.assertEqual(self.user.CloseClipboard.call_count, int(mode != 'busy'))
            self.assertEqual(self.kernel.GlobalUnlock.call_count, int(mode == 'close'))

    def test_unicode_termination_alignment_padding_and_bad_encoding(self):
        raw = '\u0100A\x00'.encode('utf-16-le') + b'PRIVATE allocation padding'
        # Native GlobalSize is even; pad allocation if needed. Bytes after the
        # aligned string terminator must never be decoded or serialized.
        raw += b'\x00' if len(raw) % 2 else b''
        self.assertEqual(decode_clipboard_text(raw), '\u0100A')
        for raw in [b'', b'x', b'A\x00', b'\x00\xd8\x00\x00', b'x' * (MAX_CLIPBOARD_BYTES + 2)]:
            with self.subTest(size=len(raw)), self.assertRaises(ReviewRequired): decode_clipboard_text(raw)

    def test_invalid_scope_or_unavailable_sequence_stops(self):
        for value in [0, True, -1, 0x100000000]:
            with self.assertRaises(ReviewRequired): self.driver.read_new(self.pid, self.main, value)
            self.user.GetClipboardData.assert_not_called()
        self.user.GetClipboardSequenceNumber.side_effect = None
        self.user.GetClipboardSequenceNumber.return_value = 0
        with self.assertRaisesRegex(ReviewRequired, 'sequence_unavailable'): self.driver.sequence()
        with patch('fakturama_automation.windows_clipboard.platform.system', return_value='Linux'):
            with self.assertRaisesRegex(ReviewRequired, 'windows_required'): WindowsClipboard()
