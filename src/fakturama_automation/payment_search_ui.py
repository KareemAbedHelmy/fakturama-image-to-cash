"""Set an exact literal search in the payment list and capture stable images. Never saves."""

import hashlib
import time

from .errors import ReviewRequired
from .matching import PAYMENT_CODES
from .payment_copy_ui import _binding, _column_image_evidence, _root_guard
from .payment_list_ui import LIST_NAME, _rectangle
from .payment_ui import _payment_root
from .uia import Locator, unique_control


def _search(root):
    pane = unique_control(root, Locator("Pane", name=LIST_NAME))
    label = unique_control(pane, Locator("Text", name="Search:"))
    field = unique_control(label.parent(), Locator("Edit", name=""))
    if any(c.element_info.process_id != root.element_info.process_id for c in (pane, label, field)):
        raise ReviewRequired("payment_search_process_mismatch")
    if type(field.handle) is not int or field.handle <= 0:
        raise ReviewRequired("payment_search_field_identity_unavailable")
    return field, (field.handle, _rectangle(field))


def search_payment_list_scope(root, method, executable, root_guard, *, diagnostics=None, sleep=time.sleep):
    if not isinstance(method, str) or method not in PAYMENT_CODES:
        raise ReviewRequired("payment_search_unsupported_method")
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault("progress", {})
    progress.update(phase="initial_scope", search_writes_attempted=0)
    root_guard()
    _, initial = _binding(root)  # Blank search only; refuse partial/replayed search.
    field, field_identity = _search(root)
    try:
        pattern = field.iface_value
        if pattern.CurrentIsReadOnly:
            raise ReviewRequired("payment_search_field_read_only")
        if pattern.CurrentValue != "":
            raise ReviewRequired("payment_search_initial_query_changed")
    except ReviewRequired:
        raise
    except Exception:
        raise ReviewRequired("payment_search_value_pattern_unavailable") from None

    def state(expected_query):
        root_guard()
        grid, metadata = _binding(root, expected_query=expected_query)
        current, identity = _search(root)
        if metadata[:-1] != initial[:-1] or identity != field_identity:
            raise ReviewRequired("payment_search_scope_changed")
        return grid, current

    _, field = state("")
    try:
        pattern = field.iface_value
        if pattern.CurrentIsReadOnly or pattern.CurrentValue != "":
            raise ReviewRequired("payment_search_field_changed_before_write")
    except ReviewRequired:
        raise
    except Exception:
        raise ReviewRequired("payment_search_value_pattern_unavailable") from None
    progress.update(phase="write_search", search_writes_attempted=1)
    try:
        pattern.SetValue(method)
    except Exception:
        raise ReviewRequired("payment_search_write_uncertain") from None
    state(method)  # UIA readback must be exact. No keystroke fallback or retry.
    frames = diagnostics.setdefault("frames", [])
    for index in range(2):
        progress["phase"] = "capture_result_" + str(index + 1)
        # Spacing is an observation interval, not an assertion of completion.
        sleep(0.5)
        grid, _ = state(method)
        png, evidence = _column_image_evidence(grid, executable)
        state(method)
        frames.append({"name": "filtered_grid_" + str(index + 1), "png": png, "evidence": evidence})
        if evidence["status"] == "REVIEW_REQUIRED":
            raise ReviewRequired(evidence["code"])
    if frames[0]["png"] != frames[1]["png"]:
        raise ReviewRequired("payment_search_images_changed_review")
    state(method)
    progress["phase"] = "complete"
    return {"status": "PAYMENT_SEARCH_OBSERVED_NOT_SAVED", "query": method,
            "query_readback_verified": True, "stable_frame_count": 2,
            "image_sha256": hashlib.sha256(frames[-1]["png"]).hexdigest(),
            "pid": root.element_info.process_id, "window_handle": initial[0],
            "pane_handle": initial[1], "grid_handle": initial[2],
            "column_order_verified": True, "scope_rechecked": True,
            "search_completion_verified": False, "all_rows_enumerated": False,
            "master_absence_verified": False, "payment_code_verified": False,
            "persistence_verified": False, "source_order_tab_present": True,
            "source_debtor_tab_present": True, "source_order_number_verified": False,
            "source_debtor_id_verified": False,
            "note": "One literal Search write, exact readback and two identical scoped grid images. Filter remains set. No keyboard, clipboard, editor-field write or Save. Stable images/OCR do not prove completed search, full enumeration or absence."}


def search_payment_list(pid, handle, method, executable, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return search_payment_list_scope(root, method, executable, _root_guard(root, pid, handle),
                                     diagnostics=diagnostics)
