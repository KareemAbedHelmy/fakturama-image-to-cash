"""VAT records: existence check in Data > VATs and creation of a missing one.

The VATs list is read with Select All / Copy (columns: Standard, Name, Description, Value as a
fraction). A record counts as present when a row has exactly the name "VAT <percent>%" and the
value percent / 100; the same name with another value, or two such rows, stops the run. A new
record gets that name and description, the default code S (Standard rate) and the value; the
existing Standard VAT is left untouched. Save is invoked once and never retried.
"""

import time
from collections import Counter
from decimal import Decimal, InvalidOperation

from .errors import ReviewRequired
from .finance import validate_order
from .matching import vat_name
from .launcher import bring_to_front
from .documents_ui import _grid
from .master_list_ui import open_list_pane, read_list_text
from .order_finalize_ui import _guard_factory, _snapshot, _wait
from .order_ui import Tree, one
from .payment_ui import _payment_root
from .uia import Locator, unique_control

NEW_TABS = {'New TAX Rate', '*New TAX Rate'}
DEFAULT_CODE = 'S (Standard rate)'


def parse_vat_rows(text):
    rows = []
    for line in (text or '').replace('\r\n', '\n').split('\n'):
        if not line.strip():
            continue
        cells = line.split('\t')
        if len(cells) != 4 or cells[0] not in {'true', 'false'}:
            raise ReviewRequired('vat_row_schema_changed')
        try:
            value = Decimal(cells[3])
        except InvalidOperation:
            raise ReviewRequired('vat_value_unreadable') from None
        rows.append({'standard': cells[0] == 'true', 'name': cells[1], 'description': cells[2], 'value': value})
    return rows


def decide_vat(rows, percent):
    """'EXISTS' for exactly one row with the exact name and value, 'MISSING' for none; conflicts stop."""
    named = [r for r in rows if r['name'] == vat_name(percent)]
    if not named:
        return 'MISSING'
    if len(named) > 1 or named[0]['value'] != percent / 100:
        raise ReviewRequired('vat_conflicting_or_duplicate_rows')
    return 'EXISTS'


def source_vat_percent(source, sku):
    validate_order(source)
    items = [i for i in source.items if i.sku == sku]
    if not items:
        raise ReviewRequired('vat_sku_not_in_source')
    percent = items[0].vat_percent
    if percent != percent.to_integral_value() or not 0 <= percent <= 100:
        raise ReviewRequired('vat_percent_must_be_a_whole_number')
    return percent


def _list_pane(root, guard):
    return open_list_pane(root, guard, 'VATs', 'VATs')


def row_lines(image, margin=10, threshold=20):
    """Y positions of the horizontal rules, read from a column right of the table where only rules show."""
    gray = image.convert('L')
    width, height = gray.size
    pixels = gray.load()
    histogram = gray.histogram()
    background = max(range(256), key=histogram.__getitem__)
    ys = [y for y in range(height) if abs(pixels[width - margin, y] - background) > threshold]
    lines = []
    for y in ys:
        if not lines or y - lines[-1][-1] > 1:
            lines.append([y])
        else:
            lines[-1].append(y)
    return [sum(group) // len(group) for group in lines]


def row_center(lines, index):
    """Centre of data row `index` (0-based): the first rule closes the header, the next ones close the rows."""
    if len(lines) < index + 2:
        raise ReviewRequired('vat_row_position_unreadable')
    return (lines[index] + lines[index + 1]) // 2


def read_existing_code(root, pane, rows, name, guard, diagnostics=None):
    """Open the existing VAT row in its editor, read its VAT code and close the unchanged editor."""
    from pywinauto.keyboard import send_keys
    index = [i for i, r in enumerate(rows) if r['name'] == name]
    if len(index) != 1:
        raise ReviewRequired('vat_existing_row_not_unique')
    guard()
    grid = _grid(pane)
    lines = row_lines(grid.capture_as_image())
    y = row_center(lines, index[0])
    before = list(_tab_names(root))
    grid.double_click_input(coords=(300, y))
    deadline = time.monotonic() + 8
    while True:
        guard()
        combos = [c for c in root.descendants(control_type='ComboBox')
                  if c.element_info.name == 'VAT code (E-Invoice)' and c.is_visible()]
        if len(combos) == 1:
            break
        if time.monotonic() > deadline:
            raise ReviewRequired('vat_existing_editor_not_observed')
        time.sleep(.2)
    after = list(_tab_names(root))
    opened = list((Counter(after) - Counter(before)).elements())  # a second tab with the same caption counts
    if diagnostics is not None:
        diagnostics['tabs'] = {'before': before, 'after': after}
    editor_name = unique_control(root, Locator('Edit', name='Name')).iface_value.CurrentValue
    code = combos[0].iface_value.CurrentValue
    if editor_name != name:
        raise ReviewRequired('vat_existing_editor_wrong_row')  # left open for inspection
    if len(opened) == 1 and not opened[0].startswith('*'):
        target, expected = opened[0], Counter(before)[opened[0]]  # a new editor tab: closing it removes just that tab
    elif not opened and name in before and Counter(after) == Counter(before):
        target, expected = name, Counter(before)[name] - 1  # Fakturama re-used the saved editor left open after a create
    else:
        raise ReviewRequired('vat_existing_editor_state_unexpected')
    guard()
    send_keys('^w', pause=.05, vk_packet=False)  # unchanged editor: closes without a prompt
    deadline = time.monotonic() + 5
    # Only the editor tab is compared: showing the Order again adds its own inner tabs to the list.
    while Counter(_tab_names(root))[target] != expected:
        if time.monotonic() > deadline:
            raise ReviewRequired('vat_existing_editor_not_closed')
        time.sleep(.2)
    return code


def read_vat_rows(root, pane, guard, driver):
    return parse_vat_rows(read_list_text(root, pane, guard, driver))


def check_vat_scope(root, source, sku, guard, *, diagnostics=None, driver=None):
    from .windows_clipboard import WindowsClipboard
    percent = source_vat_percent(source, sku)
    diagnostics = diagnostics if diagnostics is not None else {}
    guard()
    pane = _list_pane(root, guard)
    rows = read_vat_rows(root, pane, guard, driver or WindowsClipboard())
    diagnostics['row_count'] = len(rows)
    if not rows:
        raise ReviewRequired('vat_list_unexpectedly_empty')  # Fakturama always keeps a Standard VAT
    decision = decide_vat(rows, percent)
    result = {'status': 'VAT_EXISTS' if decision == 'EXISTS' else 'VAT_MISSING',
              'name': vat_name(percent), 'rows': [[r['name'], str(r['value'])] for r in rows]}
    if decision == 'EXISTS':
        code = read_existing_code(root, pane, rows, vat_name(percent), guard, diagnostics)
        diagnostics['code'] = code
        if code != DEFAULT_CODE:
            raise ReviewRequired('vat_existing_code_not_standard')  # 3.5: any conflicting setting stops
        result['code'] = code
    return result


def _tab_names(root):
    tree = Tree(_snapshot(root)[0])
    return [n.data['name'] for n in tree.under(tree.nodes[()], 'TabItem')]


def create_vat_scope(root, source, sku, guard, *, diagnostics=None):
    from pywinauto.keyboard import send_keys
    percent = source_vat_percent(source, sku)
    name = vat_name(percent)
    diagnostics = diagnostics if diagnostics is not None else {}
    progress = diagnostics.setdefault('progress', {'phase': 'preflight', 'open_invoked': False, 'save_invoked': False})
    guard()
    reuse = bool(NEW_TABS & set(_tab_names(root)))  # a blank editor left open by an interrupted run
    progress['reused_open_editor'] = reuse
    if not reuse:
        _list_pane(root, guard)
        button = unique_control(root, Locator('Button', name='Create a new tax rate'))
        progress['phase'] = 'open'
        progress['open_invoked'] = True
        try:
            button.iface_invoke.Invoke()
        except Exception:
            raise ReviewRequired('vat_open_invoke_uncertain') from None
    editor, deadline = None, time.monotonic() + 8
    while editor is None:
        found = [p for p in root.descendants(control_type='Pane')
                 if p.element_info.name == 'New TAX Rate' and p.is_visible()]
        editor = found[0] if len(found) == 1 else None
        if editor is None and time.monotonic() > deadline:
            raise ReviewRequired('vat_new_editor_not_observed')
        time.sleep(.2)
    bring_to_front(root.handle)  # opening an editor moves focus; restore it before any guarded step
    unique_control(editor, Locator('Edit', name='Name')).click_input()  # a real click activates the editor
    time.sleep(.5)
    guard()

    def field(label):
        return unique_control(editor, Locator('Edit', name=label))

    fields = {label: field(label) for label in ('Name', 'Description', 'Value')}
    code = unique_control(editor, Locator('ComboBox', name='VAT code (E-Invoice)'))
    standard = [e for e in editor.descendants(control_type='Edit') if e.is_visible() and not e.element_info.name]
    if len(standard) != 1:
        raise ReviewRequired('vat_standard_field_missing_or_ambiguous')
    before = {'name': fields['Name'].iface_value.CurrentValue, 'description': fields['Description'].iface_value.CurrentValue,
              'value': fields['Value'].iface_value.CurrentValue, 'code': code.iface_value.CurrentValue,
              'standard': standard[0].iface_value.CurrentValue}
    diagnostics['before'] = before
    blank = not before['name'] and not before['description'] and before['value'] == '0%'
    if (not reuse and not blank) or before['code'] != DEFAULT_CODE:
        raise ReviewRequired('vat_new_editor_not_blank')  # a reused editor is overwritten and re-verified
    progress['phase'] = 'fill'
    for label in ('Name', 'Description'):
        fields[label].iface_value.SetValue(name)
    fields['Value'].click_input()  # UIA SetFocus does not move keyboard focus in this editor
    time.sleep(.4)
    guard()
    send_keys('^a', pause=.05, vk_packet=False)
    send_keys(str(int(percent)), pause=.05, vk_packet=False)
    fields['Name'].click_input()  # leave the Value field so it commits
    time.sleep(.5)
    after = {'name': fields['Name'].iface_value.CurrentValue, 'description': fields['Description'].iface_value.CurrentValue,
             'value': fields['Value'].iface_value.CurrentValue, 'code': code.iface_value.CurrentValue,
             'standard': standard[0].iface_value.CurrentValue}
    diagnostics['before_save'] = after
    if (after['name'] != name or after['description'] != name or after['value'] != f'{int(percent)}%'
            or after['code'] != DEFAULT_CODE or after['standard'] != before['standard']):
        raise ReviewRequired('vat_fields_do_not_match_source')
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
        raise ReviewRequired('vat_save_invoke_uncertain') from None
    tabs, ok = _wait(lambda: _tab_names(root), lambda t: not (NEW_TABS & set(t)))
    diagnostics['tabs_after'] = tabs
    if not ok:
        raise ReviewRequired('vat_save_not_confirmed_check_dialogs')
    progress['phase'] = 'complete'
    return {'status': 'VAT_SAVED', 'name': name, 'value': after['value'], 'code': after['code'], 'tabs': tabs,
            'note': 'Saved once; re-run vat-check to confirm it in Data > VATs.'}


def check_vat(pid, handle, source, sku, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return check_vat_scope(root, source, sku, _guard_factory(root, pid, handle), diagnostics=diagnostics)


def create_vat(pid, handle, source, sku, *, diagnostics=None):
    root = _payment_root(pid, handle)
    return create_vat_scope(root, source, sku, _guard_factory(root, pid, handle), diagnostics=diagnostics)
