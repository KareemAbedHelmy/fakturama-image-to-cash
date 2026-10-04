"""In-memory contract simulator. NOT a Fakturama desktop implementation.

Tracks editor/selector identity, persists copies only on save, and supports
failure injection so verification and crash behavior.
"""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from .domain import Debtor, Item, OrderInput
from .errors import ReviewRequired
from .finance import calculate_totals, line_net, product_gross
from .matching import PAYMENT_CODES, DebtorRow, PaymentRow, ProductRow, VatRow, vat_name
from .ports import Handle, Snapshot


@dataclass
class ProductMaster:
    row: ProductRow
    gross: Decimal
    cost_net: Decimal = Decimal("0")
    stock: Decimal = Decimal("0")


class Simulator:
    def __init__(self):
        self.calls: list[str] = []
        self.source: OrderInput | None = None
        self.order: Handle | None = None
        self.invoice: Handle | None = None
        self.order_open = False
        self.selector: str | None = None
        self.debtors: dict[str, Debtor] = {}
        self.debtor_rows: list[DebtorRow] = []
        self.payments: list[PaymentRow] = []
        self.vats: list[VatRow] = []
        self.products: list[ProductMaster] = []
        self.pending_debtor: Debtor | None = None
        self.pending_product: ProductRow | None = None
        self.selected_debtor: Debtor | None = None
        self.lines: list[Item] = []
        self.invoice_snapshot: Snapshot | None = None
        self.documents: dict[tuple[str, str], Snapshot] = {}
        self.product_selection_count = 0
        self.fail_before: str | None = None
        self.fail_after: str | None = None
        self.hide_saved_master: str | None = None
        self.hide_document_rows = False

    def call(self, name: str):
        self.calls.append(name)
        if self.fail_before == name:
            raise ReviewRequired("simulated_failure_before_action")

    def after(self, name: str):
        if self.fail_after == name:
            raise ReviewRequired("simulated_failure_after_action")

    def check(self, value: bool, code: str):
        if not value:
            raise ReviewRequired(code)

    def preflight(self, source: OrderInput):
        self.call("preflight")
        if any(doc.reference == source.external_reference for doc in self.documents.values()):
            raise ReviewRequired("existing_document_reference")
        self.check(not self.order_open, "unexpected_existing_editor")
        self.source = source

    def open_new_order(self) -> Handle:
        self.call("open_new_order")
        self.check(self.source is not None, "preflight_missing")
        count = sum(kind == "Order" for kind, _ in self.documents)
        self.order = Handle("order-editor", f"O{count+1:05d}")
        self.order_open = True
        self.after("open_new_order")
        return self.order

    def assert_order_open(self, order: Handle):
        self.check(self.order_open and order == self.order, "original_order_not_open")

    def configure_order(self, order: Handle, source: OrderInput):
        self.call("configure_order")
        self.assert_order_open(order)
        self.check(source == self.source, "source_changed")

    def search_debtors_from_order(self, order: Handle, query: str):
        self.call("search_debtors_from_order")
        self.assert_order_open(order)
        self.check(self.selector is None, "selector_already_open")
        self.selector = "debtor"
        self.pending_debtor = None
        if self.hide_saved_master == "debtor":
            return ()
        return tuple(r for r in self.debtor_rows if query.casefold() in (r.company or f"{r.first_name} {r.last_name}").casefold())

    def cancel_selector(self):
        self.call("cancel_selector")
        self.check(self.selector is not None, "selector_not_open")
        self.selector = None

    def select_debtor(self, order: Handle, row: DebtorRow):
        self.call("select_debtor")
        self.assert_order_open(order)
        self.check(self.selector == "debtor", "wrong_selector")
        self.check(row.id in self.debtors, "debtor_missing")
        self.selected_debtor = self.debtors[row.id]
        self.selector = None

    def verify_addresses(self, order: Handle, debtor: Debtor):
        self.call("verify_addresses")
        self.assert_order_open(order)
        self.check(self.selected_debtor is not None, "debtor_not_selected")
        self.check(self.selected_debtor.billing == debtor.billing and self.selected_debtor.delivery == debtor.delivery,
                   "populated_addresses_mismatch")
        self.check((self.selected_debtor.company, self.selected_debtor.first_name, self.selected_debtor.last_name)
                   == (debtor.company, debtor.first_name, debtor.last_name), "populated_debtor_identity_mismatch")

    def open_debtor_editor(self, order: Handle, debtor: Debtor):
        self.call("open_debtor_editor")
        self.assert_order_open(order)
        self.check(self.selector is None, "selector_must_be_cancelled")
        self.pending_debtor = debtor
        return "debtor-editor"

    def debtor_payment_options(self, editor: str):
        self.call("debtor_payment_options")
        self.check(editor == "debtor-editor" and self.pending_debtor is not None, "debtor_editor_missing")
        return tuple(row.name for row in self.payments)

    def search_payment_terms(self, method: str):
        self.call("search_payment_terms")
        self.check(self.pending_debtor is not None, "debtor_editor_missing")
        return tuple(row for row in self.payments if row.name == method)

    def create_payment_term(self, method: str, code: str):
        self.call("create_payment_term")
        self.check(self.pending_debtor is not None, "debtor_editor_missing")
        self.payments.append(PaymentRow(str(len(self.payments)+1), method, code, Decimal("0"), 0, 0))
        self.after("create_payment_term")

    def seed_debtor(self, debtor: Debtor):
        key = str(len(self.debtor_rows)+1)
        self.debtors[key] = debtor
        self.debtor_rows.append(DebtorRow(key, debtor.company, debtor.first_name, debtor.last_name,
                                        debtor.billing.zip, debtor.billing.city))

    def save_debtor(self, editor: str, method: str):
        self.call("save_debtor")
        self.check(self.pending_debtor is not None and editor == "debtor-editor", "debtor_editor_missing")
        self.check(any(row.name == method for row in self.payments), "payment_unavailable")
        self.seed_debtor(self.pending_debtor)
        self.pending_debtor = None
        self.after("save_debtor")

    def search_products_from_order(self, order: Handle, sku: str):
        self.call("search_products_from_order")
        self.assert_order_open(order)
        self.check(self.selector is None, "selector_already_open")
        self.selector = "product"
        if self.hide_saved_master == "product":
            return ()
        return tuple(p.row for p in self.products if sku in p.row.sku)

    def search_vats(self, name: str):
        self.call("search_vats")
        self.check(self.selector is None, "selector_must_be_cancelled")
        if self.hide_saved_master == "vat":
            return ()
        return tuple(row for row in self.vats if row.name == name)

    def create_vat(self, name: str, rate: Decimal):
        self.call("create_vat")
        self.vats.append(VatRow(str(len(self.vats)+1), name, rate, "S"))
        self.after("create_vat")

    def seed_product(self, item: Item, gross: Decimal):
        self.products.append(ProductMaster(ProductRow(str(len(self.products)+1), item.sku, item.description,
                                                      item.vat_percent), gross))

    def create_product(self, order: Handle, item: Item, gross: Decimal, vat: VatRow):
        self.call("create_product")
        self.assert_order_open(order)
        self.check(vat in self.vats and vat.value == item.vat_percent, "vat_not_saved_before_product")
        self.seed_product(item, gross)
        self.after("create_product")

    def select_product(self, order: Handle, row: ProductRow):
        self.call("select_product")
        self.assert_order_open(order)
        self.check(self.selector == "product", "wrong_selector")
        self.pending_product = row
        self.product_selection_count += 1
        self.selector = None

    def complete_line(self, order: Handle, index: int, item: Item, net: Decimal):
        self.call("complete_line")
        self.assert_order_open(order)
        self.check(self.pending_product is not None and self.pending_product.sku == item.sku,
                   "line_without_product_selection")
        self.check(index == len(self.lines) and net == line_net(item), "line_index_or_total_mismatch")
        self.lines.append(item)
        self.pending_product = None

    def order_snapshot(self) -> Snapshot:
        self.check(self.source is not None and self.order is not None and self.selected_debtor is not None,
                   "incomplete_order")
        current = self.source.model_copy(update={"items": tuple(self.lines)})
        return Snapshot(self.order.number, "Order", self.source.external_reference, self.source.order_date,
                        self.selected_debtor.billing, self.selected_debtor.delivery, tuple(self.lines),
                        calculate_totals(current), "Net", "With VAT", Decimal("0"), Decimal("0"), "open",
                        debtor_company=self.selected_debtor.company, debtor_first_name=self.selected_debtor.first_name,
                        debtor_last_name=self.selected_debtor.last_name)

    def read_editor(self, handle: Handle):
        self.call("read_editor")
        if handle == self.order:
            return self.order_snapshot()
        self.check(handle == self.invoice and self.invoice_snapshot is not None, "invoice_editor_missing")
        return self.invoice_snapshot

    def save_order(self, order: Handle):
        self.call("save_order")
        self.assert_order_open(order)
        self.check(("Order", order.number) not in self.documents, "order_saved_twice")
        self.documents[("Order", order.number)] = self.order_snapshot()
        self.after("save_order")

    def find_document_rows(self, kind: str, number: str):
        self.call("find_document_rows")
        if self.hide_document_rows:
            return ()
        return tuple(row for (k, n), row in self.documents.items() if k == kind and n == number)

    def create_followup_invoice(self, order: Handle):
        self.call("create_followup_invoice")
        self.check(("Order", order.number) in self.documents, "followup_before_order_save")
        self.check(self.invoice is None, "invoice_already_created")
        count = sum(kind == "Invoice" for kind, _ in self.documents)
        self.invoice = Handle("invoice-editor", f"I{count+1:05d}")
        self.invoice_snapshot = replace(self.documents[("Order", order.number)],
                                        number=self.invoice.number, kind="Invoice", state="unpaid",
                                        source_order_number=order.number,
                                        invoice_date=date.today(), service_date=date.today())
        self.after("create_followup_invoice")
        return self.invoice

    def apply_invoice_payment(self, invoice: Handle, source: OrderInput):
        self.call("apply_invoice_payment")
        self.check(invoice == self.invoice and self.invoice_snapshot is not None, "invoice_editor_missing")
        self.check(any(row.name == source.payment.method for row in self.payments), "invoice_payment_unavailable")
        paid = source.payment.status == "PAID"
        self.invoice_snapshot = replace(self.invoice_snapshot, payment_method=source.payment.method,
                                        paid=paid, payment_date=source.payment.date if paid else None,
                                        payment_value=source.totals.gross if paid else None,
                                        state="paid" if paid else "unpaid")

    def save_invoice(self, invoice: Handle):
        self.call("save_invoice")
        self.check(invoice == self.invoice and self.invoice_snapshot is not None, "invoice_editor_missing")
        self.check(("Invoice", invoice.number) not in self.documents, "invoice_saved_twice")
        self.documents[("Invoice", invoice.number)] = replace(self.invoice_snapshot)
        self.after("save_invoice")

    def read_saved_document(self, kind: str, number: str):
        self.call("read_saved_document")
        self.check((kind, number) in self.documents, "document_not_saved")
        return self.documents[(kind, number)]


def seed_simulator(sim: Simulator, source: OrderInput, mode: str):
    if mode in {"existing", "mixed"}:
        sim.seed_debtor(source.debtor)
        sim.payments.append(PaymentRow("1", source.payment.method, PAYMENT_CODES[source.payment.method], Decimal("0"), 0, 0))
        selected_items = source.items if mode == "existing" else source.items[:1]
        for item in selected_items:
            if not any(p.row.sku == item.sku for p in sim.products):
                sim.seed_product(item, product_gross(item))
            if not any(v.name == vat_name(item.vat_percent) for v in sim.vats):
                sim.vats.append(VatRow(str(len(sim.vats)+1), vat_name(item.vat_percent), item.vat_percent, "S"))
