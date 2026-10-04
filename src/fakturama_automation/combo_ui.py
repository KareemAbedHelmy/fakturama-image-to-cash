"""Read the options of a standard Windows ComboBox and select one exact option, through UIA."""

import platform
import unicodedata

from .errors import ReviewRequired
from .uia import enabled_within_scope


def _type_name(value):
    name = type(value).__name__
    return name[:100] if name.isascii() and name.isidentifier() else "unknown"


class ComboReadRequired(ReviewRequired):
    """A failed property read with bounded, non-sensitive diagnostic evidence."""

    def __init__(self, code, diagnostic):
        super().__init__(code)
        self.diagnostic = diagnostic

    def report(self):
        return {"status": "REVIEW_REQUIRED", "code": self.code,
                "combo_diagnostic": self.diagnostic,
                "note": "Read-only combo diagnostic; no dropdown opened or selected. Options remain unverified."}


def _text(value):
    if (not isinstance(value, str) or len(value) > 1000
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise ReviewRequired("combo_option_text_invalid")
    return value


def _native_wrapper(handle):
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_combo_options")
    try:
        from pywinauto import Desktop
    except ImportError:
        raise ReviewRequired("windows_dependency_missing") from None
    return Desktop(backend="win32").window(handle=handle).wrapper_object()


def read_combo_options(scope, control, native_factory=None) -> dict:
    """Cross-check two complete option snapshots and current UIA/native values."""
    if (control.element_info.control_type != "ComboBox"
            or control.element_info.class_name != "ComboBox" or not control.handle
            or not control.is_visible() or not enabled_within_scope(control, scope)
            or control.element_info.process_id != scope.element_info.process_id):
        raise ReviewRequired("combo_scope_or_class_mismatch")
    factory = native_factory or _native_wrapper
    try:
        native = factory(control.handle)
    except ReviewRequired:
        raise
    except Exception:
        raise ReviewRequired("combo_native_binding_unavailable") from None

    methods = {}
    for name in ("item_count", "item_texts", "selected_index", "window_text"):
        try:
            methods[name] = callable(getattr(native, name, None))
        except Exception:
            methods[name] = False

    def snapshot(number):
        if (native.handle != control.handle or native.element_info.process_id != scope.element_info.process_id
                or native.class_name() != "ComboBox" or not native.is_visible()
                or not native.is_enabled() or not enabled_within_scope(control, scope)):
            raise ReviewRequired("combo_scope_or_class_mismatch")
        diagnostic = {"native_wrapper_type": _type_name(native),
                      "callable_native_methods": methods,
                      "snapshot": number, "completed_reads": [], "return_types": {}}
        stage = "item_count"

        def read(wrapper, method):
            value = getattr(wrapper, method)()
            diagnostic["completed_reads"].append(method)
            diagnostic["return_types"][method] = _type_name(value)
            return value

        try:
            count = read(native, stage)
            if type(count) is not int or not 0 <= count <= 1000:
                raise ReviewRequired("combo_option_count_out_of_range")
            diagnostic["option_count"] = count
            stage = "item_texts"
            options = read(native, stage)
            if not isinstance(options, list) or len(options) != count:
                raise ReviewRequired("combo_option_count_changed")
            options = [_text(value) for value in options]
            stage = "selected_index"
            index = read(native, stage)
            if type(index) is not int or not -1 <= index < count or (count == 0 and index != -1):
                raise ReviewRequired("combo_selected_index_invalid")
            diagnostic["selected_index"] = index
            stage = "uia_value_pattern"
            value = control.iface_value.CurrentValue
            diagnostic["completed_reads"].append(stage)
            diagnostic["return_types"][stage] = _type_name(value)
            current = _text(value)
            stage = "window_text"
            native_current = _text(read(native, stage))
            if current != native_current or (index >= 0 and current != options[index]):
                raise ReviewRequired("combo_selected_value_mismatch")
            return {"options": options, "selected_index": index, "current_value": current}
        except ReviewRequired as exc:
            diagnostic["failed_stage"] = stage
            raise ComboReadRequired(exc.code, diagnostic) from None
        except Exception as exc:
            diagnostic["failed_stage"] = stage
            diagnostic["exception_type"] = _type_name(exc)
            raise ComboReadRequired("combo_" + stage + "_unavailable", diagnostic) from None

    before, after = snapshot(1), snapshot(2)
    if before != after:
        raise ReviewRequired("combo_options_changed_during_read")
    return {"status": "OBSERVED_NATIVE_COMBO_OPTIONS_ONLY", **after,
            "note": "Current dropdown entries only; term definitions and completed master lookup are unverified."}


def select_combo_exact(scope, control, text, native_factory=None, guard=None):
    """Partial native selection primitive; live selection is not yet validated.

    Only a unique literal observed label is accepted. The caller verifies the
    application editor before/after; this helper never saves or retries a write.
    """
    text = _text(text)
    if not text:
        raise ReviewRequired("combo_selection_text_required")
    factory = native_factory or _native_wrapper
    try:
        native = factory(control.handle)
    except ReviewRequired:
        raise
    except Exception:
        raise ReviewRequired("combo_native_binding_unavailable") from None
    bound_factory = lambda handle: native
    before = read_combo_options(scope, control, bound_factory)
    matches = [i for i, value in enumerate(before["options"]) if value == text]
    if len(matches) != 1:
        raise ReviewRequired("combo_selection_missing_or_ambiguous")
    index = matches[0]
    if guard:
        guard()
    if read_combo_options(scope, control, bound_factory) != before:
        raise ReviewRequired("combo_options_changed_before_selection")
    if before["selected_index"] == index and before["current_value"] == text:
        return before
    try:
        select = native.select
        if not callable(select):
            raise AttributeError()
    except Exception:
        raise ReviewRequired("combo_selection_pattern_unavailable") from None
    try:
        select(index)
    except Exception:
        raise ReviewRequired("combo_selection_uncertain") from None
    if guard:
        guard()
    after = read_combo_options(scope, control, bound_factory)
    if (after["options"] != before["options"] or after["selected_index"] != index
            or after["current_value"] != text):
        raise ReviewRequired("combo_selection_readback_mismatch")
    return after
