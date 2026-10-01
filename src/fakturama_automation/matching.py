"""Selection rules: exact identifiers; no fuzzy first-row fallback."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable, TypeVar

from .domain import Debtor, clean_text
from .errors import ReviewRequired


@dataclass(frozen=True)
class DebtorRow:
    id: str
    company: str
    first_name: str
    last_name: str
    zip: str
    city: str


@dataclass(frozen=True)
class ProductRow:
    id: str
    sku: str
    description: str
    vat_percent: Decimal


@dataclass(frozen=True)
class VatRow:
    id: str
    name: str
    value: Decimal
    code: str


@dataclass(frozen=True)
class PaymentRow:
    id: str
    name: str
    code: str
    cash_discount: Decimal
    discount_days: int
    net_days: int


PAYMENT_CODES = {"Bank Transfer": "Credit transfer", "Credit Card": "Credit card",
                 "SEPA Direct Debit": "SEPA direct debit"}


def vat_name(rate: Decimal) -> str:
    return f"VAT {format(rate.normalize(), 'f')}%"


T = TypeVar("T")


def exact_one(rows: Iterable[T], predicate: Callable[[T], bool], code: str) -> T | None:
    matches = [row for row in rows if predicate(row)]
    if len(matches) > 1:
        raise ReviewRequired(f"ambiguous_{code}")
    return matches[0] if matches else None


def debtor_match(rows: Iterable[DebtorRow], debtor: Debtor) -> DebtorRow | None:
    rows = list(rows)
    expected = (debtor.company, debtor.first_name, debtor.last_name, debtor.billing.zip, debtor.billing.city)
    def fields(row):
        return tuple(clean_text(x) for x in (row.company, row.first_name, row.last_name, row.zip, row.city))
    result = exact_one(rows, lambda r: fields(r) == expected, "debtor")
    if result is None:
        # Same company/contact with a different postcode/city is a conflict, not absent.
        if any(fields(r)[:3] == expected[:3] for r in rows):
            raise ReviewRequired("conflicting_debtor_identity")
    return result


def product_match(rows: Iterable[ProductRow], sku: str) -> ProductRow | None:
    return exact_one(rows, lambda r: clean_text(r.sku) == sku, "product")


def vat_match(rows: Iterable[VatRow], rate: Decimal) -> VatRow | None:
    row = exact_one(rows, lambda r: clean_text(r.name) == vat_name(rate), "vat")
    if row and (row.value != rate or row.code != "S"):
        raise ReviewRequired("conflicting_vat_definition")
    return row


def payment_match(rows: Iterable[PaymentRow], method: str) -> PaymentRow | None:
    row = exact_one(rows, lambda r: clean_text(r.name) == method, "payment")
    if row and (row.code != PAYMENT_CODES[method] or row.cash_discount != 0
                or row.discount_days != 0 or row.net_days != 0):
        raise ReviewRequired("conflicting_payment_definition")
    return row
