"""Start Fakturama if needed, put its main window in a known size and bring it to the front.

Uses Win32 only (no UIA walk), so it cannot hang on another application. The
executable comes from FAKTURAMA_EXE or the usual install folders; nothing is
launched through a shell and no workspace data is touched.
"""

import ctypes
import os
import re
import subprocess
import time
from pathlib import Path

from .errors import ReviewRequired
from .uia import discover_fakturama_windows

SW_RESTORE, SW_MAXIMIZE = 9, 3
SWP_NOZORDER, SWP_SHOWWINDOW = 0x0004, 0x0040
CANDIDATES = ('Fakturama2', 'Fakturama')


def find_executable():
    configured = os.environ.get('FAKTURAMA_EXE')
    if configured:
        path = Path(configured)
        if path.is_absolute() and path.is_file() and path.name.casefold() == 'fakturama.exe':
            return path
        raise ReviewRequired('launcher_fakturama_exe_invalid')
    roots = [os.environ.get(v) for v in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA')]
    for root in filter(None, roots):
        for folder in CANDIDATES:
            path = Path(root) / folder / 'Fakturama.exe'
            if path.is_file():
                return path
    raise ReviewRequired('launcher_fakturama_exe_not_found_set_FAKTURAMA_EXE')


def main_window():
    rows = [w for w in discover_fakturama_windows() if (w['name'] or '').startswith('Fakturama - ')]
    return rows[0] if len(rows) == 1 else None


def parse_window_mode(mode):
    """'maximize' or 'WIDTHxHEIGHT' (for example 1920x1080)."""
    if mode == 'maximize':
        return mode
    match = re.fullmatch(r'(\d{3,5})x(\d{3,5})', mode or '')
    if not match:
        raise ReviewRequired('launcher_window_mode_invalid')
    return int(match[1]), int(match[2])


def arrange(handle, mode='maximize'):
    """Restore, then maximize or place the window at 0,0 with a fixed outer size."""
    user32 = ctypes.windll.user32
    target = parse_window_mode(mode)
    user32.ShowWindow(handle, SW_RESTORE)
    if target == 'maximize':
        user32.ShowWindow(handle, SW_MAXIMIZE)
    else:
        user32.SetWindowPos(handle, 0, 0, 0, target[0], target[1], SWP_NOZORDER | SWP_SHOWWINDOW)
    time.sleep(1.0)


def bring_to_front(handle):
    """Make the window foreground; a tap of Alt lifts Windows' foreground lock if needed."""
    user32 = ctypes.windll.user32
    if user32.GetForegroundWindow() == handle:
        return True
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(handle)
    time.sleep(0.4)
    return user32.GetForegroundWindow() == handle


def ensure_running(timeout=180):
    """Return (window, launched). Starts Fakturama only if no main window exists."""
    window = main_window()
    if window:
        return window, False
    executable = find_executable()
    subprocess.Popen([str(executable)], cwd=str(executable.parent), shell=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window = main_window()
        if window and not ctypes.windll.user32.IsHungAppWindow(window['handle']):
            time.sleep(3)  # let the workbench finish loading its views
            return main_window() or window, True
        time.sleep(1)
    raise ReviewRequired('launcher_main_window_not_observed_check_for_a_startup_dialog')


def prepare(mode='maximize', timeout=180):
    window, launched = ensure_running(timeout)
    arrange(window['handle'], mode)
    bring_to_front(window['handle'])
    return {**window, 'launched': launched, 'window_mode': mode}
