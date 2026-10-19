"""Verify saved Order and Invoice rows in Fakturama's Documents pane."""

import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation

from .errors import ReviewRequired
from .finance import validate_order
from .order_finalize_ui import _guard_factory
from .order_live_ui import MONTHS, parse_observed_date
from .payment_ui import _payment_root
from .selector_ui import exactly_one
from .uia import Locator, unique_control


def parse_document_rows(text):
    rows = []
    for line in text.replace('\r\n', '\n').split('\n'):
        if not line.strip():
            continue
        cells = line.split('\t')
        if len(cells) != 8 or cells[0] not in {'ICON_ORDER', 'ICON_INVOICE'}:
            raise ReviewRequired('documents_row_schema_changed')
        try:
            total = Decimal(cells[6])
        except InvalidOperation:
            raise ReviewRequired('documents_total_unreadable') from None
        if not total.is_finite():
            raise ReviewRequired('documents_total_unreadable')
        date_match = re.fullmatch(r'[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) 00:00:00 [A-Za-z]{2,6} (\d{4})', cells[2])
        if not date_match or date_match[1] not in MONTHS:
            raise ReviewRequired('documents_date_format_unreadable')
        try:
            row_date = date(int(date_match[3]), MONTHS.index(date_match[1]) + 1, int(date_match[2]))
        except ValueError:
            raise ReviewRequired('documents_date_unreadable') from None
        rows.append({'kind': cells[0], 'number': cells[1], 'date': row_date,
                     'debtor': cells[3], 'reference': cells[4], 'state': cells[5],
                     'total': total, 'printed': cells[7]})
    return rows


def verify_document_row(rows, source, number, kind, expected_date):
    matches = [r for r in rows if r['number'] == number]
    if len(matches) != 1:
        raise ReviewRequired('documents_number_missing_or_ambiguous')
    row = matches[0]
    expected_state = ('COMMAND_ORDER_PENDING' if kind == 'order' else
                      'COMMAND_CHECKED' if source.payment.status == 'PAID' else 'COMMAND_ERROR')
    if (row['kind'] != ('ICON_ORDER' if kind == 'order' else 'ICON_INVOICE')
            or row['date'] != expected_date or row['reference'] != source.external_reference
            or row['state'] != expected_state or row['total'] != source.totals.gross
            or source.debtor.company not in row['debtor']):
        raise ReviewRequired('documents_saved_row_mismatch')
    return {**row, 'date': row['date'].isoformat(), 'total': str(row['total'])}


def _menu_entry(root, caption, guard, timeout=5):
    """The opened menu's item; polled because the popup appears after the click, and
    searched in the main window and in the process's other top-level windows."""
    from pywinauto import Application
    deadline = time.monotonic() + timeout
    while True:
        guard()
        scopes = [root]
        try:
            scopes += Application(backend='uia').connect(process=root.element_info.process_id).windows()
        except Exception:
            pass
        unique = {}
        for scope in scopes:
            for c in scope.descendants(control_type='MenuItem'):
                if c.element_info.name == caption and c.is_visible():
                    unique[tuple(c.element_info.runtime_id)] = c  # same item can appear in two scopes
        found = list(unique.values())
        if len(found) == 1:
            return found[0]
        if time.monotonic() > deadline:
            raise ReviewRequired('documents_menu_item_missing_or_ambiguous')
        time.sleep(.2)


def _pane(root, guard):
    guard()
    found = [p for p in root.descendants(control_type='Pane')
             if p.element_info.name == 'Documents' and p.is_visible()]
    if not found:
        menu = unique_control(root, Locator('MenuItem', name='Data'))
        if not menu.is_visible():
            raise ReviewRequired('documents_data_menu_unavailable')
        menu.click_input()
        guard()
        entry = _menu_entry(root, 'Documents', guard)
        entry.click_input()
        deadline = time.monotonic() + 5
        while not found and time.monotonic() < deadline:
            guard()
            found = [p for p in root.descendants(control_type='Pane')
                     if p.element_info.name == 'Documents' and p.is_visible()]
            if not found:
                time.sleep(.1)
    return exactly_one(found, 'pane', 'documents')


def _grid(pane):
    label = unique_control(pane, Locator('Text', name='Search:'))
    search = exactly_one([c for c in pane.descendants(control_type='Edit')
                          if c.is_visible()], 'search', 'documents')
    if not label.is_visible() or search.iface_value.CurrentValue != '':
        raise ReviewRequired('documents_search_filter_present')
    bounds = search.rectangle()
    grids = [c for c in pane.descendants(control_type='Pane')
             if c.element_info.name == '' and c.is_visible()
             and c.rectangle().top > bounds.bottom and c.rectangle().left < bounds.left
             and c.rectangle().width() > 2 * bounds.width()]
    return exactly_one(grids, 'grid', 'documents')


def _rows(root, pane, kind, guard, driver):
    from pywinauto.keyboard import send_keys
    target = 'Orders' if kind == 'order' else 'Invoices'
    guard()
    item = exactly_one([c for c in pane.descendants(control_type='TreeItem')
                        if c.element_info.name == target and c.is_visible()],
                       'tree_item', 'documents')
    item.click_input()
    deadline = time.monotonic() + 5
    while not item.iface_selection_item.CurrentIsSelected:
        guard()
        if time.monotonic() >= deadline:
            raise ReviewRequired('documents_tree_selection_unconfirmed')
        time.sleep(.1)
    first = None
    for _ in range(2):
        guard()
        grid = _grid(pane)
        grid.click_input()
        driver.assert_focus(root.handle, grid.handle, root.element_info.process_id)
        before = driver.sequence()
        send_keys('^a', pause=.05, vk_packet=False)
        send_keys('^c', pause=.05, vk_packet=False)
        deadline = time.monotonic() + 3
        copied = None
        while copied is None and time.monotonic() < deadline:
            try:
                copied = driver.read_new(root.element_info.process_id, root.handle, before).text
            except ReviewRequired as exc:
                if exc.code not in {'clipboard_busy', 'clipboard_unchanged', 'clipboard_owner_unavailable',
                                    'clipboard_owner_not_fakturama', 'clipboard_data_unavailable',
                                    'clipboard_unicode_unavailable'}:
                    raise
                time.sleep(.1)
        if copied is None:
            raise ReviewRequired('documents_copy_unavailable')
        rows = parse_document_rows(copied)
        if not rows or any(r['kind'] != ('ICON_ORDER' if kind == 'order' else 'ICON_INVOICE') for r in rows):
            raise ReviewRequired('documents_rows_wrong_kind_or_empty')
        if first is not None and rows != first:
            raise ReviewRequired('documents_rows_changed_during_read')
        first = rows
    return first


def verify_documents_scope(root, source, order_number, guard, *, invoice_number=None,
                           invoice_date=None, diagnostics=None, driver=None):
    from .windows_clipboard import WindowsClipboard
    validate_order(source)
    if bool(invoice_number) != bool(invoice_date):
        raise ReviewRequired('documents_invoice_scope_incomplete')
    expected_invoice_date = parse_observed_date(invoice_date) if invoice_date else None
    diagnostics = diagnostics if diagnostics is not None else {}
    pane = _pane(root, guard)
    clipboard = driver or WindowsClipboard()
    orders = _rows(root, pane, 'order', guard, clipboard)
    verified = {'order': verify_document_row(orders, source, order_number, 'order', source.order_date)}
    diagnostics['order_row_count'] = len(orders)
    if invoice_number:
        invoices = _rows(root, pane, 'invoice', guard, clipboard)
        verified['invoice'] = verify_document_row(invoices, source, invoice_number, 'invoice',
                                                  expected_invoice_date)
        diagnostics['invoice_row_count'] = len(invoices)
    return {'status': 'DOCUMENTS_ROWS_VERIFIED', 'rows': verified,
            'source': 'DATA_DOCUMENTS_NATIVE_GRID_COPY', 'stable_copies_per_kind': 2}


def verify_documents(pid, handle, source, order_number, *, invoice_number=None,
                     invoice_date=None, diagnostics=None):
    root = _payment_root(pid, handle)
    return verify_documents_scope(root, source, order_number, _guard_factory(root, pid, handle),
                                  invoice_number=invoice_number, invoice_date=invoice_date,
                                  diagnostics=diagnostics)
