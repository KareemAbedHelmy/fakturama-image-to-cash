"""Type literal text into a focused field with real key events.

Some Fakturama fields (the Debtor's Company) take their model value only from key events; a UIA write
changes the displayed text but the saved record keeps the old value. The text is escaped so that no
character is read as pywinauto key syntax, and control characters are refused.
"""

import time
import unicodedata

from .errors import ReviewRequired

SPECIAL = set('+^%~(){}[]')


def escape_literal(text):
    if not isinstance(text, str) or not text or len(text) > 200:
        raise ReviewRequired('literal_text_invalid')
    if any(unicodedata.category(ch).startswith('C') for ch in text):
        raise ReviewRequired('literal_text_has_control_character')
    return ''.join('{SPACE}' if ch == ' ' else '{' + ch + '}' if ch in SPECIAL else ch for ch in text)


def type_into(control, text, *, sleep=time.sleep, send=None):
    """Click the field, select its content, type `text`; the caller reads the value back."""
    escaped = escape_literal(text)
    if send is None:
        from pywinauto.keyboard import send_keys
        send = send_keys
    control.click_input()
    sleep(.2)
    send('^a', pause=.05, vk_packet=False)
    send(escaped, pause=.02, with_spaces=True, vk_packet=True)
    sleep(.3)
