"""Windows inspection and exact UIA primitives, NOT the live desktop workflow.

No invented locators, absolute coordinates, keyboard interpolation, or first-match
fallback. Native pattern support must be verified on the installed Fakturama.
"""

import platform
import time
from dataclasses import dataclass
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
        if control.is_visible() and control.is_enabled():
            matches.append(control)
    if len(matches) != 1:
        raise ReviewRequired("control_missing_or_ambiguous")
    return matches[0]


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


def discover_fakturama_windows() -> list[dict]:
    """Discovery is read-only; no automatic attachment/mutation of any candidate."""
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_uia")
    try:
        from pywinauto import Desktop
    except ImportError:
        raise ReviewRequired("windows_dependency_missing") from None
    rows = []
    for window in Desktop(backend="uia").windows():
        try:
            name = window.element_info.name
            if "fakturama" in name.casefold() and window.is_visible():
                rows.append({"name": name, "handle": window.handle,
                             "pid": window.element_info.process_id})
        except Exception:
            continue
    return rows
