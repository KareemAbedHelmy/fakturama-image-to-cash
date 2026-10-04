"""Select All / Copy from the terms-of-payment table and read the six copied columns. Changes the table
selection and the clipboard only.
"""

import io
import json
import math
import time
import zipfile
from dataclasses import asdict

from PIL import Image

from .debtor_ui import _tab_items
from .errors import ReviewRequired
from .matching import PAYMENT_CODES
from .payment_list_ui import LIST_NAME, _rectangle
from .payment_table import PAYMENT_COLUMNS, inspect_copied_payment_rows, observe_payment_columns, parse_payment_table
from .payment_ui import _payment_root
from .selector_ui import exactly_one
from .selector_vision import ocr_words
from .extraction import MAX_IMAGE_BYTES
from .uia import Locator, enabled_within_scope, process_executable_name, unique_control
from .windows_clipboard import WindowsClipboard

PENDING_COPY = {"clipboard_busy", "clipboard_owner_unavailable", "clipboard_owner_not_fakturama",
                "clipboard_unchanged", "clipboard_unicode_unavailable", "clipboard_data_unavailable"}


def _binding(root, *, expected_query=""):
    pid = root.element_info.process_id
    tabs = _tab_items(root)
    for role, names in [("order", {"New Order", "*New Order"}),
                        ("debtor", {"New Debtor", "*New Debtor"})]:
        exactly_one([c for c in tabs if c.element_info.name in names], role + "_tab", "payment_copy")
    pane = unique_control(root, Locator("Pane", name=LIST_NAME))
    label = unique_control(pane, Locator("Text", name="Search:"))
    if any(c.element_info.process_id != pid for c in (pane, label)):
        raise ReviewRequired("payment_copy_process_mismatch")
    search = unique_control(label.parent(), Locator("Edit", name=""))
    if search.element_info.process_id != pid:
        raise ReviewRequired("payment_copy_process_mismatch")
    try:
        query = search.iface_value.CurrentValue
    except Exception:
        raise ReviewRequired("payment_copy_query_unavailable") from None
    if query != expected_query:
        raise ReviewRequired("payment_copy_clear_search_manually_first" if expected_query == ""
                             else "payment_search_query_changed")
    candidates, branch, parent = [], label, label.parent()
    for _ in range(32):
        if parent is None:
            raise ReviewRequired("payment_copy_grid_ancestry_unavailable")
        children = parent.children()
        if len(children) > 500:
            raise ReviewRequired("payment_copy_grid_tree_truncated")
        if (len(children) == 2 and branch in children
                and all(c.element_info.control_type == "Pane" for c in children)):
            sibling = children[1 - children.index(branch)]
            if (sibling.element_info.name == "" and sibling.element_info.class_name == "SWT_Window0"
                    and not sibling.children()):
                candidates.append((sibling, branch))
        if parent == pane:
            break
        branch, parent = parent, parent.parent()
    else:
        raise ReviewRequired("payment_copy_grid_ancestry_unavailable")
    grid, filter_bar = exactly_one(candidates, "grid", "payment_copy")
    for control in (pane, grid, filter_bar):
        if (control.element_info.process_id != pid or type(control.handle) is not int or control.handle <= 0
                or control.element_info.class_name != "SWT_Window0" or not control.is_visible()
                or not enabled_within_scope(control, root)):
            raise ReviewRequired("payment_copy_grid_scope_mismatch")
    root_rect, pane_rect, grid_rect = map(_rectangle, (root, pane, grid))
    bar_rect = _rectangle(filter_bar)
    for outer, inner in ((root_rect, pane_rect), (pane_rect, grid_rect), (pane_rect, bar_rect)):
        if not (outer[0] <= inner[0] < inner[2] <= outer[2]
                and outer[1] <= inner[1] < inner[3] <= outer[3]):
            raise ReviewRequired("payment_copy_grid_outside_scope")
    if grid_rect[1] < bar_rect[3]:
        raise ReviewRequired("payment_copy_grid_structure_changed")
    return grid, (root.handle, pane.handle, grid.handle, root_rect, pane_rect, grid_rect, bar_rect, query)


def copy_payment_table_scope(root, method, driver, send_keys, root_guard, *,
                             verify_columns=None, diagnostics=None, timeout=5, clock=time.monotonic, sleep=time.sleep):
    """Two intentional snapshots; no retry of an uncertain keyboard action."""
    if not isinstance(method, str) or method not in PAYMENT_CODES:
        raise ReviewRequired("payment_copy_unsupported_method")
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 10):
        raise ValueError("invalid_copy_timeout")
    pid = root.element_info.process_id
    progress = diagnostics.setdefault("progress", {}) if diagnostics is not None else {}
    progress.update(phase="initial_scope", shortcuts_attempted=0)

    def state():
        root_guard()
        grid, metadata = _binding(root)
        driver.assert_focus(root.handle, grid.handle, pid)
        return metadata

    initial = state()

    def unchanged():
        if state() != initial:
            raise ReviewRequired("payment_copy_scope_changed")

    if verify_columns is None:
        raise ReviewRequired("payment_copy_column_verifier_required")

    def columns(phase):
        progress["phase"] = phase
        unchanged()
        try:
            result = verify_columns(_binding(root)[0])
        except ReviewRequired:
            unchanged()  # Discard recognition as the cause if scope changed.
            raise
        unchanged()
        if tuple(c["label"] for c in result) != PAYMENT_COLUMNS:
            raise ReviewRequired("payment_copy_column_order_mismatch")
        return result

    initial_columns = columns("initial_headers")

    copies = []
    for snapshot in range(2):
        unchanged()
        baseline = driver.sequence()
        for keys in ("^a", "^c"):
            unchanged()
            progress["phase"] = ("select_all_" if keys == "^a" else "copy_") + str(snapshot + 1)
            progress["shortcuts_attempted"] += 1
            try:
                send_keys(keys)
            except Exception:
                raise ReviewRequired("payment_copy_key_action_uncertain") from None
            unchanged()
        deadline = clock() + timeout
        progress["phase"] = "read_copy_" + str(snapshot + 1)
        while clock() < deadline:
            unchanged()
            try:
                copied = driver.read_new(pid, root.handle, baseline)
            except ReviewRequired as exc:
                if exc.code not in PENDING_COPY:
                    raise
                sleep(min(0.1, max(0, deadline - clock())))
                continue
            unchanged()
            if copied.owner_pid != pid or copied.sequence == baseline or not copied.owner_handle:
                raise ReviewRequired("payment_copy_provenance_mismatch")
            parse_payment_table(copied.text)  # Bad first snapshot stops before another Copy.
            copies.append(copied)
            break
        else:
            raise ReviewRequired("payment_copy_fresh_clipboard_timeout")
    if copies[0].text != copies[1].text:
        raise ReviewRequired("payment_copy_rows_changed")
    unchanged()
    final_columns = columns("final_headers")
    if [c["label"] for c in initial_columns] != [c["label"] for c in final_columns]:
        raise ReviewRequired("payment_copy_columns_changed")
    report = inspect_copied_payment_rows(copies[-1].text, method)
    progress["phase"] = "complete"
    return {**report, "status": "PAYMENT_TABLE_COPIED_NOT_SAVED",
            "clipboard_provenance_verified": True, "stable_copy_count": 2,
            "copy_sequences": [c.sequence for c in copies],
            "clipboard_owner_handles": [c.owner_handle for c in copies],
            "pid": pid, "window_handle": initial[0], "pane_handle": initial[1], "grid_handle": initial[2],
            "query": initial[-1], "column_order_verified": True, "column_headers": final_columns,
            "source_order_tab_present": True, "source_debtor_tab_present": True,
            "source_order_number_verified": False, "source_debtor_id_verified": False,
            "note": "Two fresh identical copies from the focused Fakturama payment table, with six headers checked by OCR before/after. Selection and clipboard changed; no field write or Save. Complete enumeration, saved ID and payment code remain unverified."}


def _column_image_evidence(control, executable):
    """Keep exact pixels/words even when OCR or recognition requires review."""
    rect = _rectangle(control)
    image = control.capture_as_image()
    if image is None or image.size != (rect[2] - rect[0], rect[3] - rect[1]):
        raise ReviewRequired("payment_copy_header_capture_scale_mismatch")
    clean = Image.frombytes("RGB", image.size, image.convert("RGB").tobytes())
    png = io.BytesIO()
    clean.save(png, format="PNG")
    if png.tell() > MAX_IMAGE_BYTES:
        raise ReviewRequired("image_too_large")
    report = {"status": "UNVERIFIED_HEADER_IMAGE", "rect": rect, "image_size": list(clean.size),
              "expected_columns": list(PAYMENT_COLUMNS), "words": []}
    try:
        words, _ = ocr_words(png.getvalue(), executable)
        report["words"] = [asdict(word) for word in words]
        report["headers"] = observe_payment_columns(words)
        report["status"] = "OBSERVED_PAYMENT_HEADERS_ONLY"
    except ReviewRequired as exc:
        report.update(status="REVIEW_REQUIRED", code=exc.code)
    return png.getvalue(), report


def _verify_columns_image(grid, executable, diagnostics=None):
    png, report = _column_image_evidence(grid, executable)
    if diagnostics is not None:
        frames = diagnostics.setdefault("frames", [])
        frames.append({"name": "grid-" + str(len(frames) + 1), "png": png, "evidence": report})
    if report["status"] == "REVIEW_REQUIRED":
        raise ReviewRequired(report["code"])
    return report["headers"]


def payment_header_bundle(frames, report):
    """Private diagnostic ZIP, never input to a create/save decision."""
    bundle = io.BytesIO()
    evidence = {**report, "frames": []}
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, frame in enumerate(frames):
            name = "header-" + str(index + 1) + ".png"
            archive.writestr(name, frame["png"])
            evidence["frames"].append({"role": frame["name"], "image": name, **frame["evidence"]})
        archive.writestr("evidence.json", json.dumps(evidence, ensure_ascii=False, indent=2))
    return bundle.getvalue()


def _root_guard(root, pid, handle):
    title = root.element_info.name

    def guard():
        try:
            if (root.handle != handle or root.element_info.process_id != pid
                    or root.element_info.class_name != "SWT_Window0" or root.element_info.name != title
                    or not root.is_visible() or not root.is_enabled()):
                raise ReviewRequired("payment_copy_main_window_identity_changed")
            executable = process_executable_name(pid)
            if not isinstance(executable, str) or executable.casefold() != "fakturama.exe":
                raise ReviewRequired("payment_copy_executable_identity_mismatch")
            if not root.is_active():
                raise ReviewRequired("payment_copy_keep_fakturama_in_foreground")
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired("payment_copy_scope_unavailable") from None
    return guard


def capture_payment_headers_scope(root, executable, root_guard):
    """Read-only capture of the exact reader region plus its surrounding pane.

    No clipboard driver/keyboard/focus action is created or called. Header
    failure is evidence, not a reason to discard the diagnostic frame.
    """
    root_guard()
    grid, initial = _binding(root)

    def unchanged():
        root_guard()
        if _binding(root)[1] != initial:
            raise ReviewRequired("payment_copy_scope_changed")

    frames = []
    for role in ("grid", "surrounding_panel"):
        unchanged()
        control = _binding(root)[0] if role == "grid" else unique_control(root, Locator("Pane", name=LIST_NAME))
        png, evidence = _column_image_evidence(control, executable)
        unchanged()
        frames.append({"name": role, "png": png, "evidence": evidence})
    return frames, {"status": "READ_ONLY_PAYMENT_HEADERS_WRITTEN", "pid": root.element_info.process_id,
                    "window_handle": initial[0], "pane_handle": initial[1], "grid_handle": initial[2],
                    "query": initial[-1], "scope_rechecked": True,
                    "all_rows_enumerated": False, "master_absence_verified": False,
                    "persistence_verified": False,
                    "note": "Exact grid and surrounding panel pixels/OCR only. No keyboard, clipboard, field write, focus change or Save. Recognition is heuristic and cannot authorize creation."}


def capture_payment_headers(pid, handle, executable):
    root = _payment_root(pid, handle)
    return capture_payment_headers_scope(root, executable, _root_guard(root, pid, handle))


def copy_payment_table(pid, handle, method, tesseract, *, diagnostics=None):
    if not isinstance(method, str) or method not in PAYMENT_CODES:
        raise ReviewRequired("payment_copy_unsupported_method")
    root = _payment_root(pid, handle)
    driver = WindowsClipboard()
    from pywinauto.keyboard import send_keys
    guard = _root_guard(root, pid, handle)

    def fixed_keys(keys):
        send_keys(keys, pause=0.05, vk_packet=False, turn_off_numlock=False)

    return copy_payment_table_scope(root, method, driver, fixed_keys, guard,
                                   diagnostics=diagnostics,
                                   verify_columns=lambda grid: _verify_columns_image(grid, tesseract, diagnostics))
