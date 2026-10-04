"""Keyboard entry for Fakturama's segmented date field. Only date-derived digits and fixed Left/Right
navigation are sent: no Enter, Save, clipboard use or retry.
Widget reference: https://eclipse.dev/nebula/widgets/cdatetime/cdatetimeb2c1.html?page=operation
"""

import re
from datetime import date

from .combo_ui import _native_wrapper
from .errors import ReviewRequired

MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
ROLES = ('day', 'month', 'year')


def segmented_date(text):
    if not isinstance(text, str):
        raise ReviewRequired('order_date_text_unavailable')
    match = re.fullmatch(r'(\d{1,2}) ([A-Z][a-z]{2}|\d{1,2}) (\d{4})', text)
    if not match:
        raise ReviewRequired('order_date_segment_format_unsupported')
    try:
        month = int(match[2]) if match[2].isdigit() else MONTHS.index(match[2]) + 1
        value = date(int(match[3]), month, int(match[1]))
    except ValueError:
        raise ReviewRequired('order_date_segment_value_invalid') from None
    return value, {role: match.span(i + 1) for i, role in enumerate(ROLES)}


def _native_focus_checker():
    from .windows_clipboard import WindowsClipboard
    checker = WindowsClipboard()

    def assert_focus(root, control):
        try:
            checker.assert_focus(root.handle, control.handle, root.element_info.process_id,
                                 allow_caret_blink=True)
        except ReviewRequired as exc:
            raise ReviewRequired('order_date_' + exc.code.removeprefix('payment_copy_')) from None

    return assert_focus


class SegmentedDateDriver:
    def __init__(self, *, native_factory=None, assert_focus=None, send=None):
        self.native_factory = native_factory or _native_wrapper
        self.assert_focus = assert_focus or _native_focus_checker()
        if send is None:
            from pywinauto.keyboard import send_keys
            send = send_keys
        self.send = send

    def set_date(self, root, control, blur_control, target, guard, progress, diagnostics):
        if type(target) is not date:
            raise ReviewRequired('order_date_target_invalid')
        guard()
        if (control.element_info.control_type != 'Edit' or control.element_info.class_name != 'Edit'
                or blur_control.element_info.control_type != 'Edit'):
            raise ReviewRequired('order_date_control_class_mismatch')
        try:
            native = self.native_factory(control.handle)
            if any(not callable(getattr(native, method, None))
                   for method in ('window_text', 'selection_indices', 'class_name')):
                raise ReviewRequired('order_date_native_methods_unavailable')
            for field in (control, blur_control):
                if not callable(field.element_info.element.SetFocus):
                    raise ReviewRequired('order_date_focus_method_unavailable')
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired('order_date_native_binding_unavailable') from None
        progress.setdefault('date_focus_actions_attempted', [])
        progress.setdefault('date_keyboard_actions_attempted', [])
        diagnostics['date_entry'] = {'method': 'segmented_keyboard', 'component_readbacks': []}

        def focus(field, role):
            guard()
            progress['phase'] = 'date_focus_' + role
            progress['date_focus_actions_attempted'].append(role)
            try:
                # Direct COM call avoids the wrapper's implicit Win32 retry.
                field.element_info.element.SetFocus()
            except Exception:
                raise ReviewRequired('order_date_focus_action_uncertain') from None
            self.assert_focus(root, field)
            guard()

        def state(require_focus=True):
            guard()
            if require_focus:
                self.assert_focus(root, control)
            try:
                if (native.handle != control.handle
                        or native.element_info.process_id != root.element_info.process_id
                        or native.class_name() != 'Edit' or not native.is_visible() or not native.is_enabled()):
                    raise ReviewRequired('order_date_native_identity_changed')
                text = native.window_text()
                if text != control.iface_value.CurrentValue:
                    raise ReviewRequired('order_date_native_uia_value_mismatch')
                value, spans = segmented_date(text)
                indices = native.selection_indices()
                if (not isinstance(indices, (tuple, list)) or len(indices) != 2
                        or any(type(i) is not int for i in indices)
                        or not 0 <= indices[0] <= indices[1] <= len(text)):
                    raise ReviewRequired('order_date_selection_range_invalid')
                selected = next((r for r, span in spans.items() if tuple(indices) == span), None)
                diagnostics['date_entry']['last_native_state'] = {
                    'date_as_iso': value.isoformat(), 'selection_range': list(indices),
                    'selected_component': selected,
                    'component_ranges': {r: list(span) for r, span in spans.items()},
                }
                # Whole-text selection is the widget's documented focus state.
                if require_focus and selected is None and tuple(indices) != (0, len(text)):
                    raise ReviewRequired('order_date_segment_selection_unverified')
                return value, selected
            except ReviewRequired:
                raise
            except Exception:
                raise ReviewRequired('order_date_native_read_unavailable') from None

        def send(keys, role):
            # Both callers construct these from fixed navigation or date ints.
            if keys not in {'{LEFT}', '{RIGHT}'} and not re.fullmatch(r'[0-9]{2}|[0-9]{4}', keys):
                raise ReviewRequired('order_date_keyboard_input_invalid')
            guard()
            self.assert_focus(root, control)
            progress['phase'] = 'date_keyboard_' + role
            progress['date_keyboard_actions_attempted'].append({'role': role, 'keys': keys})
            try:
                self.send(keys, pause=0.06, vk_packet=False)
            except Exception:
                raise ReviewRequired('order_date_keyboard_action_uncertain') from None
            self.assert_focus(root, control)
            guard()

        state(require_focus=False)  # Cross-check native role before any action.
        focus(control, 'date')
        for role, number in (('day', 1), ('year', target.year),
                             ('month', target.month), ('day', target.day)):
            value, selected = state()
            if getattr(value, role) == number:
                continue
            for _ in range(6):
                if selected == role:
                    break
                key = '{LEFT}' if selected and ROLES.index(selected) > ROLES.index(role) else '{RIGHT}'
                send(key, 'navigate_' + role)
                value, selected = state()
            else:
                raise ReviewRequired('order_date_component_navigation_unverified')
            if selected != role:
                raise ReviewRequired('order_date_component_navigation_unverified')
            send(f'{number:04d}' if role == 'year' else f'{number:02d}', 'write_' + role)
            value, selected = state()
            diagnostics['date_entry']['component_readbacks'].append({'role': role, 'date_as_iso': value.isoformat()})
            if getattr(value, role) != number:
                raise ReviewRequired('order_date_component_readback_mismatch')
        if state()[0] != target:
            raise ReviewRequired('order_date_components_final_mismatch')
        focus(blur_control, 'reference')  # Leave the date widget without Enter.
        if state(require_focus=False)[0] != target:
            raise ReviewRequired('order_date_after_focus_change_mismatch')
        diagnostics['date_entry']['after_focus_date_as_iso'] = target.isoformat()
