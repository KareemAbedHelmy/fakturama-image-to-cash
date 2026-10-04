"""Window discovery and exact UIA primitives: exactly-one control lookup, window identity and pattern helpers."""

import platform
import time
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Callable, TypeVar

from .errors import ReviewRequired


@dataclass(frozen=True)
class Locator:
    control_type: str
    name: str | None = None
    automation_id: str | None = None

    def __post_init__(self):
        if self.name is None and not self.automation_id:
            raise ValueError("locator_requires_identity")


def unique_control(scope, locator: Locator):
    matches = []
    for control in scope.descendants(control_type=locator.control_type):
        info = control.element_info
        if locator.name is not None and info.name != locator.name:
            continue
        if locator.automation_id is not None and info.automation_id != locator.automation_id:
            continue
        if control.is_visible() and enabled_within_scope(control, scope):
            matches.append(control)
    if len(matches) != 1:
        raise ReviewRequired("control_missing_or_ambiguous")
    return matches[0]


def enabled_within_scope(control, scope) -> bool:
    """Check every ancestor: SWT may mark a child of a disabled toolbar enabled."""
    current = control
    try:
        for _ in range(32):
            if current is None or not current.is_enabled():
                return False
            if current == scope:
                return True
            current = current.parent()
    except Exception:
        raise ReviewRequired("control_ancestry_unavailable") from None
    raise ReviewRequired("control_ancestry_unavailable")


def set_value_verified(scope, locator: Locator, value: str):
    control = unique_control(scope, locator)
    try:
        control.set_edit_text(value)
        actual = control.get_value()
    except Exception:
        raise ReviewRequired("uia_value_pattern_unavailable") from None
    if actual != value:
        raise ReviewRequired("uia_value_readback_mismatch")


def invoke_once(scope, locator: Locator):
    control = unique_control(scope, locator)
    try:
        control.invoke()
    except Exception:
        # Invocation may have succeeded. Don't click/retry as a fallback.
        raise ReviewRequired("uia_invoke_uncertain") from None


T = TypeVar("T")


def wait_stable(observe: Callable[[], T], ready: Callable[[T], bool], *, timeout: float = 10,
                interval: float = 0.2, stable_reads: int = 3) -> T:
    """Read-only polls. ready must include search completion, not just empty rows."""
    if timeout <= 0 or interval <= 0 or stable_reads < 2:
        raise ValueError("invalid_wait_configuration")
    deadline = time.monotonic() + timeout
    previous = object()
    count = 0
    while time.monotonic() < deadline:
        current = observe()
        if ready(current):
            count = count + 1 if current == previous else 1
            if count >= stable_reads:
                return current
        else:
            count = 0
        previous = current
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    raise ReviewRequired("observation_timeout")


def inspect_window(pid: int, handle: int) -> dict:
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_uia")
    try:
        from pywinauto import Application
    except ImportError:
        raise ReviewRequired("windows_dependency_missing") from None
    app = Application(backend="uia").connect(process=pid, timeout=10)
    root = app.window(handle=handle).wrapper_object()
    if root.element_info.process_id != pid:
        raise ReviewRequired("window_process_mismatch")
    if not root.is_visible():
        # A process's reported MainWindowHandle may be a hidden helper pane.
        # Do not present its empty tree as an editor inspection.
        raise ReviewRequired("window_not_visible")
    # Caller explicitly supplies process and native handle; inspection is read-only.
    count = [0]
    def visit(control, depth):
        count[0] += 1
        if count[0] > 5000 or depth > 30:
            return {"truncated": True}
        info = control.element_info
        rect = control.rectangle()
        return {"name": info.name, "automation_id": info.automation_id,
                "control_type": info.control_type, "class_name": info.class_name,
                "visible": control.is_visible(), "enabled": control.is_enabled(),
                "patterns": observed_patterns(control),
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
                "children": [visit(child, depth+1) for child in control.children()[:500]]}
    return {"pid": pid, "window_handle": handle, "tree": visit(root, 0)}


def list_process_windows(pid: int) -> list[dict]:
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_uia")
    try:
        from pywinauto import Application
    except ImportError:
        raise ReviewRequired("windows_dependency_missing") from None
    app = Application(backend="uia").connect(process=pid, timeout=10)
    return [{"name": window.element_info.name, "handle": window.handle,
             "pid": window.element_info.process_id} for window in app.windows()]


def observed_patterns(control) -> dict:
    """Read-only pattern availability and current values. Never invoke a pattern."""
    result = {}
    for name, iface, attributes in [
        ("value", "iface_value", ("CurrentValue", "CurrentIsReadOnly")),
        ("invoke", "iface_invoke", ()),
        ("toggle", "iface_toggle", ("CurrentToggleState",)),
        ("selection_item", "iface_selection_item", ("CurrentIsSelected",)),
        ("expand_collapse", "iface_expand_collapse", ("CurrentExpandCollapseState",)),
        ("grid", "iface_grid", ("CurrentRowCount", "CurrentColumnCount")),
    ]:
        try:
            pattern = getattr(control, iface)
            if pattern is None:
                continue
            observed = {attr: getattr(pattern, attr) for attr in attributes}
            # COM values are expected to be strings/numbers/bools; don't serialize arbitrary objects.
            result[name] = {key: value if isinstance(value, (str, int, float, bool, type(None))) else "unavailable"
                            for key, value in observed.items()}
        except Exception:
            continue
    return result


def window_inventory() -> list[dict]:
    """Read-only diagnostic inventory, including rejected discovery candidates.

    Only explicit diagnostic calls should publish this list, since visible window
    titles can contain private data. Does not inspect descendant controls.
    """
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_uia")
    try:
        from pywinauto import Desktop
    except ImportError:
        raise ReviewRequired("windows_dependency_missing") from None
    rows = []
    executables: dict[int, str | None] = {}
    for window in Desktop(backend="uia").windows():
        try:
            name = window.element_info.name
            pid = window.element_info.process_id
            visible = window.is_visible()
            if pid not in executables:
                try:
                    executables[pid] = process_executable_name(pid)
                except Exception:
                    executables[pid] = None
            executable = executables[pid]
            accepted = visible and is_fakturama_candidate(name, executable)
            if not visible:
                reason = "not_visible"
            elif accepted:
                reason = None
            elif not executable:
                reason = "executable_identity_unavailable"
            elif "fakturama" not in name.casefold():
                reason = "title_not_matched"
            else:
                reason = "executable_not_matched"
            rows.append({"name": name, "handle": window.handle, "pid": pid,
                         "executable": executable, "visible": visible,
                         "candidate": accepted, "rejection_reason": reason})
        except Exception:
            rows.append({"error": "window_metadata_unavailable"})
    return rows


def _native_top_level_windows() -> list[tuple[int, int, str]]:
    """(handle, pid, title) of visible top-level windows via Win32 only.

    Unlike a UIA desktop walk, this cannot hang on an unrelated application whose
    accessibility provider is not responding.
    """
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    rows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _):
        if user32.IsWindowVisible(handle):
            length = user32.GetWindowTextLengthW(handle)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            rows.append((int(handle), int(pid.value), buffer.value))
        return True

    user32.EnumWindows(visit, 0)
    return rows


def discover_fakturama_windows() -> list[dict]:
    """Auto-discovery stays strict; diagnostics retain reasons for rejections."""
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_uia")
    found, executables = [], {}
    for handle, pid, name in _native_top_level_windows():
        if pid not in executables:
            try:
                executables[pid] = process_executable_name(pid)
            except Exception:
                executables[pid] = None
        if is_fakturama_candidate(name, executables[pid]):
            found.append({"name": name, "handle": handle, "pid": pid, "executable": executables[pid]})
    return found


def is_fakturama_candidate(title: str, executable: str | None) -> bool:
    """A project folder title alone cannot identify the accounting application."""
    if not executable:
        return False
    name = PureWindowsPath(executable).name.casefold()
    if name == "fakturama.exe":
        return True  # Native app identity is sufficient even when its title is blank.
    return name in {"java.exe", "javaw.exe"} and "fakturama" in title.casefold()


def process_executable_name(pid: int) -> str | None:
    """Read executable identity with limited-query access, returning basename only.

    Uses QueryFullProcessImageNameW; no process memory reads, writes, or elevated
    permissions. Inaccessible/exited processes are not automatic candidates.
    """
    if platform.system() != "Windows" or pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
            return None
        return PureWindowsPath(buffer.value).name
    finally:
        kernel.CloseHandle(handle)
