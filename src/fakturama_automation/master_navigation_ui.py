"""Guarded transitions between the retained Order and a new Debtor editor."""

from .debtor_ui import DebtorEditorSession, _tab_items
from .errors import ReviewRequired
from .finance import validate_order
from .order_finalize_ui import _guard_factory, _snapshot, _wait, read_document
from .order_ui import Tree, one
from .payment_ui import _payment_root
from .tab_navigation_ui import select_tab


def open_new_debtor_scope(root, source, expected_number, guard, *, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'open_invoked': False})
    validate_order(source)
    guard()
    before = read_document(_snapshot(root)[0])
    if (before['editor_name'] != 'New Order' or before['values']['number'] != expected_number
            or before['values']['reference'] != source.external_reference):
        raise ReviewRequired('debtor_open_source_order_mismatch')
    snapshot, wrappers = _snapshot(root)
    tree = Tree(snapshot)
    button = wrappers[one([n for n in tree.under(tree.nodes[()], 'SplitButton',
                                          'Create a new contact')
                           if n.data.get('visible') and tree.effective_enabled(n)],
                          'create_new_contact').path]
    tabs = [n.data['name'] for n in tree.under(tree.nodes[()], 'TabItem')]
    if any(t in {'New Debtor', '*New Debtor'} for t in tabs):
        raise ReviewRequired('debtor_open_new_editor_already_open')
    progress['phase'] = 'open'
    progress['open_invoked'] = True
    try:
        button.iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('debtor_open_invoke_uncertain') from None
    session, ok = _wait(lambda: DebtorEditorSession(root), lambda d: bool(d.generated_id))
    if not ok:
        raise ReviewRequired('debtor_open_editor_not_observed')
    progress['phase'] = 'complete'
    return {'status': 'NEW_DEBTOR_EDITOR_OPENED', 'generated_id': session.generated_id,
            'source_order_number': expected_number}


def return_to_order_scope(root, source, expected_number, guard, *, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'tab_selection_attempted': False})
    validate_order(source)
    guard()
    tabs = _tab_items(root)
    if sum(t.element_info.name in {'New Order', '*New Order'} for t in tabs) != 1:
        raise ReviewRequired('return_order_tab_missing_or_ambiguous')
    progress['phase'] = 'select_order'
    progress['tab_selection_attempted'] = True
    select_tab(root, {'New Order', '*New Order'}, guard)
    doc = read_document(_snapshot(root)[0])
    if (doc['editor_name'] != 'New Order' or doc['values']['number'] != expected_number
            or doc['values']['reference'] != source.external_reference
            or doc['values']['price_mode'] != 'Net' or doc['values']['vat_mode'] != 'With VAT'):
        raise ReviewRequired('return_order_identity_or_header_mismatch')
    progress['phase'] = 'complete'
    return {'status': 'SOURCE_ORDER_RESELECTED', 'source_order_number': expected_number,
            'reference': source.external_reference}


def open_new_debtor(pid, handle, source, expected_number, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return open_new_debtor_scope(root, source, expected_number, _guard_factory(root, pid, handle),
                                 diagnostics=diagnostics)


def return_to_order(pid, handle, source, expected_number, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return return_to_order_scope(root, source, expected_number, _guard_factory(root, pid, handle),
                                  diagnostics=diagnostics)
