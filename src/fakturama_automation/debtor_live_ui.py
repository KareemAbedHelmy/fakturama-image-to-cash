"""Fill one page (Main or Miscellaneous) of an open New Debtor from the source order and read it back. Never saves."""

import re
from decimal import Decimal

from .combo_ui import read_combo_options, select_combo_exact
from .debtor_ui import DebtorMainAddressSession, DebtorMiscSession, _tab_items, _value
from .domain import OrderInput
from .errors import ReviewRequired
from .keyboard_text import type_into
from .finance import validate_order
from .payment_ui import _payment_root
from .selector_ui import exactly_one
from .uia import Locator, enabled_within_scope, process_executable_name, unique_control


def _salutation(pane):
    label = unique_control(pane, Locator('Text', name='Salutation'))
    siblings = label.parent().children()
    index = siblings.index(label)
    if index + 1 >= len(siblings) or siblings[index + 1].element_info.control_type != 'Pane':
        raise ReviewRequired('debtor_prepare_salutation_structure_changed')
    return exactly_one([c for c in siblings[index + 1].children()
                        if c.element_info.control_type == 'ComboBox' and c.is_visible()
                        and enabled_within_scope(c, pane)], 'salutation', 'debtor_prepare')


def _zero_discount(text):
    return bool(re.fullmatch(r'\d+(?:[.,]\d+)?%', text)) and Decimal(text[:-1].replace(',', '.')) == 0


def _source_order_tab(root):
    tab = exactly_one([c for c in _tab_items(root)
                      if c.element_info.name in {'New Order', '*New Order'}],
                     'source_order_tab', 'debtor_prepare')
    if not enabled_within_scope(tab, root):
        raise ReviewRequired('debtor_prepare_source_order_tab_disabled')
    return tab


def _tab_runtime_id(tab, diagnostics):
    """Pin a live UIA element, including virtual SWT tabs with no native HWND.

    GetRuntimeId is opaque and only compared within this command's lifetime.
    Never persist it as a locator or infer an Order number from it.
    """
    evidence = diagnostics.setdefault('source_order_tab', {})
    evidence['name'] = tab.element_info.name
    evidence['native_handle'] = tab.handle
    evidence['identity_method'] = 'LIVE_UIA_RUNTIME_ID'
    try:
        value = tab.element_info.element.GetRuntimeId()
    except Exception as exc:
        evidence['runtime_id_available'] = False
        evidence['failed_stage'] = 'GetRuntimeId'
        evidence['exception_type'] = type(exc).__name__[:100]
        raise ReviewRequired('debtor_prepare_source_order_runtime_id_unavailable') from None
    if (not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 64
            or any(type(n) is not int or not -(2 ** 31) <= n < 2 ** 31 for n in value)):
        evidence['runtime_id_available'] = False
        raise ReviewRequired('debtor_prepare_source_order_runtime_id_invalid')
    evidence['runtime_id_available'] = True
    return tuple(value)


def prepare_debtor_page_scope(root, source: OrderInput, expected_id, page, root_guard,
                              *, native_factory=None, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics['progress'] = {'phase': 'preflight', 'page': page,
        'field_writes_attempted': [], 'combo_selections_attempted': []}
    validate_order(source)
    if page not in {'main', 'misc'} or not isinstance(expected_id, str) or not expected_id.strip():
        raise ReviewRequired('debtor_prepare_invalid_scope')
    root_guard()
    session = (DebtorMainAddressSession if page == 'main' else DebtorMiscSession)(root)
    if session.generated_id != expected_id:
        raise ReviewRequired('debtor_prepare_generated_id_mismatch')
    order_tab_identity = _tab_runtime_id(_source_order_tab(root), diagnostics)

    def fields():
        result = session.fields()
        if page == 'main':
            result['salutation'] = _salutation(session._pane())
        if any(c.element_info.process_id != root.element_info.process_id or not c.handle
               for c in result.values()):
            raise ReviewRequired('debtor_prepare_field_identity_unavailable')
        return result

    initial_fields = fields()
    before = {r: _value(c) for r, c in initial_fields.items()}
    diagnostics['before'] = {'generated_id': session.generated_id, 'values': dict(before)}
    pinned = {r: (c.handle, c.element_info.control_type, c.element_info.class_name)
              for r, c in initial_fields.items()}
    debtor = source.debtor
    if page == 'main':
        recipient = debtor.billing.recipient_name
        if recipient and recipient not in {debtor.company, ' '.join(
                p for p in (debtor.first_name, debtor.last_name) if p)}:
            raise ReviewRequired('debtor_prepare_billing_recipient_unmapped')
        target = {'company': debtor.company, 'first_name': debtor.first_name,
            'last_name': debtor.last_name, 'street': debtor.billing.street,
            'zip': debtor.billing.zip, 'city': debtor.billing.city,
            'additional_name': debtor.billing.additional_name or '',
            'specification': debtor.billing.specification or '',
            'district': debtor.billing.district or '', 'email': debtor.billing.email or '',
            'telephone': debtor.billing.telephone or '', 'country': debtor.billing.country,
            'salutation': debtor.salutation or '---'}
        combos = ('country', 'salutation')
    else:
        if not _zero_discount(before['discount']):
            raise ReviewRequired('debtor_prepare_nonzero_or_invalid_discount')
        target = {'alias': debtor.alias or '', 'discount': before['discount'],
                  'price_preference': 'Net', 'payment': source.payment.method}
        combos = ('price_preference', 'payment')
    diagnostics['expected_values'] = dict(target)
    edits = [r for r in target if r not in combos and r != 'discount']
    for role in edits:
        if before[role] not in {'', target[role]}:
            raise ReviewRequired('debtor_prepare_existing_value_conflict')
        try:
            if initial_fields[role].iface_value.CurrentIsReadOnly:
                raise ReviewRequired('debtor_prepare_field_read_only')
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired('debtor_prepare_value_pattern_unavailable') from None
    options = diagnostics['combo_options'] = {}
    # All required choices must exist uniquely before any contact field changes.
    for role in combos:
        root_guard()
        observed = read_combo_options(session._pane(), fields()[role], native_factory)
        options[role] = observed
        if observed['options'].count(target[role]) != 1:
            diagnostics['failed_role'] = role
            raise ReviewRequired('debtor_prepare_combo_target_missing_or_ambiguous')
    committed = dict(before)
    changing = set()

    def guard():
        root_guard()
        current = fields()  # checks selected pages, generated ID and editor HWND
        if _tab_runtime_id(_source_order_tab(root), diagnostics) != order_tab_identity:
            raise ReviewRequired('debtor_prepare_source_order_tab_changed')
        if {r: (c.handle, c.element_info.control_type, c.element_info.class_name)
                for r, c in current.items()} != pinned:
            raise ReviewRequired('debtor_prepare_field_identity_changed')
        values = {r: _value(c) for r, c in current.items()}
        diagnostics['last_observed'] = {'generated_id': session.generated_id, 'values': values}
        for role, value in values.items():
            allowed = {committed[role], target[role]} if role in changing else {committed[role]}
            if value not in allowed:
                diagnostics['failed_role'] = role
                raise ReviewRequired('debtor_prepare_value_changed')
        return current, values

    guard()
    for role in edits:
        current, values = guard()
        if values[role] == target[role]:
            continue
        progress['phase'] = 'write_' + role
        progress['field_writes_attempted'].append(role)
        changing.add(role)
        try:
            if current[role].iface_value.CurrentIsReadOnly:
                raise ReviewRequired('debtor_prepare_field_read_only')
            if role == 'company':  # the model takes this value only from key events
                type_into(current[role], target[role])
            else:
                current[role].iface_value.SetValue(target[role])  # literal, no keys/clipboard
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired('debtor_prepare_write_uncertain') from None
        if guard()[1][role] != target[role]:
            raise ReviewRequired('debtor_prepare_write_readback_mismatch')
        committed[role] = target[role]
        changing.remove(role)
    for role in combos:
        current, values = guard()
        observed = read_combo_options(session._pane(), current[role], native_factory)
        if observed['options'] != options[role]['options']:
            raise ReviewRequired('debtor_prepare_combo_options_changed')
        if values[role] == target[role]:
            continue
        progress['phase'] = 'select_' + role
        progress['combo_selections_attempted'].append(role)
        changing.add(role)
        after = select_combo_exact(session._pane(), current[role], target[role],
                                   native_factory, guard=guard)
        if after['options'] != options[role]['options']:
            raise ReviewRequired('debtor_prepare_combo_options_changed')
        committed[role] = target[role]
        changing.remove(role)
    first, second = guard()[1], guard()[1]
    diagnostics['final_readback'] = {'first_values': first, 'second_values': second,
        'mismatched_fields': [r for r in target if first[r] != target[r] or second[r] != target[r]],
        'changed_between_reads': [r for r in first if first[r] != second[r]]}
    if diagnostics['final_readback']['mismatched_fields'] or diagnostics['final_readback']['changed_between_reads']:
        raise ReviewRequired('debtor_prepare_final_readback_mismatch')
    progress['phase'] = 'complete'
    return {'status': 'LIVE_DEBTOR_' + page.upper() + '_PREPARED_UNSAVED',
        'page': page, 'generated_id': session.generated_id, 'generated_id_preserved': True,
        'values': second, 'final_readback': diagnostics['final_readback'],
        'source_order_tab_present': True, 'source_order_number_verified': False,
        'source_order_tab_identity_method': 'LIVE_UIA_RUNTIME_ID',
        'navigation': 'OPERATOR_SELECTED_PAGE', 'master_absence_verified': False,
        'address_roles_verified': False, 'delivery_address_verified': False,
        'payment_definition_verified': False, 'persistence_verified': False,
        'field_model_commit_verified': False,
        'note': 'Live page fields prepared and read back only. No Save or tab navigation. Master lookup, address roles, delivery, payment definition, original Order number continuity and persistence remain unverified.'}


def prepare_debtor_page(pid, handle, source, expected_id, page, *, diagnostics=None):
    root = _payment_root(pid, handle)
    title = root.element_info.name

    def root_guard():
        try:
            if (root.handle != handle or root.element_info.process_id != pid
                    or root.element_info.class_name != 'SWT_Window0' or root.element_info.name != title
                    or not root.is_visible() or not root.is_enabled()):
                raise ReviewRequired('debtor_prepare_main_window_identity_changed')
            if process_executable_name(pid).casefold() != 'fakturama.exe':
                raise ReviewRequired('debtor_prepare_executable_identity_mismatch')
            if not root.is_active():
                raise ReviewRequired('debtor_prepare_keep_fakturama_in_foreground')
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired('debtor_prepare_scope_unavailable') from None

    return prepare_debtor_page_scope(root, source, expected_id, page, root_guard,
                                    diagnostics=diagnostics)
