"""Prepare a missing Debtor's fields and address roles, then Save it once (separate command)."""

import time

from .debtor_address_ui import (DELIVERY_TAB, DeliveryAddressSession, _popup, _states,
                                prepare_billing_role_scope, prepare_delivery_scope)
from .debtor_live_ui import _salutation, prepare_debtor_page_scope
from .debtor_ui import DebtorEditorSession, DebtorMainAddressSession, _value
from .errors import ReviewRequired
from .finance import validate_order
from .order_finalize_ui import _snapshot, _wait
from .order_ui import Tree
from .payment_ui import _payment_root
from .selector_ui import exactly_one
from .tab_navigation_ui import select_tab
from .uia import Locator, process_executable_name, unique_control


def _debtor_guard_factory(root, pid, handle):
    """Pin the Fakturama process/window across slow read-only UIA traversal."""
    title = root.element_info.name
    executable = (process_executable_name(pid) or '').casefold()
    if executable != 'fakturama.exe':
        raise ReviewRequired('debtor_flow_executable_identity_mismatch')

    def guard():
        try:
            if (root.handle != handle or root.element_info.process_id != pid
                    or root.element_info.name != title or not root.is_visible()
                    or not root.is_enabled()):
                raise ReviewRequired('debtor_flow_main_window_identity_changed')
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired('debtor_flow_scope_unavailable') from None

    return guard


def _role_controls(root, page):
    pane = unique_control(root, Locator('Pane', name='New Debtor'))
    # SWT reports tab containers as invisible, so match by exact name instead of visibility.
    addresses = exactly_one([c for c in pane.descendants(control_type='Tab')
                             if c.element_info.name == 'Addresses'], 'addresses_tab', 'debtor_flow')
    tab = exactly_one([c for c in addresses.descendants(control_type='Tab')
                       if c.element_info.name == page], 'address_tab', 'debtor_flow')
    label = unique_control(tab, Locator('Text', name='address type'))
    siblings = label.parent().children()
    index = siblings.index(label)
    if index + 1 >= len(siblings) or siblings[index + 1].element_info.control_type != 'Pane':
        raise ReviewRequired('debtor_flow_address_role_structure_changed')
    group = siblings[index + 1]
    return (unique_control(group, Locator('Edit', name='')),
            unique_control(group, Locator('Button', name='')))


def _open_role_popup(root, page, guard, progress):
    guard()
    if any(c.element_info.class_name == '#32770' and c.is_visible() for c in root.children()):
        raise ReviewRequired('debtor_flow_unexpected_open_popup')
    _, button = _role_controls(root, page)
    progress['phase'] = 'open_' + page.replace(' ', '_') + '_role'
    progress['role_popup_opens_attempted'] += 1
    try:
        # SWT's Invoke can block inside its popup event loop and return only
        # after the popup disappears. A physical click on this freshly bound
        # native Button returned promptly and left one #32770 child visible.
        button.click_input()
    except Exception:
        raise ReviewRequired('debtor_flow_role_popup_open_uncertain') from None
    deadline = time.monotonic() + 4
    while not any(c.element_info.class_name == '#32770' and c.is_visible() for c in root.children()):
        guard()
        if time.monotonic() >= deadline:
            raise ReviewRequired('debtor_flow_role_popup_not_observed')
        time.sleep(.1)


def _close_role_popup(root, page, expected_roles, guard, progress):
    from pywinauto.keyboard import send_keys
    import win32gui
    boxes = _popup(root)
    if _states(boxes) != expected_roles:
        raise ReviewRequired('debtor_flow_role_popup_state_changed')
    popup_window = exactly_one([c for c in root.children()
                                if c.element_info.class_name == '#32770' and c.is_visible()],
                               'popup', 'debtor_flow')
    if win32gui.GetForegroundWindow() != popup_window.handle:
        raise ReviewRequired('debtor_flow_popup_not_foreground_for_escape')
    progress['phase'] = 'close_' + page.replace(' ', '_') + '_role'
    progress['role_popup_closes_attempted'] += 1
    try:
        send_keys('{ESC}', pause=.05, vk_packet=False)
    except Exception:
        raise ReviewRequired('debtor_flow_popup_close_uncertain') from None
    deadline = time.monotonic() + 4
    while any(c.element_info.class_name == '#32770' and c.is_visible() for c in root.children()):
        guard()
        if time.monotonic() >= deadline:
            raise ReviewRequired('debtor_flow_popup_not_closed')
        time.sleep(.1)
    guard()
    role_text = _value(_role_controls(root, page)[0])
    actual = {name: name in role_text for name in expected_roles}
    if actual != {name: bool(value) for name, value in expected_roles.items()}:
        raise ReviewRequired('debtor_flow_role_text_not_committed')
    return role_text


def _add_delivery_tab(root, guard, progress):
    guard()
    pane = unique_control(root, Locator('Pane', name='New Debtor'))
    addresses = exactly_one([c for c in pane.descendants(control_type='Tab')
                             if c.element_info.name == 'Addresses'],
                            'addresses_tab', 'debtor_flow')
    main = exactly_one([c for c in addresses.descendants(control_type='Tab')
                        if c.element_info.name == 'Main address'],
                       'main_address_tab', 'debtor_flow')
    if any(t.element_info.name == DELIVERY_TAB for t in addresses.descendants(control_type='TabItem')):
        raise ReviewRequired('debtor_flow_additional_address_already_exists')
    button = unique_control(main, Locator('Button', name='+'))
    progress['phase'] = 'add_delivery_address'
    progress['additional_address_opens_attempted'] += 1
    try:
        button.iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('debtor_flow_additional_address_open_uncertain') from None
    deadline = time.monotonic() + 5
    while True:
        guard()
        tabs = [t for t in addresses.descendants(control_type='TabItem')
                if t.element_info.name == DELIVERY_TAB]
        if len(tabs) == 1 and tabs[0].iface_selection_item.CurrentIsSelected:
            return
        if time.monotonic() >= deadline:
            raise ReviewRequired('debtor_flow_additional_address_not_observed')
        time.sleep(.1)


def _read_prepared_main(root, source, expected_id, guard):
    """Resume only when two scoped reads match every Main field exactly."""
    debtor, address = source.debtor, source.debtor.billing
    expected = {'company': debtor.company, 'first_name': debtor.first_name,
                'last_name': debtor.last_name, 'street': address.street,
                'zip': address.zip, 'city': address.city, 'country': address.country,
                'additional_name': address.additional_name or '',
                'specification': address.specification or '', 'district': address.district or '',
                'email': address.email or '', 'telephone': address.telephone or '',
                'salutation': debtor.salutation or '---'}

    def observed():
        guard()
        session = DebtorMainAddressSession(root)
        if session.generated_id != expected_id:
            raise ReviewRequired('debtor_flow_generated_id_mismatch')
        controls = session.fields()
        controls['salutation'] = _salutation(session._pane())
        return {role: _value(control) for role, control in controls.items()}

    first, second = observed(), observed()
    if first != second:
        raise ReviewRequired('debtor_flow_main_values_changed_between_reads')
    if first != expected:
        return None
    return {'status': 'MAIN_FIELDS_ALREADY_MATCH_SOURCE', 'values': second,
            'generated_id': expected_id, 'double_readback_verified': True}


def prepare_new_debtor_scope(root, source, expected_id, guard, *, diagnostics=None):
    validate_order(source)
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics['progress'] = {'phase': 'preflight', 'role_popup_opens_attempted': 0,
        'role_popup_closes_attempted': 0, 'additional_address_opens_attempted': 0}
    guard()
    session = DebtorMainAddressSession(root)
    if session.generated_id != expected_id:
        raise ReviewRequired('debtor_flow_generated_id_mismatch')
    reports = {}
    reports['main'] = _read_prepared_main(root, source, expected_id, guard)
    if reports['main'] is None:
        reports['main'] = prepare_debtor_page_scope(root, source, expected_id, 'main', guard,
                                                    diagnostics={})
    _open_role_popup(root, 'Main address', guard, progress)
    reports['billing_role'] = prepare_billing_role_scope(root, source, expected_id, guard,
                                                         diagnostics={})
    same = source.debtor.billing == source.debtor.delivery
    billing_roles = {'Invoice address': 1, 'Delivery address': int(same)}
    reports['billing_role_text'] = _close_role_popup(root, 'Main address', billing_roles,
                                                     guard, progress)
    if not same:
        _add_delivery_tab(root, guard, progress)
        reports['delivery'] = prepare_delivery_scope(root, source, expected_id, guard,
                                                     diagnostics={})
        reports['delivery_role_text'] = _close_role_popup(
            root, DELIVERY_TAB, {'Invoice address': 0, 'Delivery address': 1}, guard, progress)
        if DeliveryAddressSession(root).generated_id != expected_id:
            raise ReviewRequired('debtor_flow_generated_id_changed')
    pane = unique_control(root, Locator('Pane', name='New Debtor'))
    select_tab(pane, {'Miscellaneous'}, guard)
    reports['misc'] = prepare_debtor_page_scope(root, source, expected_id, 'misc', guard,
                                                diagnostics={})
    if reports['misc']['values']['payment'] != source.payment.method:
        raise ReviewRequired('debtor_flow_payment_not_selected')
    progress['phase'] = 'complete'
    return {'status': 'NEW_DEBTOR_PREPARED_UNSAVED', 'generated_id': expected_id,
            'main_values_verified': True, 'billing_role_verified': True,
            'delivery_address_verified': same or 'delivery' in reports,
            'misc_values_verified': True, 'payment_selected': source.payment.method,
            'reports': reports, 'save_invoked': False}


def prepare_new_debtor(pid, handle, source, expected_id, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return prepare_new_debtor_scope(root, source, expected_id, _debtor_guard_factory(root, pid, handle),
                                    diagnostics=diagnostics)


DEBTOR_TABS = {'New Debtor', '*New Debtor'}


def save_new_debtor_scope(root, source, expected_id, guard, *, diagnostics=None):
    """Save the prepared New Debtor exactly once; never retried, uncertainty is reported."""
    from .product_create_ui import _button, _tabs
    validate_order(source)
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'save_invoked': False})
    guard()
    if DebtorEditorSession(root).generated_id != expected_id:
        raise ReviewRequired('debtor_save_generated_id_mismatch')
    button, tabs = _button(root, 'Save the current contents')
    if sum(t in DEBTOR_TABS for t in tabs) != 1:
        raise ReviewRequired('debtor_save_editor_tab_missing_or_ambiguous')
    # Fakturama commits the Company field only when focus leaves it; saving straight after a UIA write
    # persisted the contact with an empty company. Tab would type a tab character into this field, so
    # focus is moved by clicking the read-only Customer ID field instead.
    pane = unique_control(root, Locator('Pane', name='New Debtor'))
    company = unique_control(pane, Locator('Edit', name='Company'))
    guard()
    company.click_input()
    unique_control(pane, Locator('Edit', name='Customer ID')).click_input()
    time.sleep(.4)
    guard()
    if company.iface_value.CurrentValue != source.debtor.company:
        raise ReviewRequired('debtor_save_company_not_in_editor')
    progress['phase'] = 'save'
    progress['save_invoked'] = True  # recorded before invoking; never retried
    try:
        button.iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('debtor_save_invoke_uncertain') from None
    after, ok = _wait(lambda: _tabs(Tree(_snapshot(root)[0])), lambda t: not (DEBTOR_TABS & set(t)))
    diagnostics['tabs_after'] = after
    if not ok:
        raise ReviewRequired('debtor_save_not_confirmed_check_dialogs')
    progress['phase'] = 'editor_transition_observed'
    return {'status': 'DEBTOR_SAVE_INVOKED', 'generated_id': expected_id, 'tabs': after,
            'save_invoked': True, 'persistence_verified': False,
            'note': 'Save invoked once and the New Debtor tab changed or closed. This does not prove '
                    'the contact fields persisted; re-open the saved record or verify the exact '
                    'Debtor row in the Order selector before continuing.'}


def save_new_debtor(pid, handle, source, expected_id, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return save_new_debtor_scope(root, source, expected_id, _debtor_guard_factory(root, pid, handle),
                                 diagnostics=diagnostics)
