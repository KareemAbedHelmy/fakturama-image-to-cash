"""Address roles of a New Debtor: the role popup, the separate delivery address, and read-back of both."""

import time

from .combo_ui import read_combo_options, select_combo_exact
from .debtor_live_ui import _source_order_tab, _tab_runtime_id
from .debtor_ui import (DebtorEditorSession, DebtorMainAddressSession, _pair,
                        _selected, _tab_items, _value)
from .errors import ReviewRequired
from .finance import validate_order
from .payment_ui import _payment_root
from .selector_ui import exactly_one
from .uia import Locator, enabled_within_scope, process_executable_name, unique_control

LABELS = ('Invoice address', 'Delivery address')


def _popup(root):
    popup = exactly_one([c for c in root.children()
        if c.element_info.class_name == '#32770' and c.is_visible()],
        'popup', 'billing_role')
    if (popup.element_info.control_type != 'Pane'
            or popup.element_info.process_id != root.element_info.process_id
            or not enabled_within_scope(popup, root)):
        raise ReviewRequired('billing_role_popup_identity_mismatch')
    boxes, stack, count = [], [(popup, 0)], 0
    while stack:
        control, depth = stack.pop()
        count += 1
        if count > 100 or depth > 8:
            raise ReviewRequired('billing_role_popup_tree_truncated')
        if control.element_info.control_type == 'CheckBox':
            boxes.append(control)
        stack.extend((c, depth + 1) for c in control.children())
    if len(boxes) != 2 or sorted(c.element_info.name for c in boxes) != sorted(LABELS):
        raise ReviewRequired('billing_role_checkbox_set_mismatch')
    result = {c.element_info.name: c for c in boxes}
    if boxes[0].parent() != boxes[1].parent():
        raise ReviewRequired('billing_role_checkbox_structure_changed')
    for c in boxes:
        if (c.element_info.process_id != root.element_info.process_id
                or c.element_info.class_name != 'Button' or not c.handle
                or not c.is_visible() or not enabled_within_scope(c, root)):
            raise ReviewRequired('billing_role_checkbox_identity_unavailable')
    return result


def _states(boxes):
    try:
        states = {name: c.iface_toggle.CurrentToggleState for name, c in boxes.items()}
        if any(type(s) is not int or s not in (0, 1) for s in states.values()):
            raise ReviewRequired('billing_role_checkbox_state_unverified')
        return states
    except ReviewRequired:
        raise
    except Exception:
        raise ReviewRequired('billing_role_toggle_pattern_unavailable') from None


def prepare_billing_role_scope(root, source, expected_id, root_guard, *, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics['progress'] = {'phase': 'preflight', 'toggles_attempted': []}
    validate_order(source)
    root_guard()
    session = DebtorMainAddressSession(root)
    if not expected_id or session.generated_id != expected_id:
        raise ReviewRequired('billing_role_generated_id_mismatch')
    order_identity = _tab_runtime_id(_source_order_tab(root), diagnostics)
    debtor, billing = source.debtor, source.debtor.billing
    if billing.recipient_name and billing.recipient_name not in {
            debtor.company, ' '.join(p for p in (debtor.first_name, debtor.last_name) if p)}:
        raise ReviewRequired('billing_role_recipient_unmapped')
    expected = {'company': debtor.company, 'first_name': debtor.first_name,
        'last_name': debtor.last_name, 'street': billing.street, 'zip': billing.zip,
        'city': billing.city, 'country': billing.country,
        'additional_name': billing.additional_name or '',
        'specification': billing.specification or '', 'district': billing.district or '',
        'email': billing.email or '', 'telephone': billing.telephone or ''}
    fields = session.fields()
    if any(not c.handle for c in fields.values()):
        raise ReviewRequired('billing_role_field_identity_unavailable')
    field_ids = {r: c.handle for r, c in fields.items()}
    same_address = billing == debtor.delivery
    target = {'Invoice address': 1, 'Delivery address': int(same_address)}
    diagnostics['expected_states'] = target
    boxes = _popup(root)
    pinned = {r: c.handle for r, c in boxes.items()}
    committed = _states(boxes)
    diagnostics['before'] = dict(committed)

    def guard():
        root_guard()
        current = session.fields()
        if {r: c.handle for r, c in current.items()} != field_ids:
            raise ReviewRequired('billing_role_field_identity_changed')
        values = {r: _value(c) for r, c in current.items()}
        if values != expected:
            diagnostics['mismatched_main_fields'] = [r for r in expected if values.get(r) != expected[r]]
            raise ReviewRequired('billing_role_main_fields_mismatch')
        if _tab_runtime_id(_source_order_tab(root), diagnostics) != order_identity:
            raise ReviewRequired('billing_role_source_order_tab_changed')
        current_boxes = _popup(root)
        if {r: c.handle for r, c in current_boxes.items()} != pinned:
            raise ReviewRequired('billing_role_checkbox_identity_changed')
        states = _states(current_boxes)
        diagnostics['last_states'] = states
        if states != committed:
            raise ReviewRequired('billing_role_checkbox_readback_mismatch')
        return current_boxes, states

    guard()
    # Remove an incorrect Delivery role before adding Invoice; no role is
    # toggled when its current state already equals the requested state.
    for name in reversed(LABELS):
        current, states = guard()
        if states[name] == target[name]:
            continue
        progress['phase'] = 'toggle_' + name
        progress['toggles_attempted'].append(name)
        try:
            current[name].iface_toggle.Toggle()
        except Exception:
            raise ReviewRequired('billing_role_toggle_uncertain') from None
        committed[name] = target[name]
        guard()  # One attempt only, including if Toggle changed state then failed.
    first = guard()[1]
    second = guard()[1]
    progress['phase'] = 'complete'
    return {'status': 'LIVE_BILLING_ROLE_SELECTED_UNSAVED',
        'generated_id': session.generated_id, 'generated_id_preserved': True,
        'main_address_values_verified': True, 'checkbox_states': second,
        'final_readback': {'first_states': first, 'second_states': second},
        'billing_role_checkbox_verified': True, 'billing_equals_delivery': same_address,
        'source_order_tab_present': True, 'source_order_number_verified': False,
        'delivery_address_verified': False, 'persistence_verified': False,
        'field_model_commit_verified': False,
        'navigation': 'OPERATOR_OPENED_MAIN_ADDRESS_TYPE_POPUP',
        'note': 'Live checkbox states read back twice. Popup remains open; dismissal, saved address roles, separate delivery address and Order linking remain unverified. No Save invoked.'}


def prepare_billing_role(pid, handle, source, expected_id, *, diagnostics=None):
    root = _payment_root(pid, handle)
    title = root.element_info.name

    def root_guard():
        if (root.handle != handle or root.element_info.process_id != pid
                or root.element_info.class_name != 'SWT_Window0'
                or root.element_info.name != title or not root.is_visible() or not root.is_enabled()
                or process_executable_name(pid).casefold() != 'fakturama.exe'):
            raise ReviewRequired('billing_role_main_window_identity_changed')
        if not root.is_active():
            raise ReviewRequired('billing_role_keep_fakturama_in_foreground')

    return prepare_billing_role_scope(root, source, expected_id, root_guard, diagnostics=diagnostics)


DELIVERY_TAB = 'additional address #1'


class DeliveryAddressSession(DebtorEditorSession):
    """The additional (delivery) address of a New Debtor."""

    def fields(self):
        pane = self._pane()
        _selected(pane, 'Addresses')
        addresses = exactly_one([c for c in pane.descendants(control_type='Tab')
            if c.element_info.name == 'Addresses'], 'addresses_tab', 'delivery_prepare')
        page = exactly_one([c for c in addresses.descendants(control_type='Tab')
            if c.element_info.name == DELIVERY_TAB], 'additional_address', 'delivery_prepare')
        tabs = _tab_items(page)
        if sorted(c.element_info.name for c in tabs) != ['Main address', DELIVERY_TAB]:
            raise ReviewRequired('delivery_prepare_address_tab_set_changed')
        for tab in tabs:
            if (not enabled_within_scope(tab, pane)
                    or bool(tab.iface_selection_item.CurrentIsSelected)
                    != (tab.element_info.name == DELIVERY_TAB)):
                raise ReviewRequired('delivery_prepare_additional_address_not_selected')
        first, last = _pair(pane, 'First Name Last Name')
        zip_code, city = _pair(page, 'ZIP - City')
        fields = {'company': unique_control(pane, Locator('Edit', name='Company')),
                  'first_name': first, 'last_name': last, 'zip': zip_code, 'city': city,
                  'country': unique_control(page, Locator('ComboBox', name='Country'))}
        for role, label in [('additional_name', 'additional name'), ('street', 'Street'),
                            ('specification', 'Address specification'), ('district', 'district'),
                            ('email', 'E-Mail'), ('telephone', 'Telephone'),
                            ('local_consultant', 'local consultant'), ('second_phone', '2nd phone'),
                            ('fax', 'Telefax'), ('mobile', 'Mobile')]:
            fields[role] = unique_control(page, Locator('Edit', name=label))
        label = unique_control(page, Locator('Text', name='address type'))
        siblings = label.parent().children()
        index = siblings.index(label)
        if index + 1 >= len(siblings) or siblings[index + 1].element_info.control_type != 'Pane':
            raise ReviewRequired('delivery_prepare_address_type_structure_changed')
        container = siblings[index + 1]
        fields['role_text'] = unique_control(container, Locator('Edit', name=''))
        fields['role_button'] = unique_control(container, Locator('Button', name=''))
        if any(not c.handle or c.element_info.process_id != self.root.element_info.process_id
               for c in fields.values()):
            raise ReviewRequired('delivery_prepare_field_identity_unavailable')
        return fields


def prepare_delivery_scope(root, source, expected_id, root_guard, *, native_factory=None,
                           diagnostics=None, manual_role_popup=False):
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics['progress'] = {'phase': 'preflight', 'field_writes_attempted': [],
        'combo_selections_attempted': [], 'popup_opens_attempted': 0, 'toggles_attempted': [],
        'popup_open_method': 'OPERATOR' if manual_role_popup else 'UIA_INVOKE'}
    validate_order(source)
    root_guard()
    session = DeliveryAddressSession(root)
    if not expected_id or session.generated_id != expected_id:
        raise ReviewRequired('delivery_prepare_generated_id_mismatch')
    order_identity = _tab_runtime_id(_source_order_tab(root), diagnostics)
    debtor, address = source.debtor, source.debtor.delivery
    if address == debtor.billing:
        raise ReviewRequired('delivery_prepare_separate_address_not_required')
    recipient = address.recipient_name or ''
    distinct_recipient = recipient not in {'', debtor.company, ' '.join(
        p for p in (debtor.first_name, debtor.last_name) if p)}
    if distinct_recipient and address.additional_name and address.additional_name != recipient:
        raise ReviewRequired('delivery_prepare_recipient_and_additional_name_conflict')
    name_addon = recipient if distinct_recipient else (address.additional_name or '')
    target = {'company': debtor.company, 'first_name': debtor.first_name,
        'last_name': debtor.last_name, 'additional_name': name_addon,
        'street': address.street, 'zip': address.zip, 'city': address.city,
        'country': address.country, 'specification': address.specification or '',
        'district': address.district or '', 'email': address.email or '',
        'telephone': address.telephone or '', 'local_consultant': '', 'second_phone': '',
        'fax': '', 'mobile': ''}
    fields = session.fields()
    pinned = {r: (c.handle, c.element_info.control_type, c.element_info.class_name)
              for r, c in fields.items()}
    before = {r: _value(fields[r]) for r in target}
    initial_role_text = _value(fields['role_text'])
    if initial_role_text not in ('', 'Delivery address'):
        raise ReviewRequired('delivery_prepare_existing_address_role_conflict')
    if manual_role_popup:
        if before != target:
            diagnostics['mismatched_fields'] = [r for r in target if before[r] != target[r]]
            raise ReviewRequired('delivery_prepare_manual_popup_requires_prepared_fields')
        _states(_popup(root))
    elif any(c.element_info.class_name == '#32770' and c.is_visible() for c in root.children()):
        raise ReviewRequired('delivery_prepare_close_popup_before_start')
    protected = {'company', 'first_name', 'last_name', 'local_consultant', 'second_phone', 'fax', 'mobile'}
    edits = [r for r in target if r not in protected and r != 'country']
    for role, value in before.items():
        if ((role in protected and value != target[role])
                or (role not in protected and role != 'country' and value not in ('', target[role]))):
            diagnostics['failed_role'] = role
            raise ReviewRequired('delivery_prepare_existing_value_conflict')
    for role in edits:
        if fields[role].iface_value.CurrentIsReadOnly:
            raise ReviewRequired('delivery_prepare_field_read_only')
    # Check all required patterns and country choices before changing any field.
    if not manual_role_popup and not callable(getattr(fields['role_button'], 'click_input', None)):
        raise ReviewRequired('delivery_prepare_popup_click_unavailable')
    options = read_combo_options(session._pane(), fields['country'], native_factory)
    diagnostics['country_options'] = options
    if options['options'].count(target['country']) != 1:
        raise ReviewRequired('delivery_prepare_country_missing_or_ambiguous')
    diagnostics['before_values'], diagnostics['expected_values'] = before, target
    committed, pending = dict(before), set()
    role_phase = False

    def guard():
        root_guard()
        current = session.fields()
        if {r: (c.handle, c.element_info.control_type, c.element_info.class_name)
                for r, c in current.items()} != pinned:
            raise ReviewRequired('delivery_prepare_field_identity_changed')
        if _tab_runtime_id(_source_order_tab(root), diagnostics) != order_identity:
            raise ReviewRequired('delivery_prepare_source_order_tab_changed')
        values = {r: _value(current[r]) for r in target}
        for role, value in values.items():
            allowed = {committed[role], target[role]} if role in pending else {committed[role]}
            if value not in allowed:
                diagnostics['failed_role'] = role
                raise ReviewRequired('delivery_prepare_value_changed')
        if not role_phase and _value(current['role_text']) != initial_role_text:
            raise ReviewRequired('delivery_prepare_address_role_changed')
        diagnostics['last_values'] = values
        return current, values

    guard()
    for role in edits:
        current, values = guard()
        if values[role] == target[role]:
            continue
        progress['phase'] = 'write_' + role
        progress['field_writes_attempted'].append(role)
        pending.add(role)
        try:
            if current[role].iface_value.CurrentIsReadOnly:
                raise ReviewRequired('delivery_prepare_field_read_only')
            current[role].iface_value.SetValue(target[role])
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired('delivery_prepare_write_uncertain') from None
        if guard()[1][role] != target[role]:
            raise ReviewRequired('delivery_prepare_write_readback_mismatch')
        committed[role] = target[role]
        pending.remove(role)
    if guard()[1]['country'] != target['country']:
        progress['phase'] = 'select_country'
        progress['combo_selections_attempted'].append('country')
        pending.add('country')
        select_combo_exact(session._pane(), guard()[0]['country'], target['country'],
                           native_factory, guard=guard)
        committed['country'] = target['country']
        pending.remove('country')
    current, values = guard()
    if values != target:
        raise ReviewRequired('delivery_prepare_final_fields_mismatch')
    role_phase = True
    progress['phase'] = 'observe_operator_popup' if manual_role_popup else 'open_address_type'
    if not manual_role_popup:
        progress['popup_opens_attempted'] += 1
        try:
            current['role_button'].click_input()
        except Exception:
            raise ReviewRequired('delivery_prepare_popup_open_uncertain') from None
    deadline = time.monotonic() + 3
    while not any(c.element_info.class_name == '#32770' and c.is_visible() for c in root.children()):
        guard()
        if time.monotonic() >= deadline:
            raise ReviewRequired('delivery_prepare_popup_not_observed')
        time.sleep(0.1)
    boxes = _popup(root)
    box_ids = {r: c.handle for r, c in boxes.items()}
    states = _states(boxes)
    desired_states = {'Invoice address': 0, 'Delivery address': 1}

    def checkbox_guard():
        guard()
        fresh = _popup(root)
        if {r: c.handle for r, c in fresh.items()} != box_ids:
            raise ReviewRequired('delivery_prepare_checkbox_identity_changed')
        observed = _states(fresh)
        diagnostics['last_checkbox_states'] = observed
        if observed != states:
            raise ReviewRequired('delivery_prepare_checkbox_readback_mismatch')
        return fresh, observed

    for name in LABELS:
        fresh, observed = checkbox_guard()
        if observed[name] == desired_states[name]:
            continue
        progress['phase'] = 'toggle_' + name
        progress['toggles_attempted'].append(name)
        try:
            fresh[name].iface_toggle.Toggle()
        except Exception:
            raise ReviewRequired('delivery_prepare_toggle_uncertain') from None
        states[name] = desired_states[name]
        checkbox_guard()
    first, first_states = guard()[1], checkbox_guard()[1]
    second, second_states = guard()[1], checkbox_guard()[1]
    if first != target or second != target:
        raise ReviewRequired('delivery_prepare_final_fields_mismatch')
    progress['phase'] = 'complete'
    return {'status': 'LIVE_DELIVERY_ADDRESS_PREPARED_UNSAVED',
        'generated_id': session.generated_id, 'generated_id_preserved': True,
        'address_tab': DELIVERY_TAB, 'values': second, 'checkbox_states': second_states,
        'final_readback': {'first_values': first, 'second_values': second,
                          'first_states': first_states, 'second_states': second_states},
        'delivery_fields_verified': True, 'delivery_role_checkbox_verified': True,
        'popup_open_method': progress['popup_open_method'],
        'recipient_mapping': {'source_recipient': recipient, 'additional_name': name_addon,
                              'document_recipient_verified': False},
        'source_order_tab_present': True, 'source_order_number_verified': False,
        'main_billing_address_rechecked': False, 'persistence_verified': False,
        'field_model_commit_verified': False, 'order_link_verified': False,
        'note': 'Additional address fields and Delivery-only checkbox states read back twice. Popup remains open. No Save or tab creation invoked. Main billing data, saved roles and final document recipient require verification.'}


def prepare_delivery(pid, handle, source, expected_id, *, diagnostics=None, manual_role_popup=False):
    root = _payment_root(pid, handle)
    title = root.element_info.name

    def root_guard():
        if (root.handle != handle or root.element_info.process_id != pid
                or root.element_info.class_name != 'SWT_Window0' or root.element_info.name != title
                or not root.is_visible() or not root.is_enabled()
                or process_executable_name(pid).casefold() != 'fakturama.exe'):
            raise ReviewRequired('delivery_prepare_main_window_identity_changed')
        if not root.is_active():
            raise ReviewRequired('delivery_prepare_keep_fakturama_in_foreground')

    return prepare_delivery_scope(root, source, expected_id, root_guard, diagnostics=diagnostics,
                                  manual_role_popup=manual_role_popup)
