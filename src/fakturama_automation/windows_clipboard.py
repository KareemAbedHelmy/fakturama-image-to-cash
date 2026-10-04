"""Windows focus checks and bounded reads of newly copied application text. No clipboard writes; the
caller sends the Copy shortcut.
"""

import ctypes
import platform
from dataclasses import dataclass

from .errors import ReviewRequired
from .input_io import MAX_JSON_BYTES

MAX_CLIPBOARD_BYTES = 2 * (MAX_JSON_BYTES + 1)
HWND = ctypes.c_void_p
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32


class RECT(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("flags", DWORD)] + [
        (name, HWND) for name in ("hwndActive", "hwndFocus", "hwndCapture",
                                 "hwndMenuOwner", "hwndMoveSize", "hwndCaret")
    ] + [("rcCaret", RECT)]


@dataclass(frozen=True)
class ClipboardCopy:
    text: str
    sequence: int
    owner_handle: int
    owner_pid: int


def decode_clipboard_text(raw: bytes) -> str:
    """Decode only the terminated UTF-16LE text, never allocation padding."""
    if not isinstance(raw, bytes) or not 2 <= len(raw) <= MAX_CLIPBOARD_BYTES or len(raw) % 2:
        raise ReviewRequired("clipboard_invalid_unicode_size")
    end = next((i for i in range(0, len(raw), 2) if raw[i:i + 2] == b"\x00\x00"), None)
    if end is None:
        raise ReviewRequired("clipboard_unicode_not_terminated")
    try:
        return raw[:end].decode("utf-16-le")
    except UnicodeError:
        raise ReviewRequired("clipboard_invalid_unicode_text") from None


class WindowsClipboard:
    def __init__(self, *, user32=None, kernel32=None):
        # Injectable native functions are for local tests, not user-file input.
        if user32 is None or kernel32 is None:
            if platform.system() != "Windows":
                raise ReviewRequired("windows_required_for_clipboard")
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32, self.kernel32 = user32, kernel32
        for dll, name, args, result in [
            (user32, "GetForegroundWindow", [], HWND),
            (user32, "GetWindowThreadProcessId", [HWND, ctypes.POINTER(DWORD)], DWORD),
            (user32, "GetGUIThreadInfo", [DWORD, ctypes.POINTER(GUITHREADINFO)], BOOL),
            (user32, "IsChild", [HWND, HWND], BOOL),
            (user32, "GetAsyncKeyState", [ctypes.c_int], ctypes.c_int16),
            (user32, "GetClipboardSequenceNumber", [], DWORD),
            (user32, "GetClipboardOwner", [], HWND),
            (user32, "OpenClipboard", [HWND], BOOL),
            (user32, "CloseClipboard", [], BOOL),
            (user32, "IsClipboardFormatAvailable", [ctypes.c_uint], BOOL),
            (user32, "GetClipboardData", [ctypes.c_uint], HWND),
            (kernel32, "GlobalSize", [HWND], ctypes.c_size_t),
            (kernel32, "GlobalLock", [HWND], ctypes.c_void_p),
            (kernel32, "GlobalUnlock", [HWND], BOOL),
        ]:
            function = getattr(dll, name)
            function.argtypes, function.restype = args, result

    def _pid_thread(self, handle):
        pid = DWORD()
        thread = self.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if not handle or not thread or not pid.value:
            raise ReviewRequired("clipboard_window_identity_unavailable")
        return pid.value, int(thread)

    def assert_focus(self, main_handle, grid_handle, pid, *, allow_caret_blink=False):
        if type(allow_caret_blink) is not bool:
            raise ValueError('invalid_caret_focus_policy')
        if any(type(value) is not int or value <= 0 for value in (main_handle, grid_handle, pid)):
            raise ReviewRequired("payment_copy_invalid_native_scope")
        if self.user32.GetForegroundWindow() != main_handle:
            raise ReviewRequired("payment_copy_keep_fakturama_in_foreground")
        actual_pid, thread = self._pid_thread(main_handle)
        grid_pid, grid_thread = self._pid_thread(grid_handle)
        if actual_pid != pid or grid_pid != pid or grid_thread != thread or not self.user32.IsChild(main_handle, grid_handle):
            raise ReviewRequired("payment_copy_native_scope_mismatch")
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(info)
        if not self.user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
            raise ReviewRequired("payment_copy_keyboard_focus_unavailable")
        if info.hwndActive != main_handle or info.hwndFocus != grid_handle:
            raise ReviewRequired("payment_copy_click_payment_table_first")
        # A text Edit normally has GUI_CARETBLINKING (0x1). Only the explicit
        # date-entry caller permits it; clipboard/grid callers keep the default.
        blocked_flags = info.flags & ~0x1 if allow_caret_blink else info.flags
        if blocked_flags or info.hwndMenuOwner or info.hwndCapture or info.hwndMoveSize:
            raise ReviewRequired("payment_copy_menu_or_drag_active")
        # Physical modifier keys can alter the fixed shortcuts being sent.
        if any(self.user32.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise ReviewRequired("payment_copy_release_modifier_keys")

    def sequence(self):
        value = self.user32.GetClipboardSequenceNumber()
        if type(value) is not int or not 0 < value <= 0xFFFFFFFF:
            raise ReviewRequired("clipboard_sequence_unavailable")
        return value

    def _owner(self, expected_pid):
        owner = self.user32.GetClipboardOwner()
        if not owner:
            raise ReviewRequired("clipboard_owner_unavailable")
        pid, _ = self._pid_thread(owner)
        if pid != expected_pid:
            raise ReviewRequired("clipboard_owner_not_fakturama")
        return int(owner)

    def read_new(self, expected_pid, requestor_handle, previous_sequence):
        """Read only Fakturama-owned text; return it only after a fresh change.

        GetClipboardData may render delayed data and advance the sequence.
        Unchanged content is discarded, never returned as a successful copy.
        """
        if (any(type(value) is not int or value <= 0 for value in (expected_pid, requestor_handle, previous_sequence))
                or previous_sequence > 0xFFFFFFFF):
            raise ReviewRequired("clipboard_invalid_scope")
        if self._pid_thread(requestor_handle)[0] != expected_pid:
            raise ReviewRequired("clipboard_requestor_process_mismatch")
        owner = self._owner(expected_pid)  # Check BEFORE opening or reading data.
        if not self.user32.OpenClipboard(requestor_handle):
            raise ReviewRequired("clipboard_busy")
        try:
            if self._owner(expected_pid) != owner:
                raise ReviewRequired("clipboard_changed_during_read")
            if not self.user32.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT
                raise ReviewRequired("clipboard_unicode_unavailable")
            memory = self.user32.GetClipboardData(13)
            if not memory:
                raise ReviewRequired("clipboard_data_unavailable")
            size = self.kernel32.GlobalSize(memory)
            if type(size) is not int or not 2 <= size <= MAX_CLIPBOARD_BYTES or size % 2:
                raise ReviewRequired("clipboard_invalid_unicode_size")
            pointer = self.kernel32.GlobalLock(memory)
            if not pointer:
                raise ReviewRequired("clipboard_memory_unavailable")
            try:
                raw = ctypes.string_at(pointer, size)
            finally:
                self.kernel32.GlobalUnlock(memory)
            sequence = self.sequence()
            if self._owner(expected_pid) != owner:
                raise ReviewRequired("clipboard_changed_during_read")
            if sequence == previous_sequence:
                raise ReviewRequired("clipboard_unchanged")
            text = decode_clipboard_text(raw)
        finally:
            if not self.user32.CloseClipboard():
                raise ReviewRequired("clipboard_close_failed")
        if self.sequence() != sequence or self._owner(expected_pid) != owner:
            raise ReviewRequired("clipboard_changed_during_read")
        return ClipboardCopy(text, sequence, owner, expected_pid)
