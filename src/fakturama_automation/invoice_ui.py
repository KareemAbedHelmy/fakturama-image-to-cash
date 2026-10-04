"""Part 5: apply the extracted payment status on the linked Invoice and Save it once.

Observed linked-invoice editor (Fakturama 2.2.0): a 'paid' CheckBox (Toggle
pattern) followed by a payment ComboBox. Per DocumentEditor.createDepositContainer,
ticking 'paid' by user action fills Value with the invoice total and the paid
date with today; we then set only the date to the extracted Payment Date using
the same guarded CDateTime keyboard driver as the Order Date. Proposed Invoice
No./Date/Service date are never changed. UNPAID leaves 'paid' clear and invents
nothing. Save and the toggle are each attempted once and never retried.
"""

from .combo_ui import read_combo_options, select_combo_exact
from .errors import ReviewRequired
from .finance import validate_order
from .order_date_ui import SegmentedDateDriver, segmented_date
from .order_finalize_ui import _editor, _guard_factory, _snapshot, _wait, parse_display_amount, read_document
from .order_ui import Tree, one
from .payment_ui import _payment_root


def _paid_controls(tree, editor):
    """Bind the paid CheckBox, payment ComboBox and (if shown) paid-date / Value edits."""
    box = one([n for n in tree.under(editor, 'CheckBox', 'paid') if n.data.get('visible')], 'paid_checkbox')
    parent = tree.parent(box)
    later = [tree.nodes[parent.path + (i,)] for i in range(box.path[-1] + 1, len(parent.data['children']))]
    combos = [n for c in later for n in ([c] if c.data['control_type'] == 'ComboBox' else tree.under(c, 'ComboBox'))
              if n.data.get('visible')]
    payment = one(combos[:1], 'payment_combo')
    labels = [n for c in later for n in tree.under(c, 'Text') if n.data.get('visible')]

    def edit_after(label):
        siblings = tree.parent(label).data['children']
        nxt = tree.nodes[label.path[:-1] + (label.path[-1] + 1,)] if label.path[-1] + 1 < len(siblings) else None
        if nxt is None:
            return None
        if nxt.data['control_type'] == 'Edit':
            return nxt
        edits = [e for e in tree.under(nxt, 'Edit') if e.data.get('visible')]
        return edits[0] if len(edits) == 1 else None

    found = {'labels': [l.data['name'] for l in labels]}
    for label in labels:
        name = label.data['name'].casefold()
        if name in {'at', 'paid at', 'paid on', 'date of payment'}:
            found['paid_date'] = edit_after(label)
        elif name == 'value':
            found['value'] = edit_after(label)
    return box, payment, found


def _value_of(node):
    return node.data.get('patterns', {}).get('value', {}).get('CurrentValue') if node else None


def _state(root):
    snapshot, wrappers = _snapshot(root)
    tree = Tree(snapshot)
    editor = _editor(tree)
    doc = read_document(snapshot)
    box, payment, found = _paid_controls(tree, editor)
    try:
        toggled = wrappers[box.path].iface_toggle.CurrentToggleState == 1
    except Exception:
        raise ReviewRequired('invoice_paid_state_unreadable') from None
    reference = one([n for n in tree.under(editor, 'Edit', 'Cust.Ref.') if n.data.get('visible')], 'reference')
    return {'doc': doc, 'paid': toggled, 'payment': _value_of(payment),
            'paid_date_text': _value_of(found.get('paid_date')), 'value_text': _value_of(found.get('value')),
            'labels': found['labels']}, {
        'box': wrappers[box.path], 'payment': wrappers[payment.path],
        'editor': wrappers[editor.path],
        'paid_date': wrappers[found['paid_date'].path] if found.get('paid_date') else None,
        'reference': wrappers[reference.path]}


def _public(state):
    return {k: v for k, v in state.items() if k != 'doc'} | {'editor_name': state['doc']['editor_name'],
                                                            'values': state['doc']['values']}


def verify_invoice(state, source):
    issues = []
    values = state['doc']['values']
    if values.get('reference') != source.external_reference:
        issues.append('reference_not_copied')
    for role, expected in (('subtotal_text', source.totals.net), ('vat_total_text', source.totals.vat),
                           ('total_text', source.totals.gross)):
        try:
            if parse_display_amount(values.get(role)) != expected:
                issues.append(role + '_mismatch')
        except ReviewRequired:
            issues.append(role + '_unreadable')
    if values.get('price_mode') != 'Net' or values.get('vat_mode') != 'With VAT':
        issues.append('vat_mode_not_copied')
    if state['payment'] != source.payment.method:
        issues.append('payment_method_mismatch')
    if source.payment.status == 'PAID':
        if not state['paid']:
            issues.append('paid_not_set')
        try:
            if segmented_date(state['paid_date_text'] or '')[0] != source.payment.date:
                issues.append('payment_date_mismatch')
        except ReviewRequired:
            issues.append('payment_date_unreadable')
        try:
            if parse_display_amount(state['value_text']) != source.totals.gross:
                issues.append('paid_value_mismatch')
        except ReviewRequired:
            issues.append('paid_value_unreadable')
    elif state['paid']:
        issues.append('paid_set_but_source_unpaid')
    return issues


def pay_invoice_scope(root, source, guard, *, save=False, diagnostics=None, date_driver=None):
    validate_order(source)
    if source.payment.status not in {'PAID', 'UNPAID'}:
        raise ReviewRequired('invoice_payment_status_requires_review')
    if source.payment.status == 'PAID' and source.payment.date is None:
        raise ReviewRequired('invoice_paid_without_date')
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'paid_toggle_invoked': False,
                                                   'date_keyboard_actions_attempted': [], 'save_invoked': False})
    guard()
    state, controls = _state(root)
    diagnostics['before'] = _public(state)
    if state['doc']['editor_name'] != 'New Invoice' or '*New Invoice' not in state['doc']['tabs']:
        if any(t.startswith('INV') for t in state['doc']['tabs']) and not save:
            pass  # read-only verification of a saved invoice is allowed below
        else:
            raise ReviewRequired('invoice_new_linked_invoice_not_selected')
    pre = [i for i in verify_invoice(state, source) if i not in {'payment_method_mismatch',
                                                                'paid_not_set', 'payment_date_mismatch',
                                                                'payment_date_unreadable', 'paid_value_mismatch',
                                                                'paid_value_unreadable'}]
    if pre:
        diagnostics['issues'] = pre
        raise ReviewRequired('invoice_does_not_match_source')

    if state['payment'] != source.payment.method:
        before = read_combo_options(controls['editor'], controls['payment'])
        diagnostics['payment_options'] = before
        if before['options'].count(source.payment.method) != 1:
            raise ReviewRequired('invoice_payment_method_missing_or_ambiguous')
        original_values = dict(state['doc']['values'])
        original_paid = state['paid']
        original_number = state['doc']['values'].get('number')
        original_combo_handle = controls['payment'].handle

        def payment_guard():
            guard()
            current, current_controls = _state(root)
            if (current['doc']['values'] != original_values or current['paid'] != original_paid
                    or current['doc']['values'].get('number') != original_number
                    or current_controls['payment'].handle != original_combo_handle
                    or current['payment'] not in {state['payment'], source.payment.method}):
                raise ReviewRequired('invoice_payment_selection_changed_document')

        progress['phase'] = 'select_payment_method'
        progress['payment_selections_attempted'] = 1
        select_combo_exact(controls['editor'], controls['payment'], source.payment.method,
                           guard=payment_guard)
        payment_guard()
        state, controls = _state(root)
        if state['payment'] != source.payment.method:
            raise ReviewRequired('invoice_payment_selection_readback_mismatch')

    if source.payment.status == 'PAID' and not state['paid'] and state['paid_date_text'] is not None:
        # Paid fields visible but toggle reads clear: clicking again could untick it.
        raise ReviewRequired('invoice_paid_state_inconsistent')
    if source.payment.status == 'PAID' and not state['paid']:
        guard()
        progress['phase'] = 'toggle_paid'
        progress['paid_toggle_invoked'] = True
        try:
            controls['box'].iface_toggle.Toggle()
        except Exception:
            raise ReviewRequired('invoice_paid_toggle_uncertain') from None
        _, ok = _wait(lambda: _state(root)[0], lambda s: s['paid'] and s['paid_date_text'] is not None)
        if not ok:
            raise ReviewRequired('invoice_paid_fields_not_observed')
        state, controls = _state(root)

    if source.payment.status == 'PAID':
        diagnostics['after_toggle'] = _public(state)
        current, _ = segmented_date(state['paid_date_text'] or '')
        if current != source.payment.date:
            progress['phase'] = 'set_payment_date'
            (date_driver or SegmentedDateDriver()).set_date(
                root, controls['paid_date'], controls['reference'], source.payment.date,
                guard, progress, diagnostics)
            state, controls = _state(root)

    first, second = _state(root)[0], _state(root)[0]
    issues = verify_invoice(first, source)
    diagnostics['final'] = _public(first)
    if issues or first['paid_date_text'] != second['paid_date_text'] or first['paid'] != second['paid']:
        diagnostics['issues'] = issues
        raise ReviewRequired('invoice_payment_readback_mismatch')
    if not save:
        progress['phase'] = 'complete'
        return {'status': 'INVOICE_PAYMENT_APPLIED_UNSAVED', **_public(first),
                'note': 'Payment status applied and read back. Re-run with --save to save once.'}

    snapshot, wrappers = _snapshot(root)
    tree = Tree(snapshot)
    button = one([n for n in tree.under(tree.nodes[()], 'Button', 'Save the current contents')
                  if n.data.get('visible') and tree.effective_enabled(n)], 'save')
    guard()
    progress['phase'] = 'save'
    progress['save_invoked'] = True  # recorded before invoking; never retried
    try:
        wrappers[button.path].iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('invoice_save_invoke_uncertain') from None
    after, ok = _wait(lambda: _state(root)[0],
                      lambda s: not any(t.startswith('*') and 'Invoice' in t for t in s['doc']['tabs']), timeout=90)
    diagnostics['after_save'] = _public(after) if after else None
    if not ok:
        raise ReviewRequired('invoice_save_not_confirmed_check_dialogs')
    post = verify_invoice(after, source)
    if post:
        diagnostics['issues'] = post
        raise ReviewRequired('invoice_saved_values_differ')
    progress['phase'] = 'complete'
    return {'status': 'INVOICE_SAVED_AND_REREAD', **_public(after),
            'note': 'Save invoked once; saved Invoice re-read: payment method, paid state, date and Value match.'}


def pay_invoice(pid, handle, source, *, save=False, diagnostics=None):
    root = _payment_root(pid, handle)
    return pay_invoice_scope(root, source, _guard_factory(root, pid, handle), save=save, diagnostics=diagnostics)
