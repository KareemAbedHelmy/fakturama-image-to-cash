"""Parse the headerless six-column text copied from the payment table.
Example row: true<TAB>Cash<TAB>Pay Cash<TAB>0.0<TAB>0<TAB>0. A parser only: it touches no clipboard.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass
from decimal import Decimal

from .errors import ReviewRequired
from .input_io import MAX_JSON_BYTES
from .matching import PAYMENT_CODES

MAX_ROWS = 1000
PAYMENT_COLUMNS = ("Standard", "Name", "Description", "Discount", "Disc. Days", "Net Days")


def observe_payment_columns(words) -> list[dict]:
    """Require one high-confidence header band in the observed column order.

    Only header typography is normalized. Payment names/data remain literal.
    Words/boxes are image-local and never become desktop click coordinates.
    """
    def key(text):
        return re.sub(r"[ .]", "", text).casefold()
    matches = []
    for anchor in words:
        if key(anchor.text) != key(PAYMENT_COLUMNS[0]):
            continue
        band = sorted([w for w in words if max(w.top, anchor.top) <
                       min(w.top + w.height, anchor.top + anchor.height)], key=lambda w: w.left)
        index, headers = band.index(anchor), []
        for label in PAYMENT_COLUMNS:
            combined, parts = "", []
            while index < len(band) and len(combined) < len(key(label)):
                parts.append(band[index])
                combined += key(band[index].text)
                index += 1
            if (combined != key(label) or not parts or min(w.confidence for w in parts) < 70
                    or any(a.left + a.width > b.left for a, b in zip(parts, parts[1:]))):
                break
            headers.append({"label": label, "words": [asdict(w) for w in parts]})
        if len(headers) == len(PAYMENT_COLUMNS):
            matches.append(headers)
    if len(matches) != 1:
        raise ReviewRequired("payment_copy_headers_missing_low_confidence_or_wrong_order")
    return matches[0]


@dataclass(frozen=True)
class CopiedPaymentRow:
    standard: bool
    name: str
    description: str
    discount_raw: str
    discount_days: int
    net_days: int


def parse_payment_table(text: str) -> tuple[CopiedPaymentRow, ...]:
    """Preserve text; reject malformed/partial rows rather than dropping them.

    Discount units have not been characterized for nonzero clipboard values.
    Preserve that cell literally; only zero is safe to compare with the brief.
    No fuzzy identifiers, CSV quoting, numeric exponents or thousands syntax.
    """
    if not isinstance(text, str):
        raise ReviewRequired("payment_table_invalid_text")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise ReviewRequired("payment_table_invalid_text") from None
    if size > MAX_JSON_BYTES:
        raise ReviewRequired("payment_table_too_large")
    normalized = text.replace("\r\n", "\n")
    if any(unicodedata.category(c).startswith("C") and c not in "\t\n" for c in normalized):
        raise ReviewRequired("payment_table_invalid_text")
    if not normalized:
        raise ReviewRequired("payment_table_empty_copy_unverified")
    lines = normalized.split("\n")
    if lines[-1] == "":
        lines.pop()  # One record terminator, not arbitrary blank-row stripping.
    if len(lines) > MAX_ROWS:
        raise ReviewRequired("payment_table_too_many_rows")
    rows = []
    for line in lines:
        cells = line.split("\t")
        if len(cells) != 6:
            raise ReviewRequired("payment_table_column_count_mismatch")
        standard, name, description, discount, discount_days, net_days = cells
        if (standard not in {"true", "false"} or not name or not name.strip()
                or max(len(name), len(description)) > 1000):
            raise ReviewRequired("payment_table_invalid_identity")
        if (not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,8})?", discount)
                or Decimal(discount) > 100):
            raise ReviewRequired("payment_table_invalid_discount")
        if any(not re.fullmatch(r"[0-9]{1,6}", value) for value in (discount_days, net_days)):
            raise ReviewRequired("payment_table_invalid_days")
        rows.append(CopiedPaymentRow(standard == "true", name, description,
                                     discount, int(discount_days), int(net_days)))
    return tuple(rows)


def inspect_copied_payment_rows(text: str, method: str) -> dict:
    """Check exact names in copied rows without asserting complete enumeration."""
    if not isinstance(method, str) or method not in PAYMENT_CODES:
        raise ReviewRequired("payment_table_unsupported_method")
    rows = parse_payment_table(text)
    matches = [row for row in rows if row.name == method]
    if len(matches) > 1:
        raise ReviewRequired("ambiguous_payment")
    if matches:
        row = matches[0]
        if Decimal(row.discount_raw) != 0 or row.discount_days != 0 or row.net_days != 0:
            raise ReviewRequired("conflicting_payment_definition")
    return {"status": "OBSERVED_COPIED_PAYMENT_ROWS_ONLY",
            "rows": [asdict(row) for row in rows],
            "exact_name_count": len(matches),
            "lookup_result": "EXACT_NAME_IN_COPY" if matches else "NO_EXACT_NAME_IN_COPY",
            "clipboard_provenance_verified": False, "all_rows_enumerated": False,
            "payment_code_verified": False, "master_absence_verified": False,
            "persistence_verified": False,
            "note": "Parsed data only. The format omits saved ID and payment code. Complete enumeration and fresh application provenance are not established by this parser; this report never authorizes creation or save."}
