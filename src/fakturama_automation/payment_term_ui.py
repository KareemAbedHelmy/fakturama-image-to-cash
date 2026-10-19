"""Terms of payment: existence check in Data > terms of payment and creation of a missing term.

The list is read with Select All / Copy (columns: Standard, Name, Description, cash discount, discount days,
net days). A term counts as present when exactly one row is named like the source payment method with zero
discount and zero days; the same name with other values, or two such rows, stops the run. A new term gets the
method as Name and Description, the matching payment code (Bank Transfer -> Credit transfer, Credit Card ->
Credit card, SEPA Direct Debit -> SEPA direct debit) and Fakturama's zero defaults; Standard is untouched. The
editor needs the retained Order open. The term is created before the New Debtor editor is opened, because that
editor reads its payment list only once, when it opens. Save is invoked once and never retried.
"""

import time

from .errors import ReviewRequired
from .finance import validate_order
from .launcher import bring_to_front
from .master_list_ui import open_list_pane, read_list_text
from .order_finalize_ui import _guard_factory, _snapshot, _wait
from .order_ui import Tree, one
from .payment_table import inspect_copied_payment_rows
from .payment_ui import EDITOR_CAPTIONS, PaymentEditorSession, _payment_root
from .uia import Locator, unique_control


def _list_pane(root, guard):
    return open_list_pane(root, guard, 'terms of payment', 'terms of payment')


def check_payment_scope(root, source, guard, *, diagnostics=None, driver=None):
    from .windows_clipboard import WindowsClipboard
    validate_order(source)
    method = source.payment.method
    diagnostics = diagnostics if diagnostics is not None else {}
    guard()
    pane = _list_pane(root, guard)
    text = read_list_text(root, pane, guard, driver or WindowsClipboard())
    if not text.strip():
        raise ReviewRequired('payment_list_unexpectedly_empty')  # Fakturama always keeps a standard term
    report = inspect_copied_payment_rows(text, method)
    diagnostics['row_count'] = len(report['rows'])
    return {'status': 'PAYMENT_EXISTS' if report['exact_name_count'] == 1 else 'PAYMENT_MISSING',
            'method': method, 'rows': [[r['name'], r['description']] for r in report['rows']]}


def _tab_names(root):
    tree = Tree(_snapshot(root)[0])
    return [n.data['name'] for n in tree.under(tree.nodes[()], 'TabItem')]


def create_payment_scope(root, source, guard, *, diagnostics=None):
    validate_order(source)
    method = source.payment.method
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'open_invoked': False,
                                                   'save_invoked': False})
    guard()
    tabs = _tab_names(root)
    if not ({'New Order', '*New Order'} & set(tabs)):
        raise ReviewRequired('payment_create_needs_the_order_editor')
    reuse = bool(EDITOR_CAPTIONS & set(tabs))  # a blank editor left open by an interrupted run
    progress['reused_open_editor'] = reuse
    if not reuse:
        _list_pane(root, guard)
        button = unique_control(root, Locator('Button', name='Create a new term of payment'))
        progress['phase'] = 'open'
        progress['open_invoked'] = True
        try:
            button.iface_invoke.Invoke()
        except Exception:
            raise ReviewRequired('payment_open_invoke_uncertain') from None
    editor, deadline = None, time.monotonic() + 8
    while editor is None:
        found = [p for p in root.descendants(control_type='Pane')
                 if p.element_info.name == 'New Term of Payment' and p.is_visible()]
        editor = found[0] if len(found) == 1 else None
        if editor is None and time.monotonic() > deadline:
            raise ReviewRequired('payment_new_editor_not_observed')
        time.sleep(.2)
    bring_to_front(root.handle)  # opening an editor moves focus; restore it before any guarded step
    unique_control(editor, Locator('Edit', name='Name')).click_input()  # a real click activates the editor
    time.sleep(.5)
    guard()
    progress['phase'] = 'fill'
    prepared = PaymentEditorSession(root, guard, require_debtor=False).prepare(method)
    diagnostics['prepared'] = prepared['values']
    snapshot, wrappers = _snapshot(root)
    tree = Tree(snapshot)
    save = one([n for n in tree.under(tree.nodes[()], 'Button', 'Save the current contents')
                if n.data.get('visible') and tree.effective_enabled(n)], 'save')
    guard()
    progress['phase'] = 'save'
    progress['save_invoked'] = True  # recorded before invoking; never retried
    try:
        wrappers[save.path].iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('payment_save_invoke_uncertain') from None
    after, ok = _wait(lambda: _tab_names(root), lambda t: not (EDITOR_CAPTIONS & set(t)))
    diagnostics['tabs_after'] = after
    if not ok:
        raise ReviewRequired('payment_save_not_confirmed_check_dialogs')
    progress['phase'] = 'complete'
    return {'status': 'PAYMENT_SAVED', 'method': method, 'code': prepared['values']['code'], 'tabs': after,
            'note': 'Saved once; re-run payment-term-check to confirm it in the list.'}


def check_payment(pid, handle, source, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return check_payment_scope(root, source, _guard_factory(root, pid, handle), diagnostics=diagnostics)


def create_payment(pid, handle, source, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return create_payment_scope(root, source, _guard_factory(root, pid, handle), diagnostics=diagnostics)
