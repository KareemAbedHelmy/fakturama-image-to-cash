"""Complete selected Order lines from extraction; no Product edits or Save.

UIA locates the custom grid. Fresh OCR grounds its headers and SKU row; the
temporary native Edit supplies numeric entry. Fresh Fakturama-owned clipboard
copies verify committed cells, including values clipped off screen.
"""

import io
import re
import time
from dataclasses import replace
from decimal import Decimal
from PIL import ImageOps

from .errors import ReviewRequired
from .finance import validate_order
from .order_finalize_ui import _guard_factory, _snapshot, read_document
from .payment_ui import _payment_root
from .selector_live_ui import SelectorDriver, parse_rows
from .selector_ui import exactly_one
from .selector_vision import ocr_words
from .uia import Locator, unique_control


def _one(values, role):
    return exactly_one(values, role, 'order_line')


def copied_lines(text):
    """Observed SWT copy schema excludes Pos.; money uses canonical decimals."""
    rows = parse_rows(text or '')
    result = []
    for row in rows:
        if len(row) != 9:
            raise ReviewRequired('order_line_copy_schema_changed')
        def number(value):
            if not re.fullmatch(r'-?\d+(?:\.\d+)?', value):
                raise ReviewRequired('order_line_copy_number_unreadable')
            return Decimal(value)
        def money(value):
            if not value.startswith('EUR '):
                raise ReviewRequired('order_line_copy_currency_mismatch')
            return number(value[4:])
        vat = re.search(r'^VAT taxValue: \[([0-9.]+)\]', row[5])
        if not vat:
            raise ReviewRequired('order_line_copy_vat_unreadable')
        result.append(dict(sku=row[1], quantity=number(row[0]), unit_net=money(row[6]),
                           vat_percent=number(vat[1]) * 100,
                           discount_percent=-number(row[7]) * 100,
                           source_line_net=money(row[8])))
    return result


def verify_prefix(rows, source, line_index, *, complete):
    if len(rows) != line_index or [r['sku'] for r in rows] != [i.sku for i in source.items[:line_index]]:
        raise ReviewRequired('order_line_count_or_source_order_mismatch')
    for index, (row, item) in enumerate(zip(rows, source.items)):
        if index == line_index - 1 and not complete:
            if row['vat_percent'] != item.vat_percent:
                raise ReviewRequired('order_line_vat_mismatch')
            continue
        if any(row[key] != getattr(item, key) for key in row):
            raise ReviewRequired('order_line_committed_values_mismatch')


def ground_cells(words, sku, expected_skus=None):
    """Labels and exact SKU from this frame, never stored coordinates."""
    pos = _one([w for w in words if w.text == 'Pos.' and w.confidence >= 70], 'position_header')
    headers = {}
    for label in ('Qty.', 'Item', 'Picture', 'U.Price', 'Discount', 'Price'):
        headers[label] = _one([w for w in words if w.text == label and w.confidence >= 60
                              and max(w.top, pos.top) < min(w.top + w.height, pos.top + pos.height)],
                             'header_' + label)
    ordered = [pos, *headers.values()]
    if any(a.left + a.width >= b.left for a, b in zip(ordered, ordered[1:])):
        raise ReviewRequired('order_line_header_order_changed')
    if expected_skus is not None:
        visible = sorted([w for w in words if w.text in expected_skus and w.confidence >= 70
                          and w.top > pos.top + pos.height
                          and headers['Item'].left - headers['Item'].height <= w.left < headers['Picture'].left],
                         key=lambda w: w.top)
        if [w.text for w in visible] != list(expected_skus):
            raise ReviewRequired('order_line_source_rows_not_fully_visible')
    matches = [w for w in words if w.text == sku and w.confidence >= 70
               and w.top > pos.top + pos.height
               and headers['Item'].left - headers['Item'].height <= w.left < headers['Picture'].left]
    if not matches:
        raise ReviewRequired('order_line_sku_not_visible')
    # Each runner iteration appends a line. Prefix verification separately
    # proves the row count/order, including repeated source SKUs.
    row = max(matches, key=lambda w: w.top)
    if sum(w.top == row.top for w in matches) != 1:
        raise ReviewRequired('order_line_sku_row_ambiguous')
    y = row.top + row.height // 2
    return {role: (word.left + word.width // 2, y) for role, word in
            [('position', pos), ('quantity', headers['Qty.']),
             ('unit_net', headers['U.Price']), ('discount_percent', headers['Discount'])]}


def _grid(root, editor_name='New Order'):
    editor = unique_control(root, Locator('Pane', name=editor_name))
    label = unique_control(editor, Locator('Text', name='Items'))
    grids = [c for c in label.parent().parent().descendants(control_type='Pane')
             if c.is_visible() and c.is_enabled() and c.handle
             and c.element_info.class_name == 'SWT_Window0'
             and c.rectangle().top >= label.rectangle().top
             and c.rectangle().left > label.rectangle().right
             and any(k.element_info.control_type == 'ScrollBar' for k in c.children())
             and all(k.element_info.control_type in {'ScrollBar', 'Edit'} for k in c.children())]
    return _one(grids, 'grid')


def _rect(control):
    r = control.rectangle()
    return (r.left, r.top, r.right, r.bottom)


def grid_words(image, executable, skus):
    def read(frame):
        output = io.BytesIO()
        frame.save(output, format='PNG')
        return ocr_words(output.getvalue(), executable)[0]
    words = read(image)
    # SWT selection uses white text on blue/grey. Tesseract's page threshold
    # can erase those glyphs. Normalize each scanline by its median background
    # and corroborate exact source SKUs and the required header labels.
    grey = ImageOps.grayscale(image)
    for y in range(grey.height):
        row = grey.crop((0, y, grey.width, y + 1))
        histogram = row.histogram()
        count = 0
        background = 255
        for level, amount in enumerate(histogram):
            count += amount
            if count > grey.width // 2:
                background = level
                break
        lut = [0 if value > 230 else 255 for value in range(256)] if 60 < background < 230 else [
            0 if value < 100 else 255 for value in range(256)]
        grey.paste(row.point(lut), (0, y))
    for candidate in read(grey):
        threshold = 70 if candidate.text in skus else 60
        if (candidate.text not in {*skus, 'Qty.', 'Item', 'Picture', 'U.Price', 'Discount', 'Price'}
                or candidate.confidence < threshold):
            continue
        same = [w for w in words if w.text == candidate.text and
                max(w.top, candidate.top) < min(w.top + w.height, candidate.top + candidate.height)]
        if same and max(w.confidence for w in same) >= candidate.confidence:
            continue
        words = [w for w in words if w not in same] + [candidate]
    positions = [w for w in words if w.text == 'Pos.' and w.confidence >= 70]
    if len(positions) == 1:
        pos = positions[0]
        items = [w for w in words if w.text == 'Item' and abs(w.top - pos.top) < pos.height]
        quantities = [w for w in words if w.text == 'Qty.' and w.confidence >= 60
                      and abs(w.top - pos.top) < pos.height]
        if len(items) == 1 and not quantities:
            left = pos.left + pos.width + pos.height
            right = items[0].left - pos.height
            top = max(0, pos.top - pos.height // 2)
            bottom = min(image.height, pos.top + pos.height + pos.height // 4)
            if right - left >= 32 and bottom - top >= 32:
                crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop((left, top, right, bottom))))
                if crop.histogram().index(max(crop.histogram())) < 128:
                    crop = ImageOps.invert(crop)
                words.extend(replace(w, left=w.left + left, top=w.top + top)
                             for w in read(crop) if w.text == 'Qty.' and w.confidence >= 60)
    return words


class ItemGridDriver(SelectorDriver):
    def __init__(self, root, grid, cells, guard):
        super().__init__()
        self.root, self.grid, self.cells, self.guard = root, grid, cells, guard

    def click_cell(self, role):
        self.guard()
        self.grid.click_input(coords=self.cells[role])
        time.sleep(.2)
        self.guard()

    def read(self):
        self.click_cell('position')  # Row header selects without opening an editor.
        copies = []
        for _ in range(2):
            self.guard()
            copies.append(self.copy_rows(self.root, self.grid, self.root.element_info.process_id))
            self.guard()
        if copies[0] is None or copies[0] != copies[1]:
            raise ReviewRequired('order_line_copy_missing_or_unstable')
        return copied_lines(copies[0])

    def write(self, role, value):
        self.click_cell(role)
        x, y = self.cells[role]
        rect = self.grid.rectangle()
        x, y = x + rect.left, y + rect.top
        fields = [c for c in self.grid.descendants(control_type='Edit') if c.is_visible()
                  and c.rectangle().left <= x < c.rectangle().right
                  and c.rectangle().top <= y < c.rectangle().bottom]
        field = _one(fields, 'active_numeric_editor')
        if (field.element_info.class_name != 'Edit' or not field.handle
                or field.element_info.process_id != self.root.element_info.process_id
                or field.iface_value.CurrentIsReadOnly):
            raise ReviewRequired('order_line_numeric_editor_identity_mismatch')
        # Fakturama stores reductions as negative percentages; source discount
        # is positive. Quantity/percent editors use dots, price uses commas.
        target = -value if role == 'discount_percent' else value
        text = format(target, 'f')
        if role == 'unit_net':
            text = text.replace('.', ',')
        if not re.fullmatch(r'-?\d+(?:[.,]\d+)?', text):
            raise ReviewRequired('order_line_numeric_input_invalid')
        def focus():
            self.guard()
            self.clipboard.assert_focus(self.root.handle, field.handle,
                                        self.root.element_info.process_id, allow_caret_blink=True)
        focus()
        self.keys('^a')
        focus()
        self.keys(text)
        focus()
        actual = field.iface_value.CurrentValue.strip().replace('%', '').replace('EUR', '').strip()
        if not re.fullmatch(r'-?\d+(?:[.,]\d+)?', actual) or Decimal(actual.replace(',', '.')) != target:
            raise ReviewRequired('order_line_numeric_edit_readback_mismatch')
        self.keys('{ENTER}')
        # SWT can retain/reopen its numeric editor after Enter. Leaving the
        # cell for the row header completes focus-loss validation as well.
        self.click_cell('position')
        deadline = time.monotonic() + 8
        while True:
            time.sleep(.2)
            self.guard()
            if not any(c.is_visible() for c in self.grid.descendants(control_type='Edit')):
                break
            if time.monotonic() >= deadline:
                raise ReviewRequired('order_line_edit_not_committed')


def complete_line(source, line_index, driver, diagnostics):
    """Shared tested orchestration; only mismatching transaction fields change."""
    validate_order(source)
    if not 1 <= line_index <= len(source.items):
        raise ReviewRequired('order_line_index_out_of_range')
    item = source.items[line_index - 1]
    progress = diagnostics.setdefault('progress', {'phase': 'read', 'writes_attempted': []})
    rows = driver.read()
    verify_prefix(rows, source, line_index, complete=False)
    for role in ('quantity', 'unit_net', 'discount_percent'):
        value = getattr(item, role)
        if rows[-1][role] == value:
            continue
        progress['phase'] = 'write_' + role
        progress['writes_attempted'].append(role)
        before = rows
        driver.write(role, value)
        rows = driver.read()
        verify_prefix(rows, source, line_index, complete=False)
        if rows[:-1] != before[:-1] or rows[-1][role] != value:
            raise ReviewRequired('order_line_write_not_committed_or_other_row_changed')
        for other in ('quantity', 'unit_net', 'discount_percent', 'vat_percent'):
            if other != role and rows[-1][other] != before[-1][other]:
                raise ReviewRequired('order_line_unrelated_cell_changed')
    verify_prefix(rows, source, line_index, complete=True)
    progress['phase'] = 'complete'
    return {'status': 'ORDER_LINE_PREPARED_UNSAVED', 'line_index': line_index,
            'values': {k: str(v) for k, v in rows[-1].items()},
            'committed_line_verified': True, 'persistence_verified': False}


def prepare_order_line(pid, handle, source, expected_number, line_index, executable, *, diagnostics=None):
    validate_order(source)
    if not 1 <= line_index <= len(source.items):
        raise ReviewRequired('order_line_index_out_of_range')
    diagnostics = diagnostics if diagnostics is not None else {}
    root = _payment_root(pid, handle)
    root_guard = _guard_factory(root, pid, handle)
    root_guard()
    grid = _grid(root)
    identity = grid.handle, _rect(grid)
    def guard():
        root_guard()
        current = _grid(root)
        if (current.handle, _rect(current)) != identity:
            raise ReviewRequired('order_line_grid_moved_or_replaced')
        doc = read_document(_snapshot(root)[0])
        v = doc['values']
        if (doc['editor_name'] != 'New Order' or v['number'] != expected_number
                or v['reference'] != source.external_reference
                or v['price_mode'] != 'Net' or v['vat_mode'] != 'With VAT'):
            raise ReviewRequired('order_line_order_header_mismatch')
    guard()
    # The selector icon's hover tooltip can cover the newly added SKU row.
    # Move to a live header field before reading pixels, without clicking it.
    from pywinauto import mouse
    reference = unique_control(root, Locator('Edit', name='Cust.Ref.')).rectangle()
    mouse.move(coords=((reference.left + reference.right) // 2,
                       (reference.top + reference.bottom) // 2))
    time.sleep(.5)
    guard()
    image = grid.capture_as_image()
    guard()
    r = identity[1]
    if image.size != (r[2] - r[0], r[3] - r[1]):
        raise ReviewRequired('order_line_capture_scale_mismatch')
    words = grid_words(image, executable, [item.sku for item in source.items[:line_index]])
    diagnostics['grid_ocr'] = [{'text': w.text, 'left': w.left, 'top': w.top,
                                'confidence': w.confidence} for w in words]
    cells = ground_cells(words, source.items[line_index - 1].sku,
                         [item.sku for item in source.items[:line_index]])
    guard()
    return complete_line(source, line_index, ItemGridDriver(root, grid, cells, guard), diagnostics)
