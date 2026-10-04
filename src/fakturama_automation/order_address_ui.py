"""Select and verify document address tabs before contact linking and Save."""

import re
import time
import io
from pathlib import Path

from .debtor_ui import _tab_items
from .errors import ReviewRequired
from .selector_ui import exactly_one
from .uia import Locator, unique_control


def address_tab_point(words, name):
    caption = name.split()[0]
    pairs = [(a, b) for a in words for b in words
             if a.text == caption and b.text == 'address' and min(a.confidence, b.confidence) >= 60
             and 0 <= b.left - (a.left + a.width) <= 2 * a.height
             and max(a.top, b.top) < min(a.top + a.height, b.top + b.height)]
    a, b = exactly_one(pairs, 'caption', 'order_address')
    return ((a.left + b.left + b.width) // 2, (a.top + a.top + a.height) // 2)


def _activate_tab(tab, name, guard):
    try:
        tab.iface_selection_item.Select()
        return
    except Exception as exc:
        # SWT exposes CurrentIsSelected but raises DISP_E_MEMBERNOTFOUND for
        # Select. Other failures are uncertain and must not trigger a click.
        if getattr(exc, 'hresult', None) != -2147352573:
            raise ReviewRequired('order_address_tab_select_uncertain') from None
    guard()
    if tab.iface_selection_item.CurrentIsSelected:
        return
    from .selector_vision import ocr_words
    parent = tab.parent()
    rect = parent.rectangle()
    image = parent.capture_as_image()
    if image.size != (rect.width(), rect.height()):
        raise ReviewRequired('order_address_capture_scale_mismatch')
    output = io.BytesIO()
    image.save(output, format='PNG')
    words, _ = ocr_words(output.getvalue(), Path('C:/Program Files/Tesseract-OCR/tesseract.exe'))
    point = address_tab_point(words, name)
    guard()
    if parent.rectangle() != rect:
        raise ReviewRequired('order_address_tabs_moved')
    parent.click_input(coords=point)


def select_address_tab(root, name, guard, editor_name='New Order'):
    if name not in {'Invoice address', 'Delivery address'}:
        raise ReviewRequired('order_address_tab_invalid')
    guard()
    editor = unique_control(root, Locator('Pane', name=editor_name))
    def tab():
        # SWT marks even selected TabItems invisible; use SelectionItem,
        # scoped to the active editor, rather than visibility or coordinates.
        return exactly_one([c for c in _tab_items(editor) if c.element_info.name == name],
                           'tab', 'order_address')
    selected = tab()
    if not selected.iface_selection_item.CurrentIsSelected:
        _activate_tab(selected, name, guard)
    deadline = time.monotonic() + 5
    while not tab().iface_selection_item.CurrentIsSelected:
        guard()
        if time.monotonic() >= deadline:
            raise ReviewRequired('order_address_tab_not_selected')
        time.sleep(.1)
    guard()
    return editor


def address_matches(text, address, debtor=None, *, check_recipient=True):
    flat = ' '.join((text or '').split())
    wanted = [address.recipient_name if check_recipient else None,
              address.street, address.zip, address.city, address.country,
              address.additional_name, address.specification, address.district]
    if debtor is not None:
        wanted.extend([debtor.company, debtor.first_name, debtor.last_name])
    return all(re.search(r'(?<!\w)' + re.escape(' '.join(value.split())) + r'(?!\w)', flat)
               for value in wanted if value)


def verify_order_addresses(root, source, guard, *, diagnostics=None, editor_name='New Order'):
    from .order_finalize_ui import _snapshot, read_document
    diagnostics = diagnostics if diagnostics is not None else {}
    observed = {}
    diagnostics['addresses'] = observed
    try:
        for name, address, debtor in [('Invoice address', source.debtor.billing, source.debtor),
                                       ('Delivery address', source.debtor.delivery, None)]:
            select_address_tab(root, name, guard, editor_name)
            first = read_document(_snapshot(root)[0])['values']['address_text']
            second = read_document(_snapshot(root)[0])['values']['address_text']
            observed[name] = first
            # Fakturama formats delivery headings from the linked contact's
            # company/person, even when the source names a warehouse. Keep that
            # formatting separate from verification of the destination fields.
            if first != second or not address_matches(first, address, debtor, check_recipient=debtor is not None):
                raise ReviewRequired('order_address_source_mismatch')
    finally:
        # Fakturama's Save binding depends on the selected address page.
        select_address_tab(root, 'Invoice address', guard, editor_name)
    diagnostics['addresses'] = observed
    return {'invoice_address_selected': True, 'billing_address_verified': True,
            'delivery_address_verified': True, 'delivery_verification': 'POSTAL_FIELDS',
            'delivery_recipient_matches_source': address_matches(observed['Delivery address'], source.debtor.delivery)}
