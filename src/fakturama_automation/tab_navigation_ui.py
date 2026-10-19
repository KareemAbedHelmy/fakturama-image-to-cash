"""Select an observed SWT tab using its current caption and verify selection."""

import re
import time
from pathlib import Path

from .debtor_ui import _tab_items
from .errors import ReviewRequired
from .selector_ui import exactly_one


def tab_caption_point(words, caption):
    target = caption.split()
    if not target or len(target) > 5:
        raise ReviewRequired('tab_navigation_caption_invalid')
    candidates = []
    for first in words:
        if first.text.lstrip('*') != target[0] or first.confidence < 60:
            continue
        phrase = [first]
        for token in target[1:]:
            prior = phrase[-1]
            next_words = [w for w in words if w.text == token and w.confidence >= 60
                          and 0 <= w.left - (prior.left + prior.width) <= 2 * max(prior.height, w.height)
                          and max(w.top, prior.top) < min(w.top + w.height, prior.top + prior.height)]
            if len(next_words) != 1:
                break
            phrase.append(next_words[0])
        if len(phrase) == len(target):
            candidates.append(phrase)
    if not candidates and len(target) == 1:
        # On this SWT tab strip, Tesseract sometimes reads the first zero as
        # an extra letter O (PO000004 -> PoO000004). Require every digit and
        # the full letter prefix so a neighboring document number cannot match.
        match = re.fullmatch(r'([A-Za-z]+)(\d{6,})', target[0])
        if match:
            prefix, digits = match.groups()
            candidates = [[w] for w in words
                          if w.confidence >= 20 and w.top < 4 * w.height
                          and (observed := re.fullmatch(r'([A-Za-z]+)(\d{6,})', w.text.lstrip('*')))
                          and observed[2] == digits
                          and observed[1].casefold() in {prefix.casefold(), (prefix + 'O').casefold()}]
    phrase = exactly_one(candidates, 'caption', 'tab_navigation')
    left = phrase[0].left
    right = phrase[-1].left + phrase[-1].width
    return ((left + right) // 2, phrase[0].top + phrase[0].height // 2)


def select_tab(scope, captions, guard, *, tesseract=Path('C:/Program Files/Tesseract-OCR/tesseract.exe')):
    """Use UIA Select, falling back to live OCR only for SWT's known HRESULT."""
    captions = set(captions)
    if not captions or any(not isinstance(c, str) or not c for c in captions):
        raise ReviewRequired('tab_navigation_caption_invalid')

    def current():
        return exactly_one([t for t in _tab_items(scope) if t.element_info.name in captions],
                           'tab', 'tab_navigation')

    guard()
    tab = current()
    if not tab.iface_selection_item.CurrentIsSelected:
        try:
            tab.iface_selection_item.Select()
        except Exception as exc:
            if getattr(exc, 'hresult', None) != -2147352573:
                raise ReviewRequired('tab_navigation_select_uncertain') from None
            guard()
            if not current().iface_selection_item.CurrentIsSelected:
                parent = tab.parent()
                rect = parent.rectangle()
                from .order_items_ui import grid_words
                caption = next(c for c in captions if c == tab.element_info.name)
                number_or_order = bool(re.fullmatch(r'[A-Za-z]+\d{6,}', caption.lstrip('*'))
                                       or caption.lstrip('*') == 'New Order')
                # Only the reading is repeated: the strip can still be redrawing right after an editor closes.
                for attempt in range(3):
                    image = parent.capture_as_image()
                    if image.size != (rect.width(), rect.height()):
                        raise ReviewRequired('tab_navigation_capture_scale_mismatch')
                    bottom = min(image.height, 75) if number_or_order else image.height
                    words = grid_words(image.crop((0, 0, image.width, bottom)),
                                       tesseract, [caption.lstrip('*')])
                    try:
                        point = tab_caption_point(words, caption.lstrip('*'))
                        break
                    except ReviewRequired as exc:
                        if exc.code != 'tab_navigation_caption_missing_or_ambiguous' or attempt == 2:
                            raise
                        time.sleep(1.5)
                        guard()
                guard()
                if parent.rectangle() != rect:
                    raise ReviewRequired('tab_navigation_tabs_moved')
                parent.click_input(coords=point)
    deadline = time.monotonic() + 5
    while not current().iface_selection_item.CurrentIsSelected:
        guard()
        if time.monotonic() >= deadline:
            raise ReviewRequired('tab_navigation_not_selected')
        time.sleep(.1)
    guard()
    return current()
