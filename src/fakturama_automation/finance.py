"""Decimal money rules: line nets, VAT per rate, order discount and totals, checked against live Fakturama."""

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
        factor = 1 - order.order_discount_percent / HUNDRED
        buckets = {rate: cents(base * factor) for rate, base in buckets.items()}  # order discount per VAT rate
        net = sum(buckets.values(), ZERO)
        vat = sum((cents(base * rate / HUNDRED) for rate, base in buckets.items()), ZERO)
        return Totals(net=net, vat=vat, gross=net + vat)


def items_subtotal(order: OrderInput) -> Decimal:
    """Sum of the line nets before the order discount: what Fakturama shows as the Order's subtotal."""
    return sum((line_net(item) for item in order.items), ZERO)


def validate_order(order: OrderInput) -> Totals:
    if order.review_issues:
        raise ReviewRequired("extraction_review_issues")
    if order.payment.status not in {"PAID", "UNPAID"}:
        raise ReviewRequired("unsupported_payment_status")
    if order.shipping_net != ZERO:
        raise ReviewRequired("unsupported_order_shipping")
    if not ZERO <= order.order_discount_percent < HUNDRED:
        raise ReviewRequired("order_discount_out_of_range")
    for item in order.items:
        if line_net(item) != item.source_line_net:
            raise ReviewRequired("source_line_total_mismatch")
    expected = calculate_totals(order)
    if order.totals != expected:
        raise ReviewRequired("source_totals_mismatch")
    # Repeated SKUs remain separate lines. Conflicting master descriptions/VAT require review.
    masters: dict[str, tuple[str, Decimal, str | None]] = {}
    for item in order.items:
        definition = (item.description, item.vat_percent, item.unit)
        if item.sku in masters and masters[item.sku] != definition:
            raise ReviewRequired("repeated_sku_definition_conflict")
        masters[item.sku] = definition
    return expected
