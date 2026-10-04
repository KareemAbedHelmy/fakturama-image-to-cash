"""Fill the New product editor (item number, name, description, VAT, gross price) from the source order
and read it back.
"""

import re
from decimal import Decimal

from .combo_ui import read_combo_options, select_combo_exact
from .debtor_live_ui import _source_order_tab, _tab_runtime_id
from .debtor_ui import _tab_items, _value
from .errors import ReviewRequired
from .finance import product_gross, validate_order
from .matching import vat_name
from .payment_ui import _payment_root
from .selector_ui import exactly_one
from .uia import Locator, enabled_within_scope, process_executable_name, unique_control


def _money(text, require_currency=False):
    match = re.fullmatch(r'(\d+(?:[.,]\d{1,2})?)([ \u00a0]*EUR)?', text)
    if not match or (require_currency and not match[2]):
        raise ReviewRequired('product_prepare_price_format_or_currency_unverified')
    return Decimal(match[1].replace(',', '.'))


def _labeled_amount(pane, name):
    label = unique_control(pane, Locator('Text', name=name))
    siblings = label.parent().children()
    index = siblings.index(label)
    if index + 1 >= len(siblings) or siblings[index + 1].element_info.control_type != 'Pane':
        raise ReviewRequired('product_prepare_price_structure_changed')
    return unique_control(siblings[index + 1], Locator('Edit', name=''))


def _fields(root):
    tab = exactly_one([c for c in _tab_items(root) if c.element_info.name in
                      {'New product', '*New product'}], 'new_product_tab', 'product_prepare')
    if not tab.iface_selection_item.CurrentIsSelected or not enabled_within_scope(tab, root):
        raise ReviewRequired('product_prepare_new_product_not_selected')
    pane = unique_control(root, Locator('Pane', name='New product'))
    fields = {role: unique_control(pane, Locator('Edit', name=label)) for role, label in
        [('sku', 'Item Number'), ('name', 'Name'), ('description', 'Description'),
         ('stock', 'Stock'), ('category', 'Category'), ('gtin', 'GTIN'),
         ('supplier_code', 'supplier code'), ('allowance', 'allowance'),
         ('user1', 'user defined field 1'), ('user2', 'user defined field 2'),
         ('user3', 'user defined field 3')]}
    fields['gross'] = _labeled_amount(pane, 'Price (gross)')
    fields['cost'] = _labeled_amount(pane, 'cost price (net)')
    fields['vat'] = unique_control(pane, Locator('ComboBox', name='VAT'))
    if (not pane.handle or pane.element_info.process_id != root.element_info.process_id
            or any(not c.handle or c.element_info.process_id != root.element_info.process_id
                   for c in fields.values())):
        raise ReviewRequired('product_prepare_field_identity_unavailable')
    return pane, fields


class PriceKeyboardDriver:
    """Numeric keyboard input to one focused native Edit, never clipboard or Save."""

    def __init__(self, *, native_factory=None, focus_check=None, send=None):
        from .combo_ui import _native_wrapper
        self.native_factory = native_factory or _native_wrapper
        if focus_check is None:
            from .windows_clipboard import WindowsClipboard
            checker = WindowsClipboard()  # Only its native focus checks are used.
            focus_check = lambda root, control: checker.assert_focus(
                root.handle, control.handle, root.element_info.process_id, allow_caret_blink=True)
        self.focus_check = focus_check
        if send is None:
            from pywinauto.keyboard import send_keys
            send = send_keys
        self.send = send

    def enter(self, root, control, text, guard, progress, diagnostics):
        if not re.fullmatch(r'[0-9]{1,14}[.,][0-9]{2}', text):
            raise ReviewRequired('product_price_keyboard_value_invalid')
        native = self.native_factory(control.handle)
        actions = progress.setdefault('price_keyboard_actions_attempted', [])

        def check():
            guard()
            self.focus_check(root, control)
            if (native.handle != control.handle or native.element_info.process_id != root.element_info.process_id
                    or native.class_name() != 'Edit' or not native.is_visible() or not native.is_enabled()
                    or control.element_info.class_name != 'Edit'
                    or control.iface_value.CurrentIsReadOnly):
                raise ReviewRequired('product_price_native_identity_mismatch')
            value = native.window_text()
            if value != _value(control):
                raise ReviewRequired('product_price_native_uia_mismatch')
            return value

        def send(keys, role):
            check()
            progress['phase'] = 'price_keyboard_' + role
            actions.append({'role': role, 'keys': keys})
            try:
                self.send(keys, pause=0.06, vk_packet=False)
            except Exception:
                raise ReviewRequired('product_price_keyboard_action_uncertain') from None
            check()

        check()
        send('^a', 'select_all')
        value = check()
        selection = native.selection_indices()
        if not isinstance(selection, (tuple, list)) or tuple(selection) != (0, len(value)):
            raise ReviewRequired('product_price_full_selection_unverified')
        diagnostics['price_entry'] = {'method': 'FOCUSED_NUMERIC_KEYBOARD',
                                    'selection_range': list(selection)}
        send(text, 'replace_amount')
        if _money(check()) != _money(text):
            raise ReviewRequired('product_price_keyboard_readback_mismatch')


def prepare_product_scope(root, source, sku, root_guard, *, native_factory=None, diagnostics=None,
                          price_driver=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics['progress'] = {'phase': 'preflight', 'field_writes_attempted': [],
        'combo_selections_attempted': [], 'focus_actions_attempted': []}
    validate_order(source)
    matches = [item for item in source.items if item.sku == sku]
    if not matches:
        raise ReviewRequired('product_prepare_sku_not_in_source')
    if len({(i.description, i.unit_net, i.vat_percent, i.unit) for i in matches}) != 1:
        raise ReviewRequired('product_prepare_conflicting_source_master')
    item = matches[0]
    gross = product_gross(item)  # transaction quantity/discount intentionally excluded
    root_guard()
    pane, fields = _fields(root)
    pane_handle = pane.handle
    order_identity = _tab_runtime_id(_source_order_tab(root), diagnostics)
    pinned = {r: (c.handle, c.element_info.class_name) for r, c in fields.items()}
    before = {r: _value(c) for r, c in fields.items()}
    diagnostics['before_values'] = before
    target = {**before, 'sku': item.sku, 'name': item.description,
              'description': item.description, 'vat': vat_name(item.vat_percent),
              'gross': format(gross, '.2f')}
    diagnostics['expected_product'] = {r: target[r] for r in ('sku', 'name', 'description', 'vat', 'gross')}
    for role in ('sku', 'name', 'description'):
        if before[role] not in ('', target[role]):
            raise ReviewRequired('product_prepare_existing_text_conflict')
    if (_money(before['cost'], True) != 0 or _money(before['stock']) != 0
            or _money(before['gross'], True) not in (Decimal(0), gross)):
        raise ReviewRequired('product_prepare_existing_price_or_stock_conflict')
    for role in ('sku', 'name', 'description', 'gross'):
        if fields[role].iface_value.CurrentIsReadOnly:
            raise ReviewRequired('product_prepare_field_read_only')
    # Focus is needed for numeric keyboard entry and committed price readback.
    try:
        for role in ('gross', 'name'):
            if not callable(fields[role].element_info.element.SetFocus):
                raise AttributeError()
    except Exception:
        raise ReviewRequired('product_prepare_focus_unavailable') from None
    options = read_combo_options(pane, fields['vat'], native_factory)
    diagnostics['vat_options'] = options
    if options['options'].count(target['vat']) != 1:
        raise ReviewRequired('product_prepare_vat_missing_or_ambiguous')
    committed, pending = dict(before), set()

    def guard():
        root_guard()
        current_pane, current = _fields(root)
        if (current_pane.handle != pane_handle or
                {r: (c.handle, c.element_info.class_name) for r, c in current.items()} != pinned):
            raise ReviewRequired('product_prepare_field_identity_changed')
        if _tab_runtime_id(_source_order_tab(root), diagnostics) != order_identity:
            raise ReviewRequired('product_prepare_source_order_tab_changed')
        values = {r: _value(c) for r, c in current.items()}
        diagnostics['last_values'] = values
        for role, value in values.items():
            if role == 'gross':
                allowed = {_money(committed[role])}
                if role in pending: allowed.add(gross)
                valid = _money(value) in allowed
            else:
                allowed = {committed[role], target[role]} if role in pending else {committed[role]}
                valid = value in allowed
            if not valid:
                diagnostics['failed_role'] = role
                raise ReviewRequired('product_prepare_value_changed')
        return current_pane, current, values

    guard()
    if before['vat'] != target['vat']:
        progress['phase'] = 'select_vat'
        progress['combo_selections_attempted'].append('vat')
        pending.add('vat')
        select_combo_exact(pane, fields['vat'], target['vat'], native_factory, guard=guard)
        committed['vat'] = target['vat']
        pending.remove('vat')
    for role in ('sku', 'name', 'description', 'gross'):
        _, current, values = guard()
        if role == 'gross':
            progress['phase'] = 'focus_price'
            progress['focus_actions_attempted'].append('gross')
            try:
                current['gross'].element_info.element.SetFocus()
            except Exception:
                raise ReviewRequired('product_prepare_focus_uncertain') from None
            _, current, values = guard()
        # Re-enter the reviewed amount even if its display already matches:
        # a prior SetValue may have changed text without updating the model.
        if role != 'gross' and values[role] == target[role]:
            continue
        progress['phase'] = 'write_' + role
        progress['field_writes_attempted'].append(role)
        pending.add(role)
        text = target[role]
        if role == 'gross' and ',' in before['gross']: text = text.replace('.', ',')
        try:
            if current[role].iface_value.CurrentIsReadOnly:
                raise ReviewRequired('product_prepare_field_read_only')
            if role == 'gross':
                driver = price_driver or PriceKeyboardDriver()
                driver.enter(root, current[role], text, guard, progress, diagnostics)
            else:
                current[role].iface_value.SetValue(text)
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired('product_prepare_write_uncertain') from None
        observed = guard()[2][role]
        if not (_money(observed) == gross if role == 'gross' else observed == target[role]):
            raise ReviewRequired('product_prepare_write_readback_mismatch')
        committed[role] = target[role]
        pending.remove(role)
    progress['phase'] = 'commit_price_focus'
    progress['focus_actions_attempted'].append('name')
    try:
        guard()[1]['name'].element_info.element.SetFocus()
    except ReviewRequired:
        raise
    except Exception:
        raise ReviewRequired('product_prepare_focus_uncertain') from None
    first, second = guard()[2], guard()[2]
    if first != second or _money(second['gross'], True) != gross:
        raise ReviewRequired('product_prepare_final_readback_mismatch')
    progress['phase'] = 'complete'
    return {'status': 'LIVE_PRODUCT_PREPARED_UNSAVED', 'sku': item.sku,
        'values': second, 'gross_price_eur': format(gross, '.2f'),
        'price_entry_method': diagnostics.get('price_entry', {}).get('method', 'UNCHANGED_VALUE'),
        'transaction_discount_applied_to_master': False,
        'final_readback': {'first_values': first, 'second_values': second},
        'vat_definition_verification': 'OPERATOR_REVIEW_REQUIRED',
        'source_order_tab_present': True, 'source_order_number_verified': False,
        'master_absence_verified': False, 'persistence_verified': False,
        'order_line_created': False,
        'note': 'Product fields and exact VAT label read back after focus change. VAT numeric value/code and product absence require operator review. No Save, Product selection or Order line entry invoked.'}


def prepare_product(pid, handle, source, sku, *, diagnostics=None):
    root = _payment_root(pid, handle)
    title = root.element_info.name

    def guard():
        if (root.handle != handle or root.element_info.process_id != pid
                or root.element_info.class_name != 'SWT_Window0' or root.element_info.name != title
                or not root.is_visible() or not root.is_enabled()
                or process_executable_name(pid).casefold() != 'fakturama.exe'):
            raise ReviewRequired('product_prepare_main_window_identity_changed')
        if not root.is_active():
            raise ReviewRequired('product_prepare_keep_fakturama_in_foreground')

    return prepare_product_scope(root, source, sku, guard, diagnostics=diagnostics)
