"""Read-only Win32 selector inspection; no clicks, focus, typing or saves."""

import platform

from .errors import ReviewRequired

SELECTOR_TITLES = {"Select the address", "Select a product"}


def inspect_native_selector(pid: int, title: str) -> dict:
    if type(pid) is not int or pid <= 0 or title not in SELECTOR_TITLES:
        raise ReviewRequired("invalid_native_selector_scope")
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_native_inspection")
    try:
        from pywinauto import Desktop
    except ImportError:
        raise ReviewRequired("windows_dependency_missing") from None
    windows = Desktop(backend="win32").windows(process=pid, title=title, visible_only=True)
    if len(windows) != 1:
        raise ReviewRequired("native_selector_missing_or_ambiguous")
    root = windows[0]
    if root.class_name() != "#32770":
        raise ReviewRequired("native_selector_class_mismatch")
    report = inspect_native_controls(root, pid, prefix="native_selector")
    return {"type": "read-only-native-selector-diagnostics", "pid": pid,
            "title": title, "backend": "win32", **report,
            "note": "Metadata only. Item counts do not prove completed search or verified master absence."}


def inspect_native_controls(root, pid: int, *, prefix="native_scope") -> dict:
    """Bounded metadata traversal of an already identified native scope.

    The observed Win32 backend can return descendants as children, causing
    repeated HWNDs at many discovery paths. Read each HWND once; a discovery
    path is not a guaranteed immediate-parent hierarchy.
    """
    if type(pid) is not int or pid <= 0:
        raise ReviewRequired(prefix + "_invalid_pid")
    nodes = []
    visited = set()
    duplicate_entries_skipped = 0
    truncated = False

    def visit(control, path):
        nonlocal truncated, duplicate_entries_skipped
        if control.element_info.process_id != pid:
            raise ReviewRequired(prefix + "_process_mismatch")
        handle = control.handle
        if type(handle) is not int or handle <= 0:
            raise ReviewRequired(prefix + "_invalid_handle")
        if handle in visited:
            duplicate_entries_skipped += 1
            return
        if len(nodes) >= 5000 or len(path) > 30:
            truncated = True
            return
        visited.add(handle)
        name = control.window_text()
        if not isinstance(name, str):
            raise ReviewRequired(prefix + "_text_unavailable")
        row = {"path": list(path), "handle": handle,
               "class_name": control.class_name(), "name": name[:4096],
               "text_truncated": len(name) > 4096,
               "visible": control.is_visible(), "enabled": control.is_enabled()}
        if row["class_name"] == "SysListView32":
            try:
                row["native_item_count"] = control.item_count()
            except Exception:
                row["native_item_count"] = None
        nodes.append(row)
        children = control.children()
        if len(children) > 500:
            truncated = True
        for index, child in enumerate(children[:500]):
            if len(nodes) >= 5000:
                truncated = True
                break
            visit(child, path + (index,))

    visit(root, ())
    return {"nodes": nodes, "truncated": truncated,
            "duplicate_entries_skipped": duplicate_entries_skipped}
