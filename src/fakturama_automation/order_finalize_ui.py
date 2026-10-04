"""Verify the completed Order against the source, Save it once, then create its linked Invoice from the
follow-up area. Controls are bound by label and layout, never by stored automation IDs. Item rows are
checked by order_items_ui and, here, through the Total Net / VAT / Total fields. Save and the follow-up
Invoice action are each invoked at most once and never retried.
"""

import re
import time
from decimal import Decimal, InvalidOperation

from .domain import OrderInput
from .errors import ReviewRequired
from .finance import validate_order
from .order_live_ui import _snapshot as _strict_snapshot, parse_observed_date
from .order_ui import Tree, one, resolve_order_fields
from .payment_ui import _payment_root
from .uia import process_executable_name

SHIPPING_FREE = 'Free of shipping costs'


def handle_save_contact_selector(root, source, diagnostics):
    """Resolve only the known address selector, once, during an in-flight Save."""
    from .selector_live_ui import (SelectorDriver, _windows, check_selector_scope,
                                   debtor_matcher)
    if not _windows(root, 'Select the address'):
        return False
    if 'save_contact_selector' in diagnostics:
        raise ReviewRequired('order_save_contact_selector_reappeared')
    details = diagnostics['save_contact_selector'] = {}
    title, pid = root.element_info.name, root.element_info.process_id
    def guard():
        # The modal owns focus and disables its parent during Save.
        if root.element_info.name != title or root.element_info.process_id != pid:
            raise ReviewRequired('order_save_contact_owner_changed')
    report = check_selector_scope(root, 'debtor', source.debtor.company, debtor_matcher(source),
                                  guard, SelectorDriver(), diagnostics=details, existing_dialog=True)
    details['result'] = report
    if report['status'] != 'SELECTED_EXISTING':
        raise ReviewRequired('order_save_contact_not_resolved')
    return True


def parse_display_amount(text):
    """'1.234,50 EUR' / '678,30 EUR' / 'EUR 12.00' -> Decimal; else review."""
    if not isinstance(text, str):
        raise ReviewRequired('amount_unreadable')
    raw = re.sub(r'[\s Â ]', '', text).replace('EUR', '').replace('€', '')
    if not re.fullmatch(r'-?[0-9.,]+', raw):
        raise ReviewRequired('amount_unreadable')
    if ',' in raw:  # Observed German-style display: '.' thousands, ',' decimals.
        raw = raw.replace('.', '').replace(',', '.')
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ReviewRequired('amount_unreadable') from None


def _editor(tree):
    """The one visible document editor holding a visible Cust.Ref. field.

    Before Save the pane is named 'New Order'; after Save Fakturama renames it,
    so bind by the reference field and climb to the nearest named Pane.
    """
    refs = [n for n in tree.under(tree.nodes[()], 'Edit', 'Cust.Ref.') if n.data.get('visible')]
    node = one(refs, 'document_reference')
    path = node.path
    while path:
        path = path[:-1]
        candidate = tree.nodes[path]
        if candidate.data.get('control_type') == 'Pane' and candidate.data.get('name'):
            return candidate
    raise ReviewRequired('document_editor_unresolved')


def _address_text(tree, editor):
    """Only the selected address Tab's content is exposed by SWT/UIA."""
    found = []
    for tab in tree.under(editor, 'Tab'):
        if 'address' not in tab.data.get('name', ''):
            continue
        for edit in tree.under(tab, 'Edit'):
            if edit.data.get('visible'):
                found.append(edit.data.get('patterns', {}).get('value', {}).get('CurrentValue', ''))
    return one(found, 'address_text')


def read_document(snapshot):
    tree = Tree(snapshot)
    editor = _editor(tree)
    fields = resolve_order_fields(tree, editor)
    values = {r: n.data.get('patterns', {}).get('value', {}).get('CurrentValue') for r, n in fields.items()}
    values['address_text'] = _address_text(tree, editor)
    tabs = [n.data['name'] for n in tree.under(tree.nodes[()], 'TabItem')]
    followup = tree.under(editor, 'Group', 'Create a follow-up document')
    return {'editor_name': editor.data['name'], 'values': values, 'tabs': tabs,
            'followup_available': len(followup) == 1}


def verify_order_values(values, source: OrderInput, expected_number):
    """Pure comparison of observed editor strings with the reviewed source."""
    issues = []
    if values.get('number') != expected_number:
        issues.append('number_mismatch')
    try:
        if parse_observed_date(values.get('date_text', '')) != source.order_date:
            issues.append('date_mismatch')
    except ReviewRequired:
        issues.append('date_unreadable')
    if values.get('reference') != source.external_reference:
        issues.append('reference_mismatch')
    if values.get('price_mode') != 'Net':
        issues.append('price_mode_not_net')
    if values.get('vat_mode') != 'With VAT':
        issues.append('vat_mode_not_with_vat')
    if source.order_discount_percent == 0 and values.get('discount_text') not in {'0%', '0 %', '0,00%', '0.00%'}:
        issues.append('order_discount_not_zero')
    if source.shipping_net == 0 and values.get('shipping_text') != SHIPPING_FREE:
        issues.append('shipping_not_free')
    for role, expected in (('subtotal_text', source.totals.net), ('vat_total_text', source.totals.vat),
                           ('total_text', source.totals.gross)):
        try:
            if parse_display_amount(values.get(role)) != expected:
                issues.append(role.replace('_text', '') + '_mismatch')
        except ReviewRequired:
            issues.append(role.replace('_text', '') + '_unreadable')
    address = values.get('address_text') or ''
    flat = ' '.join(address.split())
    shown = [kind for kind, a in (('delivery', source.debtor.delivery), ('billing', source.debtor.billing))
             if a.street in flat and a.zip in flat and a.city in flat]
    if not shown:
        issues.append('address_not_matching_source')
    return issues, shown


def _snapshot(root):
    """Multi-line Addresses/Remarks fields legitimately contain CR/LF/TAB."""
    return _strict_snapshot(root, allow_line_breaks=True)


def _guard_factory(root, pid, handle):
    title = root.element_info.name

    def guard():
        try:
            if (root.handle != handle or root.element_info.process_id != pid
                    or root.element_info.name != title or not root.is_visible() or not root.is_enabled()):
                raise ReviewRequired('finalize_main_window_identity_changed')
            if (process_executable_name(pid) or '').casefold() != 'fakturama.exe':
                raise ReviewRequired('finalize_executable_identity_mismatch')
            if not root.is_active():
                raise ReviewRequired('finalize_keep_fakturama_in_foreground')
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired('finalize_scope_unavailable') from None
    return guard


def _find(snapshot, wrappers, kind, name, scope_path=()):
    tree = Tree(snapshot)
    scope = tree.nodes[scope_path]
    nodes = [n for n in tree.under(scope, kind, name) if n.data.get('visible') and tree.effective_enabled(n)]
    return wrappers[one(nodes, name.lower().replace(' ', '_')).path]


def _wait(observe, ready, timeout=12.0, interval=0.5):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = observe()
            if ready(last):
                return last, True
        except ReviewRequired:
            pass
        time.sleep(interval)
    return last, False


def finalize_order_scope(root, source, expected_number, guard, *, save=False, diagnostics=None):
    validate_order(source)
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'save_invoked': False})
    guard()
    first = read_document(_snapshot(root)[0])
    second = read_document(_snapshot(root)[0])
    diagnostics['before'] = first
    if first['values'] != second['values']:
        raise ReviewRequired('order_finalize_values_unstable')
    if first['editor_name'] == expected_number and not any(t.startswith('*') and 'Order' in t for t in first['tabs']):
        raise ReviewRequired('order_already_saved_do_not_save_again')
    if first['editor_name'] != 'New Order':
        raise ReviewRequired('order_finalize_new_order_editor_not_selected')
    if save:
        from .order_address_ui import verify_order_addresses
        diagnostics['address_verification'] = verify_order_addresses(root, source, guard, diagnostics=diagnostics)
        first = read_document(_snapshot(root)[0])
    issues, shown = verify_order_values(first['values'], source, expected_number)
    diagnostics['verification'] = {'issues': issues, 'address_matches': shown,
                                   'line_items': 'TOTALS_RECONCILED_GRID_NOT_EXPOSED_IN_UIA'}
    if issues:
        raise ReviewRequired('order_finalize_verification_failed')
    if not save:
        progress['phase'] = 'complete'
        return {'status': 'ORDER_VERIFIED_UNSAVED', **diagnostics['verification'],
                'values': first['values'], 'note': 'Order matches reviewed source. Re-run with --save to save once.'}

    snapshot, wrappers = _snapshot(root)
    save_button = _find(snapshot, wrappers, 'Button', 'Save the current contents')
    guard()
    progress['phase'] = 'save'
    progress['save_invoked'] = True  # Recorded before invoking: never retry an uncertain Save.
    try:
        save_button.iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('order_save_invoke_uncertain') from None

    deadline = time.monotonic() + 90
    after, settled = None, False
    while time.monotonic() < deadline:
        handle_save_contact_selector(root, source, diagnostics)
        try:
            after = read_document(_snapshot(root)[0])
        except ReviewRequired:
            # A rebuilding editor can briefly lack fields after Save. Only
            # observations are retried; selector actions and Save are not.
            time.sleep(.5)
            continue
        if after['editor_name'] == expected_number and not any(
                t.startswith('*') and 'Order' in t for t in after['tabs']):
            settled = True
            break
        time.sleep(.5)
    diagnostics['after_save'] = after
    if not settled or after is None:
        raise ReviewRequired('order_save_not_confirmed_check_dialogs')
    post_issues, _ = verify_order_values(after['values'], source, expected_number)
    if post_issues:
        diagnostics['post_save_issues'] = post_issues
        raise ReviewRequired('order_saved_values_differ')
    progress['phase'] = 'complete'
    return {'status': 'ORDER_SAVED_AND_REREAD', 'saved_editor_name': after['editor_name'],
            'tabs': after['tabs'], 'values': after['values'], **diagnostics['verification'],
            'persistence_verified': 'EDITOR_REREAD_ONLY',
            'note': 'Save invoked once. Editor re-read after save matches. Confirm the row in Data > Documents.'}


def create_followup_invoice_scope(root, source, expected_number, guard, *, diagnostics=None):
    """Invoke the saved Order's follow-up 'Invoice' (not the toolbar Invoice)."""
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'followup_invoked': False})
    guard()
    snapshot, wrappers = _snapshot(root)
    doc = read_document(snapshot)
    diagnostics['order'] = doc
    if any(t.startswith('*') for t in doc['tabs']):
        raise ReviewRequired('followup_order_not_saved_or_unsaved_tabs_open')
    issues, _ = verify_order_values(doc['values'], source, expected_number)
    if issues:
        diagnostics['issues'] = issues
        raise ReviewRequired('followup_order_does_not_match_source')
    tree = Tree(snapshot)
    editor = _editor(tree)
    group = one(tree.under(editor, 'Group', 'Create a follow-up document'), 'followup_group')
    button = wrappers[one([n for n in tree.under(group, 'Button', 'Invoice')
                           if n.data.get('visible') and tree.effective_enabled(n)], 'followup_invoice').path]
    guard()
    progress['phase'] = 'invoke_followup'
    progress['followup_invoked'] = True
    try:
        button.iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('followup_invoice_invoke_uncertain') from None

    def observe():
        return read_document(_snapshot(root)[0])

    after, settled = _wait(observe, lambda d: 'Invoice' in d['editor_name'] or any('New Invoice' in t for t in d['tabs']))
    diagnostics['invoice_editor'] = after
    if not settled:
        raise ReviewRequired('followup_invoice_editor_not_observed')
    copied = {k: after['values'].get(k) for k in ('reference', 'subtotal_text', 'vat_total_text', 'total_text')}
    copy_issues = [k for k, v in copied.items() if v != doc['values'].get(k)]
    progress['phase'] = 'complete'
    return {'status': 'LINKED_INVOICE_EDITOR_OPENED_UNSAVED', 'invoice_editor_name': after['editor_name'],
            'tabs': after['tabs'], 'copied_from_order': copied, 'copy_issues': copy_issues,
            'note': 'Follow-up Invoice invoked once from the saved Order. Invoice not saved.'}


def _live(pid, handle, fn, *args, **kwargs):
    root = _payment_root(pid, handle)
    return fn(root, *args, _guard_factory(root, pid, handle), **kwargs)


def finalize_order(pid, handle, source, expected_number, *, save=False, diagnostics=None):
    return _live(pid, handle, finalize_order_scope, source, expected_number, save=save, diagnostics=diagnostics)


def create_followup_invoice(pid, handle, source, expected_number, *, diagnostics=None):
    return _live(pid, handle, create_followup_invoice_scope, source, expected_number, diagnostics=diagnostics)
