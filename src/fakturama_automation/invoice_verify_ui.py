"""Step 5.1: read-only deep check of the linked Invoice before it is saved.

Verifies the copied Order Date, both address pages (postal fields; the delivery
heading is reported separately), every item row (SKU, Qty., U.Price, VAT,
Discount, line net) from two stable grid copies, and the totals. Only address
tabs and the item-row header are clicked; nothing is edited or saved, and the
Invoice address tab is re-selected afterwards.
"""

import time

from pywinauto import mouse

from .errors import ReviewRequired
from .finance import validate_order
from .invoice_ui import verify_invoice
from .order_address_ui import verify_order_addresses
from .order_finalize_ui import _guard_factory, _snapshot, read_document
from .order_items_ui import ItemGridDriver, _grid, _rect, grid_words, ground_cells, verify_prefix
from .order_live_ui import parse_observed_date
from .order_ui import Tree, one
from .payment_ui import _payment_root
from .uia import Locator, unique_control


def read_order_date(snapshot):
    """The Edit that follows the 'Order Date' label (observed Fakturama 2.2.0 layout)."""
    tree = Tree(snapshot)
    label = one([n for n in tree.under(tree.nodes[()], 'Text', 'Order Date') if n.data.get('visible')],
                'order_date_label')
    siblings = tree.parent(label).data['children']
    index = label.path[-1] + 1
    nxt = tree.nodes[label.path[:-1] + (index,)] if index < len(siblings) else None
    edits = ([nxt] if nxt is not None and nxt.data['control_type'] == 'Edit'
             else tree.under(nxt, 'Edit') if nxt is not None else [])
    edit = one([e for e in edits if e.data.get('visible')], 'order_date_edit')
    return edit.data.get('patterns', {}).get('value', {}).get('CurrentValue', '')


def verify_invoice_deep(pid, handle, source, executable, *, diagnostics=None):
    validate_order(source)
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'save_invoked': False})
    root = _payment_root(pid, handle)
    root_guard = _guard_factory(root, pid, handle)
    root_guard()
    doc = read_document(_snapshot(root)[0])
    name = doc['editor_name']
    if name != 'New Invoice' and not name.startswith('INV'):
        raise ReviewRequired('invoice_verify_invoice_editor_not_selected')
    issues = []
    values = doc['values']
    if values.get('reference') != source.external_reference:
        issues.append('reference_not_copied')
    try:
        if parse_observed_date(read_order_date(_snapshot(root)[0])) != source.order_date:
            issues.append('order_date_not_copied')
    except ReviewRequired:
        issues.append('order_date_unreadable')
    report = {'editor_name': name}
    progress['phase'] = 'addresses'
    try:
        report.update(verify_order_addresses(root, source, root_guard, diagnostics=diagnostics, editor_name=name))
    except ReviewRequired as exc:
        issues.append(exc.code)

    progress['phase'] = 'items'
    grid = _grid(root, name)
    identity = grid.handle, _rect(grid)

    def guard():
        root_guard()
        current = _grid(root, name)
        if (current.handle, _rect(current)) != identity:
            raise ReviewRequired('invoice_verify_grid_moved_or_replaced')

    guard()
    reference = unique_control(root, Locator('Edit', name='Cust.Ref.')).rectangle()
    mouse.move(coords=((reference.left + reference.right) // 2, (reference.top + reference.bottom) // 2))
    time.sleep(.5)
    skus = [item.sku for item in source.items]
    image = grid.capture_as_image()
    r = identity[1]
    if image.size != (r[2] - r[0], r[3] - r[1]):
        raise ReviewRequired('invoice_verify_capture_scale_mismatch')
    words = grid_words(image, executable, skus)
    cells = ground_cells(words, skus[-1], skus)
    rows = ItemGridDriver(root, grid, cells, guard).read()
    diagnostics['item_rows'] = [{k: str(v) for k, v in row.items()} for row in rows]
    try:
        verify_prefix(rows, source, len(source.items), complete=True)
    except ReviewRequired as exc:
        issues.append(exc.code)

    totals = verify_invoice({'doc': doc, 'payment': source.payment.method, 'paid': source.payment.status == 'PAID',
                             'paid_date_text': None, 'value_text': None}, source)
    issues += [i for i in totals if i.startswith(('subtotal', 'vat_total', 'total', 'vat_mode'))]
    progress['phase'] = 'complete'
    diagnostics['issues'] = issues
    if issues:
        raise ReviewRequired('invoice_verify_mismatch')
    return {'status': 'INVOICE_COPY_VERIFIED', **report, 'order_date_verified': True,
            'items_verified': len(rows), 'totals_verified': True,
            'note': 'Read-only; addresses, Order Date, every item and totals match the source.'}
