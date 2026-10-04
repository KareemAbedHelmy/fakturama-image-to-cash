"""Steps 2.1-2.3 and 3.2-3.3: the Order's Debtor/Product selectors as existence checks.

Flow: click the UPPER icon beside 'Addresses' / 'Items' of the open New Order
(position from the live UIA element bounds, never stored coordinates), type the
literal query into the dialog's Search field, focus the result grid and copy
its rows with Select All / Copy (the grid exposes no UIA rows; the copy is
read only if the clipboard is freshly owned by Fakturama), twice for stability.

Decision: exactly one row and it is an exact match -> OK (selected);
no exact row -> Cancel (caller may create the master);
several exact rows or a lone non-exact row set we cannot disambiguate -> Cancel and review.
Absence is inferred from an empty copy; the guided runner asks the operator to
confirm before creating anything.
"""

import time
from pathlib import Path

from .errors import ReviewRequired
from .order_ui import Tree, one
from .order_finalize_ui import _snapshot

DIALOGS = {'debtor': ('Addresses', 'Select the address'), 'product': ('Items', 'Select a product')}


def grid_has_rows(image, *, dark=110, footer=60):
    """True if dark text appears below the header band of a captured result grid.

    Fakturama throws an NPE ('copiedCells is null') if Copy is sent to an EMPTY
    grid, so emptiness must be established from pixels before any Select All/Copy.
    The header is the first contiguous band of dark rows from the top; the
    horizontal scrollbar at the bottom is excluded.
    """
    gray = image.convert('L')
    width, height = gray.size
    pixels = gray.load()
    dark_rows = [any(pixels[x, y] < dark for x in range(width)) for y in range(max(0, height - footer))]
    y = 0
    while y < len(dark_rows) and not dark_rows[y]:
        y += 1  # blank margin above the header text
    gap = 0
    while y < len(dark_rows) and gap <= 3:  # header text band; tolerate thin gaps inside it
        gap = 0 if dark_rows[y] else gap + 1
        y += 1
    return any(dark_rows[y:])


def parse_rows(text):
    rows = [line.split('\t') for line in text.replace('\r\n', '\n').split('\n') if line.strip()]
    return [[cell.strip() for cell in row] for row in rows]


def debtor_matcher(source):
    d, b = source.debtor, source.debtor.billing
    wanted = [d.first_name, d.last_name, d.company, b.zip, b.city]
    return lambda cells: len(cells) >= 6 and cells[1:6] == wanted


def product_matcher(sku):
    return lambda cells: sku in cells


def decide(rows, matcher):
    exact = [r for r in rows if matcher(r)]
    if not rows or not exact:
        return 'NOT_FOUND'
    if len(exact) == 1 and len(rows) == 1:
        return 'SELECT'
    return 'AMBIGUOUS'


def ground_selector_row(words, row):
    identifiers = [w for w in words if w.text == row[0] and w.confidence >= 70]
    if len(identifiers) == 1:
        return identifiers[0]
    # The focused No. cell can render white on pale blue. In that case require
    # two different exact copied cell values on one shared text baseline.
    candidates = [w for w in words if w.text in row[1:6] and w.confidence >= 70]
    bands = {}
    for word in candidates:
        band = [w for w in candidates if max(w.top, word.top) <
                min(w.top + w.height, word.top + word.height)]
        if len({w.text for w in band}) >= 2:
            key = tuple(sorted((w.text, w.left, w.top) for w in band))
            bands[key] = band
    if len(bands) != 1:
        raise ReviewRequired('selector_exact_row_not_visually_grounded')
    return min(next(iter(bands.values())), key=lambda w: w.left)


def _windows(root, title):
    return [w for w in root.descendants(control_type='Window')
            if w.element_info.name == title and w.is_visible()]


def _opener(root, label_name):
    snapshot, wrappers = _snapshot(root)
    tree = Tree(snapshot)
    editor = one([n for n in tree.under(tree.nodes[()], 'Pane', 'New Order') if n.data.get('visible')],
                 'new_order_editor')
    label = one([n for n in tree.under(editor, 'Text', label_name) if n.data.get('visible')], 'selector_label')
    images = [c for c in tree.under(tree.parent(label), 'Image') if c.data.get('visible')]
    if not images:
        raise ReviewRequired('selector_icon_missing')
    def top(node):
        r = wrappers[node.path].rectangle()
        return (r.top, r.left)
    ordered = sorted(images, key=top)
    if len(ordered) > 1 and top(ordered[0])[0] == top(ordered[1])[0]:
        raise ReviewRequired('selector_icon_order_ambiguous')
    return wrappers[ordered[0].path]


class SelectorDriver:
    """Native effects; injectable for tests."""

    def __init__(self):
        from pywinauto.keyboard import send_keys
        from .windows_clipboard import WindowsClipboard
        self.clipboard = WindowsClipboard()
        self._send = send_keys

    def click(self, control):
        control.click_input()

    def keys(self, keys):
        self._send(keys, pause=0.05, vk_packet=False, turn_off_numlock=False)

    def focus(self, control):
        control.click_input()
        time.sleep(0.3)

    def copy_rows(self, dialog, grid, pid, timeout=3.0, *, select_all=True):
        """Select All + Copy; returns the fresh text, or None if nothing fresh was copied."""
        self.clipboard.assert_focus(dialog.handle, grid.handle, pid)
        baseline = self.clipboard.sequence()
        if select_all:
            self.keys('^a')
        self.keys('^c')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return self.clipboard.read_new(pid, dialog.handle, baseline).text
            except ReviewRequired as exc:
                if exc.code not in {'clipboard_busy', 'clipboard_unchanged', 'clipboard_owner_unavailable',
                                    'clipboard_owner_not_fakturama', 'clipboard_data_unavailable',
                                    'clipboard_unicode_unavailable'}:
                    raise
            time.sleep(0.1)
        return None

    def select_single_row(self, dialog, grid, pid, row):
        from .order_items_ui import grid_words
        self.clipboard.assert_focus(dialog.handle, grid.handle, pid)
        # Ctrl+A/Copy enumerates data but does not fire SWT's contact-row
        # selection event. Ground the exact copied identifier in this frame
        # and explicitly click it before OK.
        rect = grid.rectangle()
        image = grid.capture_as_image()
        if image.size != (rect.width(), rect.height()):
            raise ReviewRequired('selector_row_capture_scale_mismatch')
        words = grid_words(image, Path('C:/Program Files/Tesseract-OCR/tesseract.exe'), row[:6])
        word = ground_selector_row(words, row)
        if grid.rectangle() != rect:
            raise ReviewRequired('selector_grid_moved_before_selection')
        self.clipboard.assert_focus(dialog.handle, grid.handle, pid)
        grid.click_input(coords=(word.left + word.width // 2, word.top + word.height // 2))
        time.sleep(.2)
        copied = self.copy_rows(dialog, grid, pid, select_all=False)
        if parse_rows(copied or '') != [row]:
            raise ReviewRequired('selector_single_row_selection_unverified')


def check_selector_scope(root, kind, query, matcher, guard, driver, *, diagnostics=None, settle=1.5,
                         existing_dialog=False):
    if kind not in DIALOGS:
        raise ReviewRequired('selector_kind_invalid')
    if not isinstance(query, str) or not query.strip() or any(ord(c) < 32 for c in query):
        raise ReviewRequired('selector_query_invalid')
    label_name, title = DIALOGS[kind]
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'opened': False,
                                                   'ok_invoked': False, 'cancel_invoked': False})
    pid = root.element_info.process_id
    guard()
    if _windows(root, title) and not existing_dialog:
        raise ReviewRequired('selector_dialog_already_open')
    if not existing_dialog:
        icon = _opener(root, label_name)
        progress['phase'] = 'open'
        progress['opened'] = True
        driver.click(icon)
    deadline = time.monotonic() + 8
    while not _windows(root, title):
        if time.monotonic() > deadline:
            raise ReviewRequired('selector_dialog_not_observed')
        time.sleep(0.2)
    time.sleep(1.0)  # let the dialog finish loading before UIA calls
    found = _windows(root, title)
    dialog = found[0] if len(found) == 1 else None
    if dialog is None or dialog.element_info.process_id != pid:
        raise ReviewRequired('selector_dialog_ambiguous')

    def control(kind_, name=None):
        found = [c for c in dialog.descendants(control_type=kind_)
                 if (name is None or c.element_info.name == name) and c.is_visible()]
        return found

    try:
        label = [c for c in control('Text', 'Search:')][0]
        search = [c for c in label.parent().descendants(control_type='Edit') if c.is_visible()]
        if len(search) != 1:
            raise ReviewRequired('selector_search_missing')
        search = search[0]
        bar_bottom = label.rectangle().bottom  # read before typing: the filter bar is rebuilt by the filter
        progress['phase'] = 'search'
        # The product dialog is still loading its catalogue just after it appears and
        # UIA raises COMError until it is idle: retry the same literal set, bounded.
        retry_until = time.monotonic() + 15
        while True:
            try:
                search.iface_value.SetValue(query)  # literal text, never keyboard syntax
                current = search.iface_value.CurrentValue
                break
            except Exception:
                if not _windows(root, title):
                    current = None  # dialog closed while typing: unique match auto-added
                    break
                if time.monotonic() > retry_until:
                    raise
                time.sleep(0.5)
        if current is not None and current != query:
            raise ReviewRequired('selector_query_readback_mismatch')
        time.sleep(settle)
        if not _windows(root, title):
            # Fakturama closes the product dialog by itself and adds the line when the
            # filter leaves exactly one product. The caller must verify the added line.
            progress['phase'] = 'complete'
            diagnostics.update(query=query, decision='AUTO_ADDED')
            return {'status': 'SELECTED_EXISTING', 'kind': kind, 'query': query, 'rows': [],
                    'note': 'Dialog closed itself after the search (single match, line added). '
                            'Verify the new line (SKU, U.Price) and the totals before Save.'}
        # The result grid: the SWT pane below the filter bar whose only children are scrollbars.
        # Filtering rebuilds the grid, so UIA may raise COMError meanwhile: retry, bounded.
        retry_until = time.monotonic() + 15
        while True:
            try:
                grids = [p for p in control('Pane') if p.element_info.class_name == 'SWT_Window0'
                         and all(c.element_info.control_type == 'ScrollBar' for c in p.children())
                         and p.rectangle().top >= bar_bottom and p.rectangle().width() > 200]
                break
            except ReviewRequired:
                raise
            except Exception:
                if time.monotonic() > retry_until:
                    raise
                time.sleep(0.5)
        if len(grids) != 1:
            raise ReviewRequired('selector_grid_missing_or_ambiguous')
        grid = grids[0]
        progress['phase'] = 'copy'
        if grid_has_rows(grid.capture_as_image()) or grid_has_rows(grid.capture_as_image()):
            driver.focus(grid)
            copies = [driver.copy_rows(dialog, grid, pid), None]
            time.sleep(0.3)
            driver.focus(grid)
            copies[1] = driver.copy_rows(dialog, grid, pid)
            if copies[0] != copies[1]:
                raise ReviewRequired('selector_rows_unstable')
        else:  # empty result: never send Copy (Fakturama NPE); absence is the answer
            copies = [None, None]
        rows = parse_rows(copies[0] or '')
        decision = decide(rows, matcher)
        diagnostics.update(query=query, rows=rows, decision=decision,
                           copy_was_empty=copies[0] is None)
        button = 'OK' if decision == 'SELECT' else 'Cancel'
        if decision == 'SELECT':
            driver.select_single_row(dialog, grid, pid, rows[0])
        buttons = control('Button', button)
        if len(buttons) != 1:
            raise ReviewRequired('selector_button_missing')
        guard_dialog = _windows(root, title)
        if len(guard_dialog) != 1:
            raise ReviewRequired('selector_dialog_changed')
        progress['phase'] = 'close_' + button.lower()
        progress['ok_invoked' if button == 'OK' else 'cancel_invoked'] = True
        try:
            buttons[0].iface_invoke.Invoke()
        except Exception:
            time.sleep(0.5)
            if _windows(root, title):
                raise
    except ReviewRequired:
        raise
    except Exception as exc:
        import traceback
        diagnostics['exception'] = f'{type(exc).__name__}: {exc}'[:200]
        diagnostics['where'] = [f'{f.lineno}:{f.name}' for f in traceback.extract_tb(exc.__traceback__)][-3:]
        try:
            diagnostics['dialog_controls'] = [
                [c.element_info.control_type, c.element_info.name, c.element_info.class_name,
                 str(c.rectangle()), len(c.children())] for c in dialog.descendants() if c.is_visible()]
        except Exception:
            pass
        raise ReviewRequired('selector_interaction_uncertain') from None
    deadline = time.monotonic() + 8
    while _windows(root, title):
        if time.monotonic() > deadline:
            raise ReviewRequired('selector_dialog_did_not_close')
        time.sleep(0.2)
    progress['phase'] = 'complete'
    if decision == 'AMBIGUOUS':
        raise ReviewRequired('selector_ambiguous_or_conflicting_rows')
    return {'status': 'SELECTED_EXISTING' if decision == 'SELECT' else 'NOT_FOUND',
            'kind': kind, 'query': query, 'rows': rows,
            'note': 'Exactly one exact row selected with OK.' if decision == 'SELECT'
            else 'No row copied for the exact query; dialog cancelled. Confirm before creating the master.'}


def check_selector(pid, handle, source, kind, query, *, diagnostics=None):
    from .payment_ui import _payment_root
    root = _payment_root(pid, handle)
    matcher = debtor_matcher(source) if kind == 'debtor' else product_matcher(query)
    if kind == 'product' and query not in {i.sku for i in source.items}:
        raise ReviewRequired('selector_sku_not_in_source')
    title = root.element_info.name

    def guard():
        # The modal dialog takes the foreground, so only the main identity is pinned here.
        if root.element_info.name != title or root.element_info.process_id != pid:
            raise ReviewRequired('selector_main_window_changed')

    if kind == 'debtor':
        from .order_address_ui import select_address_tab, verify_order_addresses
        from .order_finalize_ui import _guard_factory
        address_guard = _guard_factory(root, pid, handle)
        select_address_tab(root, 'Invoice address', address_guard)
    report = check_selector_scope(root, kind, query, matcher, guard, SelectorDriver(), diagnostics=diagnostics)
    if kind == 'debtor' and report['status'] == 'SELECTED_EXISTING':
        report.update(verify_order_addresses(root, source, address_guard, diagnostics=diagnostics))
    return report
