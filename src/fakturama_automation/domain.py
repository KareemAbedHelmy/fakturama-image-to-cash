"""Strict input contract. Numeric JSON values are strings to avoid float loss."""

import hashlib
import json
import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictStr, WithJsonSchema, model_validator


def clean_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("text_required")
    value = unicodedata.normalize("NFC", value)
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise ValueError("control_character")
    if len(value) > 1000:
        raise ValueError("text_too_long")
    return " ".join(value.split())


Text = Annotated[StrictStr, BeforeValidator(clean_text), Field(max_length=1000)]
RequiredText = Annotated[Text, Field(min_length=1)]
Identifier = Annotated[Text, Field(min_length=1, max_length=120)]


def exact_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"\d{1,12}(?:\.\d{1,6})?", value):
        result = Decimal(value)
    else:
        raise ValueError("plain_decimal_string_required")
    if not result.is_finite() or result < 0 or result > Decimal("999999999999"):
        raise ValueError("decimal_out_of_range")
    if result.as_tuple().exponent < -6:
        raise ValueError("too_many_decimal_places")
    return result


Amount = Annotated[Decimal, BeforeValidator(exact_decimal), Field(ge=0, le=999999999999),
                   WithJsonSchema({"type": "string", "pattern": r"^\d{1,12}(?:\.\d{1,6})?$"})]
Percent = Annotated[Amount, Field(le=100)]
Quantity = Annotated[Amount, Field(gt=0, le=1000000)]


def iso_date(value: object) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("iso_date_required")
    return date.fromisoformat(value)


ISODate = Annotated[date, BeforeValidator(iso_date)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class Address(StrictModel):
    street: RequiredText
    zip: RequiredText
    city: RequiredText
    country: RequiredText
    additional_name: Text | None = None
    specification: Text | None = None
    district: Text | None = None
    email: Text | None = None
    telephone: Text | None = None


class Debtor(StrictModel):
    company: Text
    first_name: Text
    last_name: Text
    alias: Text | None = None
    salutation: Text | None = None
    billing: Address
    delivery: Address

    @model_validator(mode="after")
    def identity_present(self):
        if not self.company and not (self.first_name or self.last_name):
            raise ValueError("debtor_identity_missing")
        return self


class Payment(StrictModel):
    method: Literal["Bank Transfer", "Credit Card", "SEPA Direct Debit"]
    status: Literal["PAID", "UNPAID", "UNKNOWN", "PARTIALLY_PAID"]
    date: ISODate | None = None

    @model_validator(mode="after")
    def paid_needs_date(self):
        if self.status == "PAID" and self.date is None:
            raise ValueError("paid_date_missing")
        if self.status == "UNPAID" and self.date is not None:
            raise ValueError("unpaid_has_payment_date")
        return self


class Item(StrictModel):
    sku: Identifier
    description: RequiredText
    quantity: Quantity
    unit_net: Amount
    vat_percent: Percent
    discount_percent: Percent
    source_line_net: Amount


class Totals(StrictModel):
    net: Amount
    vat: Amount
    gross: Amount


class OrderInput(StrictModel):
    order_date: ISODate
    external_reference: Identifier
    currency: Literal["EUR"]
    debtor: Debtor
    payment: Payment
    items: Annotated[tuple[Item, ...], Field(min_length=1, max_length=100)]
    order_discount_percent: Percent
    shipping_net: Amount
    totals: Totals
    review_issues: Annotated[tuple[RequiredText, ...], Field(max_length=100)] = ()


def canonical_json(order: OrderInput) -> str:
    return json.dumps(order.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(order: OrderInput) -> str:
    return hashlib.sha256(canonical_json(order).encode()).hexdigest()


def business_key(order: OrderInput) -> str:
    # Do not include amounts or dates: altered data for the same identity/ref is a conflict.
    key = [order.external_reference, order.debtor.company, order.debtor.first_name,
           order.debtor.last_name, order.debtor.billing.zip, order.debtor.billing.city]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
