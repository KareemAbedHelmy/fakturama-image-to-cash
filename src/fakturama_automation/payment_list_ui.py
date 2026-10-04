"""Capture the terms-of-payment list: one scoped frame plus bounded UIA/Win32 metadata. Read-only."""

import io
import unicodedata

from PIL import Image

from .debtor_ui import _tab_items
from .errors import ReviewRequired
from .extraction import MAX_IMAGE_BYTES, MAX_PIXELS
from .native_ui import inspect_native_controls
from .payment_ui import _payment_root
from .selector_ui import exactly_one
from .uia import Locator, observed_patterns, process_executable_name, unique_control

LIST_NAME = "terms of payment"


def _rectangle(control):
    rect = control.rectangle()
    result = (rect.left, rect.top, rect.right, rect.bottom)
    if min(result[2] - result[0], result[3] - result[1]) < 32:
        raise ReviewRequired("payment_list_expand_panel_before_capture")
    if (result[2] - result[0]) * (result[3] - result[1]) > MAX_PIXELS:
        raise ReviewRequired("image_dimensions_out_of_range")
    return result


def _snapshot(scope, pid):
    count = 0
    truncated = False

    def visit(control, depth):
        nonlocal count, truncated
        if count >= 5000 or depth > 30:
            truncated = True
            return {"truncated": True}
        count += 1
        info = control.element_info
        if info.process_id != pid:
            raise ReviewRequired("payment_list_process_mismatch")
        rect = control.rectangle()
        children = control.children()
        if len(children) > 500:
            truncated = True
        return {"name": info.name, "class_name": info.class_name,
                "control_type": info.control_type, "handle": control.handle,
                "visible": control.is_visible(), "enabled": control.is_enabled(),
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
                "patterns": observed_patterns(control),
                "children": [visit(c, depth + 1) for c in children[:500]]}

    tree = visit(scope, 0)
    return {"tree": tree, "truncated": truncated}


def capture_payment_list_scope(root, native_factory, root_guard):
    """Dependency-injected capture; guards surround all reads and the frame."""
    pid = root.element_info.process_id

    def state():
        root_guard()
        tabs = _tab_items(root)
        for role, names in [("order", {"New Order", "*New Order"}),
                            ("debtor", {"New Debtor", "*New Debtor"})]:
            exactly_one([c for c in tabs if c.element_info.name in names],
                        role + "_tab", "payment_list")
        pane = unique_control(root, Locator("Pane", name=LIST_NAME))
        if (pane.element_info.process_id != pid or not pane.handle
                or pane.element_info.class_name != "SWT_Window0"):
            raise ReviewRequired("payment_list_pane_identity_mismatch")
        search_label = unique_control(pane, Locator("Text", name="Search:"))
        if search_label.element_info.process_id != pid:
            raise ReviewRequired("payment_list_process_mismatch")
        search = unique_control(search_label.parent(), Locator("Edit", name=""))
        if search.element_info.process_id != pid:
            raise ReviewRequired("payment_list_process_mismatch")
        try:
            query = search.iface_value.CurrentValue
        except Exception:
            raise ReviewRequired("payment_list_query_unavailable") from None
        if (not isinstance(query, str) or len(query) > 1000
                or any(unicodedata.category(c).startswith("C") for c in query)):
            raise ReviewRequired("payment_list_query_invalid")
        root_rect, pane_rect = _rectangle(root), _rectangle(pane)
        if not (root_rect[0] <= pane_rect[0] < pane_rect[2] <= root_rect[2]
                and root_rect[1] <= pane_rect[1] < pane_rect[3] <= root_rect[3]):
            raise ReviewRequired("payment_list_pane_outside_window")
        return pane, (root.handle, pane.handle, root_rect, pane_rect, query)

    pane, before = state()
    uia = _snapshot(pane, pid)
    native = native_factory(pane.handle)
    if (native.handle != pane.handle or native.element_info.process_id != pid
            or native.class_name() != "SWT_Window0" or not native.is_visible()
            or not native.is_enabled()):
        raise ReviewRequired("payment_list_native_identity_mismatch")
    native_report = inspect_native_controls(native, pid, prefix="payment_list_native")
    _, after_reads = state()
    if after_reads != before:
        raise ReviewRequired("payment_list_changed_during_capture")
    image = pane.capture_as_image()
    _, after = state()
    if after != before:
        raise ReviewRequired("payment_list_changed_during_capture")
    rect = before[3]
    if image is None or image.size != (rect[2] - rect[0], rect[3] - rect[1]):
        raise ReviewRequired("payment_list_capture_scale_mismatch")
    clean = Image.frombytes("RGB", image.size, image.convert("RGB").tobytes())
    output = io.BytesIO()
    clean.save(output, format="PNG")
    if output.tell() > MAX_IMAGE_BYTES:
        raise ReviewRequired("image_too_large")
    return output.getvalue(), {
        "status": "UNVERIFIED_PAYMENT_LIST_EVIDENCE", "pid": pid,
        "window_handle": before[0], "pane_handle": before[1], "query": before[4],
        "image_size": list(image.size), "uia": uia, "win32": native_report,
        "source_order_tab_present": True, "source_debtor_tab_present": True,
        "source_order_number_verified": False, "source_debtor_id_verified": False,
        "all_rows_enumerated": False, "master_absence_verified": False,
        "persistence_verified": False,
        "note": "Read-only list evidence. Counts, a blank search and OCR cannot prove complete lookup or persistence. No panel, search, selection or save action was invoked."}


def capture_payment_list(pid, handle):
    root = _payment_root(pid, handle)
    title = root.element_info.name
    from pywinauto import Desktop

    def guard():
        try:
            if (root.handle != handle or root.element_info.process_id != pid
                    or root.element_info.class_name != "SWT_Window0"
                    or root.element_info.name != title or not root.is_visible()
                    or not root.is_enabled()):
                raise ReviewRequired("payment_list_window_identity_changed")
            executable = process_executable_name(pid)
            if not isinstance(executable, str) or executable.casefold() != "fakturama.exe":
                raise ReviewRequired("payment_list_executable_identity_mismatch")
            if not root.is_active():
                raise ReviewRequired("payment_list_keep_fakturama_in_foreground")
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired("payment_list_scope_unavailable") from None

    def native_factory(hwnd):
        return Desktop(backend="win32").window(handle=hwnd).wrapper_object()

    return capture_payment_list_scope(root, native_factory, guard)
