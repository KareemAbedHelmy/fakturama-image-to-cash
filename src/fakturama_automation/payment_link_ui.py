"""Verify a reopened payment term, switch tabs and select it as the Debtor's payment method. Never saves."""

import time

from .combo_ui import select_combo_exact
from .debtor_ui import DebtorEditorSession, DebtorMiscSession, _tab_items
from .errors import ReviewRequired
from .payment_copy_ui import _root_guard
from .payment_ui import _payment_root
from .saved_payment_ui import SavedPaymentSession
from .selector_ui import exactly_one
from .uia import enabled_within_scope


def _tab(scope, captions):
    control = exactly_one([c for c in _tab_items(scope) if c.element_info.name in captions],
                          'tab', 'payment_link')
    if not enabled_within_scope(control, scope):
        raise ReviewRequired('payment_link_tab_disabled')
    return control


def _select_tab(scope, captions, guard, progress, role, *, manual=False, notify=None,
                diagnostics=None, clock=time.monotonic, sleep=time.sleep):
    guard()
    tab = _tab(scope, captions)
    try:
        pattern = tab.iface_selection_item
        selected = bool(pattern.CurrentIsSelected)
    except Exception:
        raise ReviewRequired('payment_link_selection_pattern_unavailable') from None
    if not selected:
        guard()
        if manual:
            if not callable(notify):
                raise ReviewRequired('payment_link_manual_notification_required')
            progress['phase'] = 'wait_for_operator_' + role
            notify({'status': 'WAITING_FOR_MANUAL_TAB_SELECTION',
                    'tab': 'New Debtor' if role == 'debtor' else 'Miscellaneous',
                    'timeout_seconds': 60})
        else:
            progress['phase'] = 'select_' + role
            # Check the callable before counting a possible mutation.
            try:
                action = pattern.Select
                if not callable(action):
                    raise AttributeError()
            except Exception:
                raise ReviewRequired('payment_link_tab_select_unavailable') from None
            progress['tab_selections_attempted'] += 1
            try:
                action()
            except Exception as exc:
                # Keep bounded types/numeric HRESULT only; messages may be private.
                name = type(exc).__name__
                diagnostic = {'role': role, 'exception_type': name[:100] if name.isascii() and name.isidentifier() else 'unknown'}
                try:
                    hresult = getattr(exc, 'hresult', None)
                    if type(hresult) is int and -(2**31) <= hresult < 2**32:
                        diagnostic['hresult'] = hresult
                except Exception:
                    pass
                if diagnostics is not None:
                    diagnostics['tab_action_diagnostic'] = diagnostic
                raise ReviewRequired('payment_link_tab_selection_uncertain') from None
    deadline = clock() + (60 if manual else 3)
    while True:
        guard()
        current = _tab(scope, captions)
        try:
            if current.iface_selection_item.CurrentIsSelected:
                return
        except Exception:
            raise ReviewRequired('payment_link_selection_readback_unavailable') from None
        if clock() >= deadline:
            code = 'payment_link_manual_tab_timeout' if manual else 'payment_link_tab_selection_readback_mismatch'
            raise ReviewRequired(code)
        sleep(min(0.1, max(0, deadline - clock())))


def link_debtor_payment_scope(root, method, expected_standard, expected_id, root_guard,
                              *, native_factory=None, diagnostics=None, manual_tabs=False, notify=None,
                              clock=time.monotonic, sleep=time.sleep):
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {})
    progress.update(phase='verify_reopened_payment', tab_selections_attempted=0,
                    payment_selections_attempted=0, tab_mode='manual' if manual_tabs else 'uia')
    if not isinstance(expected_id, str) or not expected_id.strip() or len(expected_id) > 1000:
        raise ReviewRequired('payment_link_invalid_expected_id')
    root_guard()
    definition = SavedPaymentSession(root, method, expected_standard, root_guard).verify()
    diagnostics['payment_definition'] = definition

    def retained():
        root_guard()
        _tab(root, {'New Order', '*New Order'})
        _tab(root, {'New Debtor', '*New Debtor'})
        _tab(root, {method})
        if any(c.element_info.name == '*' + method for c in _tab_items(root)):
            raise ReviewRequired('payment_link_payment_became_dirty')

    _select_tab(root, {'New Debtor', '*New Debtor'}, retained, progress, 'debtor',
                manual=manual_tabs, notify=notify, diagnostics=diagnostics, clock=clock, sleep=sleep)
    progress['phase'] = 'verify_debtor_id'
    editor = DebtorEditorSession(root)
    if editor.generated_id != expected_id:
        raise ReviewRequired('payment_link_generated_id_mismatch')

    def debtor_guard():
        retained()
        editor._pane()  # Pins the generated ID and native editor identity.

    _select_tab(editor._pane(), {'Miscellaneous'}, debtor_guard, progress, 'miscellaneous',
                manual=manual_tabs, notify=notify, diagnostics=diagnostics, clock=clock, sleep=sleep)
    session = DebtorMiscSession(root)
    if session.generated_id != expected_id or session.editor_handle != editor.editor_handle:
        raise ReviewRequired('payment_link_debtor_identity_changed')
    initial = session.payment_options(native_factory)
    diagnostics['observed_payment_options'] = initial['payment_options']
    diagnostics['before_values'] = initial['values']
    options = initial['payment_options']['options']
    if options.count(method) != 1:
        raise ReviewRequired('payment_link_method_missing_or_ambiguous')
    combo_handle = session.fields()['payment'].handle
    before = initial['values']

    def unchanged():
        debtor_guard()
        values = session.observe()['values']
        if any(values[role] != before[role] for role in ('alias', 'discount', 'price_preference')):
            raise ReviewRequired('payment_link_unrelated_debtor_values_changed')
        if values['payment'] not in {before['payment'], method}:
            raise ReviewRequired('payment_link_payment_conflict')
        if session.fields()['payment'].handle != combo_handle:
            raise ReviewRequired('payment_link_combo_identity_changed')

    unchanged()
    progress['phase'] = 'select_payment'
    # Attempt counter records possible mutation, even when native Select raises.
    progress['payment_selections_attempted'] = int(before['payment'] != method)
    select_combo_exact(session._pane(), session.fields()['payment'], method, native_factory,
                       guard=unchanged)
    unchanged()
    final = session.payment_options(native_factory)
    if final['values']['payment'] != method or final['payment_options']['options'] != options:
        raise ReviewRequired('payment_link_final_readback_mismatch')
    unchanged()
    progress['phase'] = 'complete'
    return {**final, 'status': 'DEBTOR_PAYMENT_SELECTED_UNSAVED',
            'payment_definition': definition, 'before_values': before,
            'generated_id_verified': True, 'payment_definition_verified': True,
            'tab_mode': 'manual' if manual_tabs else 'uia',
            'automatic_tab_navigation_verified': not manual_tabs,
            'debtor_persistence_verified': False, 'master_absence_verified': False,
            'all_rows_enumerated': False, 'save_reopen_lifecycle_verified': False,
            'note': 'Reopened payment fields checked before switching. Debtor ID and other Miscellaneous values preserved; Payment read back. No Save, ID/alias/price write or Standard change. Full Debtor configuration and automatic master lifecycle remain pending.'}


def link_debtor_payment(pid, handle, method, expected_standard, expected_id, *, diagnostics=None,
                        manual_tabs=False, notify=None):
    root = _payment_root(pid, handle)
    return link_debtor_payment_scope(root, method, expected_standard, expected_id,
                                    _root_guard(root, pid, handle), diagnostics=diagnostics,
                                    manual_tabs=manual_tabs, notify=notify)
