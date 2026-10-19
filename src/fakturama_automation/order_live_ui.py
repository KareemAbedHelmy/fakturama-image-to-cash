"""Fill the New Order header (Date, Cust.Ref., Net, With VAT) and read it back. Never saves."""

import re
import unicodedata
from datetime import date

from .combo_ui import read_combo_options, select_combo_exact
from .domain import OrderInput
from .errors import ReviewRequired
from .finance import validate_order
from .order_ui import Tree, analyze_order_header, one, resolve_order_fields
from .order_date_ui import SegmentedDateDriver, segmented_date
from .payment_ui import _payment_root
from .uia import process_executable_name

MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def parse_observed_date(text):
    """Only the unambiguous English format actually captured on this desktop."""
    match = re.fullmatch(r'(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})', text)
    if not match or match[2] not in MONTHS:
        raise ReviewRequired('order_prepare_date_format_unsupported')
    try:
        return date(int(match[3]), MONTHS.index(match[2]) + 1, int(match[1]))
    except ValueError:
        raise ReviewRequired('order_prepare_date_invalid') from None


def percent_value(text):
    """'10%' / '12,5 %' -> Decimal; anything else is unreadable."""
    from decimal import Decimal, InvalidOperation
    match = re.fullmatch(r'(-?[0-9]+(?:[.,][0-9]+)?) ?%', (text or '').strip())
    if not match:
        raise ReviewRequired('order_discount_unreadable')
    try:
        return Decimal(match[1].replace(',', '.'))
    except InvalidOperation:
        raise ReviewRequired('order_discount_unreadable') from None


LINE_BREAKS = frozenset('\r\n\t')


def _snapshot(root, *, allow_line_breaks=False):
    """Bounded unfiltered traversal also retains invisible SWT tab headers."""
    wrappers = {}

    def visit(control, path):
        if len(wrappers) >= 5000 or len(path) > 30:
            raise ReviewRequired('order_prepare_tree_truncated')
        info = control.element_info
        if info.process_id != root.element_info.process_id:
            raise ReviewRequired('order_prepare_process_mismatch')
        wrappers[path] = control
        node = {'name': info.name, 'control_type': info.control_type,
                'class_name': info.class_name, 'visible': bool(control.is_visible()),
                'enabled': bool(control.is_enabled()), 'patterns': {}}
        if info.control_type in {'Edit', 'ComboBox'}:
            try:
                value = control.iface_value.CurrentValue
                readonly = control.iface_value.CurrentIsReadOnly
                if (not isinstance(value, str) or len(value) > 1000
                        or any(unicodedata.category(c).startswith('C')
                               and not (allow_line_breaks and c in LINE_BREAKS) for c in value)):
                    raise ReviewRequired('order_prepare_value_invalid')
                node['patterns']['value'] = {'CurrentValue': value, 'CurrentIsReadOnly': readonly}
            except ReviewRequired:
                raise
            except Exception:
                pass  # Missing patterns on unrelated fields do not bind a role.
        children = control.children()
        if len(children) > 500:
            raise ReviewRequired('order_prepare_tree_truncated')
        node['children'] = [visit(child, path + (i,)) for i, child in enumerate(children)]
        return node

    try:
        return visit(root, ()), wrappers
    except ReviewRequired:
        raise
    except Exception:
        raise ReviewRequired('order_prepare_tree_unavailable') from None


def _binding(root):
    snapshot, wrappers = _snapshot(root)
    tree = Tree(snapshot)
    editor = one(tree.under(tree.nodes[()], 'Pane', 'New Order'), 'editor')
    tab = one([n for n in tree.under(tree.nodes[()], 'TabItem')
               if n.data['name'] in {'New Order', '*New Order'}], 'new_order_tab')
    if not tree.effective_enabled(editor) or not tree.effective_enabled(tab):
        raise ReviewRequired('order_prepare_editor_disabled')
    try:
        if not wrappers[tab.path].iface_selection_item.CurrentIsSelected:
            raise ReviewRequired('order_prepare_select_original_new_order_tab')
    except ReviewRequired:
        raise
    except Exception:
        raise ReviewRequired('order_prepare_tab_selection_unavailable') from None
    nodes = resolve_order_fields(tree, editor)
    for role in ('date_text', 'reference', 'price_mode', 'vat_mode'):
        node = nodes[role]
        if (not tree.effective_enabled(node) or not node.data['visible']
                or node.data.get('patterns', {}).get('value', {}).get('CurrentIsReadOnly') != 0):
            raise ReviewRequired('order_prepare_field_not_writable')
    return wrappers[editor.path], {r: wrappers[n.path] for r, n in nodes.items()}, analyze_order_header(snapshot, 'EUR')


def prepare_order_header_scope(root, source: OrderInput, expected_number, root_guard,
                               *, native_factory=None, date_driver=None, diagnostics=None):
    """One attempt per differing field. Failures never trigger Save or retry."""
    validate_order(source)
    if not isinstance(expected_number, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', expected_number):
        raise ReviewRequired('order_prepare_invalid_expected_number')
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {})
    progress.update(phase='preflight', field_writes_attempted=[], combo_selections_attempted=[])
    root_guard()
    pane, fields, initial = _binding(root)
    diagnostics['before'] = initial
    before = initial['values']
    if before['number'] != expected_number:
        raise ReviewRequired('order_prepare_generated_number_mismatch')
    if initial['currency'] != source.currency:
        raise ReviewRequired('order_prepare_display_currency_mismatch_or_unverified')
    # This header-only entry point must not reconfigure a populated order.
    for role in ('subtotal_text', 'vat_total_text', 'total_text'):
        if any(c in '123456789' for c in before[role]):
            raise ReviewRequired('order_prepare_nonzero_order_totals')
    parse_observed_date(before['date_text'])
    target = {'date_text': f'{source.order_date.day} {MONTHS[source.order_date.month - 1]} {source.order_date.year:04d}',
              'reference': source.external_reference, 'price_mode': 'Net', 'vat_mode': 'With VAT'}
    diagnostics['expected_header_values'] = dict(target)
    if before['reference'] not in {'', target['reference']}:
        raise ReviewRequired('order_prepare_reference_conflict')
    if before['discount_text'] != '0%' or before['shipping_text'] != 'Free of shipping costs':
        raise ReviewRequired('order_prepare_discount_or_shipping_conflict')
    handles = {r: c.handle for r, c in fields.items()}
    if not pane.handle or any(not h for h in handles.values()):
        raise ReviewRequired('order_prepare_native_identity_unavailable')
    options = {}
    for role in ('price_mode', 'vat_mode'):
        options[role] = read_combo_options(pane, fields[role], native_factory)['options']
        if options[role].count(target[role]) != 1:
            raise ReviewRequired('order_prepare_target_option_missing_or_ambiguous')

    date_editing = False

    def guard():
        root_guard()
        current_pane, current_fields, observed = _binding(root)
        if current_pane.handle != pane.handle or {r: c.handle for r, c in current_fields.items()} != handles:
            raise ReviewRequired('order_prepare_control_identity_changed')
        values = observed['values']
        diagnostics['last_observed'] = {'phase': progress['phase'],
                                        'values': dict(values), 'currency': observed['currency']}
        if values['number'] != expected_number:
            raise ReviewRequired('order_prepare_generated_number_changed')
        if observed['currency'] != source.currency:
            raise ReviewRequired('order_prepare_display_currency_changed')
        for role in target:
            if role == 'date_text' and date_editing:
                # The segmented widget temporarily renders the selected month
                # numerically. Other roles and all native identities stay pinned.
                segmented_date(values[role])
                continue
            if values[role] not in {before[role], target[role]}:
                raise ReviewRequired('order_prepare_header_conflict')
        if values['shipping_text'] != before['shipping_text']:
            raise ReviewRequired('order_prepare_unrelated_value_changed')
        if values['discount_text'] != before['discount_text']:
            raise ReviewRequired('order_prepare_unrelated_value_changed')
        for role in ('subtotal_text', 'vat_total_text', 'total_text'):
            if any(c in '123456789' for c in values[role]):
                raise ReviewRequired('order_prepare_order_totals_changed')
        return current_fields, observed

    for role in ('date_text', 'reference'):
        current, observed = guard()
        if observed['values'][role] == target[role]:
            continue
        progress['phase'] = 'write_' + role
        progress['field_writes_attempted'].append(role)
        if role == 'date_text':
            date_editing = True
            try:
                (date_driver or SegmentedDateDriver()).set_date(
                    root, current[role], current['reference'], source.order_date,
                    guard, progress, diagnostics)
            finally:
                date_editing = False
        else:
            try:
                current[role].iface_value.SetValue(target[role])
            except Exception:
                raise ReviewRequired('order_prepare_field_write_uncertain') from None
        if guard()[1]['values'][role] != target[role]:
            raise ReviewRequired('order_prepare_field_readback_mismatch')
    for role in ('price_mode', 'vat_mode'):
        current, observed = guard()
        progress['phase'] = 'select_' + role
        if observed['values'][role] != target[role]:
            progress['combo_selections_attempted'].append(role)
        after = select_combo_exact(pane, current[role], target[role], native_factory, guard=guard)
        if after['options'] != options[role]:
            raise ReviewRequired('order_prepare_options_changed')
    final = guard()[1]
    confirmation = guard()[1]
    diagnostics['final_readback'] = {
        'first_values': dict(final['values']),
        'second_values': dict(confirmation['values']),
        'mismatched_fields': [r for r, v in target.items()
                              if final['values'][r] != v or confirmation['values'][r] != v],
        'changed_between_reads': [r for r in final['values']
                                  if final['values'][r] != confirmation['values'][r]],
    }
    if (diagnostics['final_readback']['changed_between_reads']
            or diagnostics['final_readback']['mismatched_fields']):
        raise ReviewRequired('order_prepare_final_readback_mismatch')
    if parse_observed_date(final['values']['date_text']) != source.order_date:
        raise ReviewRequired('order_prepare_date_readback_mismatch')
    progress['phase'] = 'complete'
    return {**final, 'status': 'LIVE_ORDER_HEADER_PREPARED_UNSAVED',
            'generated_number_preserved': True, 'date_text_as_iso': source.order_date.isoformat(),
            'persistence_verified': False, 'date_model_commit_verified': False,
            'date_entry_method': diagnostics.get('date_entry', {}).get('method', 'already_matching'),
            'date_after_focus_change_verified': 'after_focus_date_as_iso' in diagnostics.get('date_entry', {}),
            'date_entry': diagnostics.get('date_entry', {'method': 'already_matching'}),
            'final_readback': diagnostics['final_readback'],
            'customer_link_verified': False, 'item_entry_verified': False,
            'note': 'Live date/reference fields and pricing modes prepared and read back. Generated number preserved. No Save invoked. Display currency only; saved date model, customer, items and persistence remain unverified.'}


def prepare_order_header(pid, handle, source, expected_number, *, diagnostics=None):
    root = _payment_root(pid, handle)
    title = root.element_info.name

    def guard():
        try:
            if (root.handle != handle or root.element_info.process_id != pid
                    or root.element_info.class_name != 'SWT_Window0' or root.element_info.name != title
                    or not root.is_visible() or not root.is_enabled()):
                raise ReviewRequired('order_prepare_main_window_identity_changed')
            if process_executable_name(pid).casefold() != 'fakturama.exe':
                raise ReviewRequired('order_prepare_executable_identity_mismatch')
            if not root.is_active():
                raise ReviewRequired('order_prepare_keep_fakturama_in_foreground')
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired('order_prepare_scope_unavailable') from None

    return prepare_order_header_scope(root, source, expected_number, guard, diagnostics=diagnostics)
