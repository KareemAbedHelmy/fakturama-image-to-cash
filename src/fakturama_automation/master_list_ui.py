"""Open one of Fakturama's master-data lists (Data menu) and read its rows by Select All / Copy.

Shared by the VAT and terms-of-payment checks. The grid is read twice and must give identical text; an
empty grid is detected from its pixels first, because copying from one makes Fakturama throw.
"""

import time

from .documents_ui import _grid, _menu_entry
from .errors import ReviewRequired
from .selector_live_ui import grid_has_rows
from .uia import Locator, unique_control


def open_list_pane(root, guard, menu_caption, pane_name):
    def find():
        return [p for p in root.descendants(control_type='Pane')
                if p.element_info.name == pane_name and p.is_visible()]
    found = find()
    if not found:
        guard()
        unique_control(root, Locator('MenuItem', name='Data')).click_input()
        _menu_entry(root, menu_caption, guard).click_input()
        deadline = time.monotonic() + 5
        while not found and time.monotonic() < deadline:
            guard()
            found = find()
            time.sleep(.1)
    if len(found) != 1:
        raise ReviewRequired('list_pane_missing_or_ambiguous')
    return found[0]


def read_list_text(root, pane, guard, driver):
    """The grid's rows as copied text, or '' when the grid is empty."""
    from pywinauto.keyboard import send_keys
    grid = _grid(pane)
    guard()
    if not grid_has_rows(grid.capture_as_image()):
        return ''
    first = None
    for _ in range(2):
        guard()
        grid.click_input()
        driver.assert_focus(root.handle, grid.handle, root.element_info.process_id)
        before = driver.sequence()
        send_keys('^a', pause=.05, vk_packet=False)
        send_keys('^c', pause=.05, vk_packet=False)
        copied, deadline = None, time.monotonic() + 3
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
            raise ReviewRequired('list_copy_unavailable')
        if first is not None and copied != first:
            raise ReviewRequired('list_rows_changed_during_read')
        first = copied
    return first
