"""Steps 3.7 and 3.11: open New product from the toolbar and Save the filled Product once.

Field entry stays in product_live_ui.prepare_product (reviewed JSON -> form).
Each action is invoked at most once; uncertain outcomes are reported, never retried.
"""

from .errors import ReviewRequired
from .finance import product_gross, validate_order
from .matching import vat_name
from .order_finalize_ui import _guard_factory, _snapshot, _wait
from .order_ui import Tree, one
from .payment_ui import _payment_root

PRODUCT_TABS = {'New product', '*New product'}


def _tabs(tree):
    return [n.data['name'] for n in tree.under(tree.nodes[()], 'TabItem')]


def _button(root, name):
    snapshot, wrappers = _snapshot(root)
    tree = Tree(snapshot)
    nodes = [n for n in tree.under(tree.nodes[()], 'Button', name)
             if n.data.get('visible') and tree.effective_enabled(n)]
    return wrappers[one(nodes, name.lower().replace(' ', '_')).path], _tabs(tree)


def _source_item(source, sku):
    validate_order(source)
    matches = [i for i in source.items if i.sku == sku]
    if not matches:
        raise ReviewRequired('product_sku_not_in_source')
    return matches[0]


def open_new_product_scope(root, source, sku, guard, *, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'open_invoked': False})
    _source_item(source, sku)
    guard()
    button, tabs = _button(root, 'Create a new product')
    if not any(t in {'New Order', '*New Order'} for t in tabs):
        raise ReviewRequired('product_open_source_order_tab_missing')
    if PRODUCT_TABS & set(tabs):
        raise ReviewRequired('product_open_new_product_editor_already_open')
    progress['phase'] = 'open'
    progress['open_invoked'] = True
    try:
        button.iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('product_open_invoke_uncertain') from None
    after, ok = _wait(lambda: _tabs(Tree(_snapshot(root)[0])), lambda t: bool(PRODUCT_TABS & set(t)))
    diagnostics['tabs_after'] = after
    if not ok:
        raise ReviewRequired('product_open_editor_not_observed')
    progress['phase'] = 'complete'
    return {'status': 'NEW_PRODUCT_EDITOR_OPENED', 'sku': sku, 'tabs': after}


def save_product_scope(root, source, sku, guard, *, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'save_invoked': False})
    item = _source_item(source, sku)
    guard()
    snapshot, _ = _snapshot(root)
    tree = Tree(snapshot)
    pane = one([n for n in tree.under(tree.nodes[()], 'Pane', 'New product') if n.data.get('visible')],
               'new_product_editor')
    values = {name: one([n for n in tree.under(pane, 'Edit', name) if n.data.get('visible')],
                        name.lower().replace(' ', '_')).data['patterns']['value']['CurrentValue']
              for name in ('Item Number', 'Name', 'Description')}
    vat = one(tree.under(pane, 'ComboBox', 'VAT'), 'vat').data['patterns']['value']['CurrentValue']
    diagnostics['before_save'] = {**values, 'VAT': vat}
    if (values['Item Number'] != item.sku or values['Name'] != item.description
            or values['Description'] != item.description or vat != vat_name(item.vat_percent)):
        raise ReviewRequired('product_save_fields_do_not_match_source')
    gross_text = ' '.join(n.data.get('patterns', {}).get('value', {}).get('CurrentValue', '')
                          for n in tree.under(pane, 'Edit') if n.data.get('visible'))
    expected_gross = format(product_gross(item), '.2f').replace('.', ',')
    if expected_gross not in gross_text:
        raise ReviewRequired('product_save_gross_price_not_observed')
    button, _ = _button(root, 'Save the current contents')
    guard()
    progress['phase'] = 'save'
    progress['save_invoked'] = True  # recorded before invoking; never retried
    try:
        button.iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('product_save_invoke_uncertain') from None
    after, ok = _wait(lambda: _tabs(Tree(_snapshot(root)[0])),
                      lambda t: not (PRODUCT_TABS & set(t)) and item.description in t)
    diagnostics['tabs_after'] = after
    if not ok:
        raise ReviewRequired('product_save_not_confirmed_check_dialogs')
    progress['phase'] = 'complete'
    return {'status': 'PRODUCT_SAVED', 'sku': sku, 'saved_tab': item.description,
            'gross_price_eur': format(product_gross(item), '.2f'), 'tabs': after,
            'note': 'Save invoked once; editor tab renamed to the product. Next: select it from the Order (3.12).'}


def open_new_product(pid, handle, source, sku, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return open_new_product_scope(root, source, sku, _guard_factory(root, pid, handle), diagnostics=diagnostics)


def save_product(pid, handle, source, sku, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return save_product_scope(root, source, sku, _guard_factory(root, pid, handle), diagnostics=diagnostics)


ORDER_TABS = {'New Order', '*New Order'}


def open_new_order_scope(root, guard, *, diagnostics=None):
    """Step 1.3: toolbar 'Create: New Order' once; return the proposed No. unchanged (1.4)."""
    from .order_finalize_ui import read_document
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'open_invoked': False})
    guard()
    button, tabs = _button(root, 'Create: New Order')
    if ORDER_TABS & set(tabs):
        raise ReviewRequired('order_open_new_order_already_open')
    progress['phase'] = 'open'
    progress['open_invoked'] = True
    try:
        button.iface_invoke.Invoke()
    except Exception:
        raise ReviewRequired('order_open_invoke_uncertain') from None
    after, ok = _wait(lambda: read_document(_snapshot(root)[0]),
                      lambda d: d['editor_name'] == 'New Order' and d['values'].get('number'))
    if not ok:
        raise ReviewRequired('order_open_editor_not_observed')
    progress['phase'] = 'complete'
    return {'status': 'NEW_ORDER_OPENED', 'proposed_number': after['values']['number'], 'tabs': after['tabs']}


def open_new_order(pid, handle, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return open_new_order_scope(root, _guard_factory(root, pid, handle), diagnostics=diagnostics)
