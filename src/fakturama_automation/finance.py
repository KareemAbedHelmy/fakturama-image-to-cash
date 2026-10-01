"""Explicit initial rounding policy; live Fakturama parity still needs validation."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP, localcontext

from .domain import Item, OrderInput, Totals
from .errors import ReviewRequired

ZERO = Decimal("0")
HUNDRED = Decimal("100")
CENT = Decimal("0.01")


def cents(value: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        return value.quantize(CENT, rounding=ROUND_HALF_UP)


def product_gross(item: Item) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        return cents(item.unit_net * (1 + item.vat_percent / HUNDRED))


def line_net(item: Item) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        return cents(item.quantity * item.unit_net * (1 - item.discount_percent / HUNDRED))


def calculate_totals(order: OrderInput) -> Totals:
    # Initial policy: round line nets, aggregate by VAT rate, round each rate's tax.
    buckets: dict[Decimal, Decimal] = defaultdict(lambda: ZERO)
    with localcontext() as ctx:
        ctx.prec = 50
        for item in order.items:
            buckets[item.vat_percent] += line_net(item)
        net = sum(buckets.values(), ZERO)
        vat = sum((cents(base * rate / HUNDRED) for rate, base in buckets.items()), ZERO)
        return Totals(net=net, vat=vat, gross=net + vat)


def validate_order(order: OrderInput) -> Totals:
    if order.review_issues:
        raise ReviewRequired("extraction_review_issues")
    if order.payment.status not in {"PAID", "UNPAID"}:
        raise ReviewRequired("unsupported_payment_status")
    if order.order_discount_percent != ZERO or order.shipping_net != ZERO:
        raise ReviewRequired("unsupported_order_discount_or_shipping")
    for item in order.items:
        if line_net(item) != item.source_line_net:
            raise ReviewRequired("source_line_total_mismatch")
    expected = calculate_totals(order)
    if order.totals != expected:
        raise ReviewRequired("source_totals_mismatch")
    # Repeated SKUs remain separate lines. Conflicting master descriptions/VAT require review.
    masters: dict[str, tuple[str, Decimal]] = {}
    for item in order.items:
        definition = (item.description, item.vat_percent)
        if item.sku in masters and masters[item.sku] != definition:
            raise ReviewRequired("repeated_sku_definition_conflict")
        masters[item.sku] = definition
    return expected
