"""Continuous, guarded Order-first orchestration; no blind side-effect retries."""

from dataclasses import dataclass
from typing import Callable, TypeVar

from .domain import OrderInput
from .errors import ReviewRequired
from .finance import line_net, product_gross, validate_order
from .journal import Journal
from .matching import (PAYMENT_CODES, debtor_match, exact_one, payment_match,
                       product_match, vat_match, vat_name)
from .ports import DesktopPort, Handle, Snapshot

T = TypeVar("T")


@dataclass(frozen=True)
class Result:
    order_number: str
    invoice_number: str
    status: str = "VERIFIED"


def verify_contents(snapshot: Snapshot, source: OrderInput, number: str, kind: str):
    if (snapshot.number != number or snapshot.kind != kind
            or snapshot.reference != source.external_reference
            or snapshot.order_date != source.order_date
            or snapshot.billing != source.debtor.billing or snapshot.delivery != source.debtor.delivery
            or (snapshot.debtor_company, snapshot.debtor_first_name, snapshot.debtor_last_name)
            != (source.debtor.company, source.debtor.first_name, source.debtor.last_name)
            or snapshot.items != source.items or snapshot.totals != source.totals
            or snapshot.price_mode != "Net" or snapshot.vat_mode != "With VAT"
            or snapshot.overall_discount != source.order_discount_percent
            or snapshot.shipping_net != source.shipping_net):
        raise ReviewRequired("document_contents_mismatch")


def verify_payment(snapshot: Snapshot, source: OrderInput):
    if snapshot.payment_method != source.payment.method:
        raise ReviewRequired("invoice_payment_method_mismatch")
    if source.payment.status == "PAID":
        if (not snapshot.paid or snapshot.payment_date != source.payment.date
                or snapshot.payment_value != source.totals.gross):
            raise ReviewRequired("invoice_paid_details_mismatch")
    elif snapshot.paid or snapshot.payment_date is not None or snapshot.payment_value not in (None, 0):
        raise ReviewRequired("unpaid_invoice_has_payment")


class Workflow:
    def __init__(self, desktop: DesktopPort, journal: Journal, workspace: str):
        self.desktop, self.journal, self.workspace = desktop, journal, workspace
        self.key = ""
        self.order: Handle | None = None

    def action(self, stage: str, operation: Callable[[], T]) -> T:
        # Intent is durably committed before the operation, including uncertain saves.
        self.journal.event(self.workspace, self.key, stage, "INTENT")
        if self.order is not None:
            self.desktop.assert_order_open(self.order)
        result = operation()
        self.journal.event(self.workspace, self.key, stage, "OBSERVED")
        return result

    def persisted_row(self, handle: Handle, kind: str) -> Snapshot:
        rows = self.desktop.find_document_rows(kind, handle.number)
        if len(rows) != 1:
            raise ReviewRequired("saved_document_missing_or_ambiguous")
        return rows[0]

    def run(self, source: OrderInput) -> Result:
        self.order = None
        validate_order(source)  # All validation precedes desktop mutation and lease acquisition.
        self.key = self.journal.begin(self.workspace, source)
        try:
            self.action("preflight", lambda: self.desktop.preflight(source))
            self.order = self.action("open_order", self.desktop.open_new_order)
            order = self.order
            self.journal.document_numbers(self.workspace, self.key, order=order.number)
            self.action("configure_order", lambda: self.desktop.configure_order(order, source))

            rows = self.action("search_debtor", lambda: self.desktop.search_debtors_from_order(
                order, source.debtor.company or " ".join(filter(None, (source.debtor.first_name, source.debtor.last_name)))))
            debtor = debtor_match(rows, source.debtor)
            if debtor is None:
                self.action("cancel_debtor_selector", self.desktop.cancel_selector)
                editor = self.action("open_debtor", lambda: self.desktop.open_debtor_editor(order, source.debtor))
                options = self.action("debtor_payment_options", lambda: self.desktop.debtor_payment_options(editor))
                selected = exact_one(options, lambda v: v == source.payment.method, "payment_dropdown")
                if selected is None:
                    terms = self.action("search_payment_terms", lambda: self.desktop.search_payment_terms(source.payment.method))
                    if payment_match(terms, source.payment.method) is None:
                        self.action("create_payment", lambda: self.desktop.create_payment_term(
                            source.payment.method, PAYMENT_CODES[source.payment.method]))
                        saved = self.action("verify_payment_term", lambda: self.desktop.search_payment_terms(source.payment.method))
                        if payment_match(saved, source.payment.method) is None:
                            raise ReviewRequired("new_payment_not_persisted")
                self.action("save_debtor", lambda: self.desktop.save_debtor(editor, source.payment.method))
                rows = self.action("reselect_debtor_search", lambda: self.desktop.search_debtors_from_order(
                    order, source.debtor.company or " ".join(filter(None, (source.debtor.first_name, source.debtor.last_name)))))
                debtor = debtor_match(rows, source.debtor)
                if debtor is None:
                    raise ReviewRequired("new_debtor_not_persisted")
            self.action("select_debtor", lambda: self.desktop.select_debtor(order, debtor))
            self.action("verify_addresses", lambda: self.desktop.verify_addresses(order, source.debtor))

            for index, item in enumerate(source.items):
                rows = self.action("search_product", lambda: self.desktop.search_products_from_order(order, item.sku))
                product = product_match(rows, item.sku)
                if product is None:
                    self.action("cancel_product_selector", self.desktop.cancel_selector)
                    vats = self.action("search_vat", lambda: self.desktop.search_vats(vat_name(item.vat_percent)))
                    vat = vat_match(vats, item.vat_percent)
                    if vat is None:
                        self.action("create_vat", lambda: self.desktop.create_vat(vat_name(item.vat_percent), item.vat_percent))
                        vats = self.action("verify_vat", lambda: self.desktop.search_vats(vat_name(item.vat_percent)))
                        vat = vat_match(vats, item.vat_percent)
                        if vat is None:
                            raise ReviewRequired("new_vat_not_persisted")
                    self.action("create_product", lambda: self.desktop.create_product(order, item, product_gross(item), vat))
                    rows = self.action("reselect_product_search", lambda: self.desktop.search_products_from_order(order, item.sku))
                    product = product_match(rows, item.sku)
                    if product is None:
                        raise ReviewRequired("new_product_not_persisted")
                if product.description != item.description or product.vat_percent != item.vat_percent:
                    raise ReviewRequired("conflicting_product_definition")
                self.action("select_product", lambda: self.desktop.select_product(order, product))
                self.action("complete_line", lambda: self.desktop.complete_line(order, index, item, line_net(item)))

            snapshot = self.action("read_order_editor", lambda: self.desktop.read_editor(order))
            verify_contents(snapshot, source, order.number, "Order")
            self.action("save_order", lambda: self.desktop.save_order(order))
            row = self.action("verify_saved_order_row", lambda: self.persisted_row(order, "Order"))
            verify_contents(row, source, order.number, "Order")
            if row.state != "open":
                raise ReviewRequired("source_order_not_open")

            invoice = self.action("followup_invoice", lambda: self.desktop.create_followup_invoice(order))
            self.journal.document_numbers(self.workspace, self.key, invoice=invoice.number)
            before = self.action("read_invoice_editor", lambda: self.desktop.read_editor(invoice))
            verify_contents(before, source, invoice.number, "Invoice")
            if before.source_order_number != order.number or before.invoice_date is None or before.service_date is None:
                raise ReviewRequired("invoice_link_or_proposed_dates_missing")
            self.action("set_invoice_payment", lambda: self.desktop.apply_invoice_payment(invoice, source))
            after = self.action("verify_invoice_editor_payment", lambda: self.desktop.read_editor(invoice))
            verify_contents(after, source, invoice.number, "Invoice")
            verify_payment(after, source)
            if (after.source_order_number, after.invoice_date, after.service_date) != (
                    before.source_order_number, before.invoice_date, before.service_date):
                raise ReviewRequired("invoice_proposed_fields_changed")
            self.action("save_invoice", lambda: self.desktop.save_invoice(invoice))

            invoice_row = self.action("verify_saved_invoice_row", lambda: self.persisted_row(invoice, "Invoice"))
            verify_contents(invoice_row, source, invoice.number, "Invoice")
            expected_state = "paid" if source.payment.status == "PAID" else "unpaid"
            if invoice_row.state != expected_state:
                raise ReviewRequired("invoice_state_mismatch")
            saved = self.action("read_saved_invoice", lambda: self.desktop.read_saved_document("Invoice", invoice.number))
            verify_contents(saved, source, invoice.number, "Invoice")
            verify_payment(saved, source)
            if saved.state != expected_state:
                raise ReviewRequired("persisted_invoice_state_mismatch")
            if (saved.source_order_number, saved.invoice_date, saved.service_date) != (
                    before.source_order_number, before.invoice_date, before.service_date):
                raise ReviewRequired("persisted_invoice_link_or_dates_mismatch")
            final_order = self.action("verify_source_order_final", lambda: self.persisted_row(order, "Order"))
            verify_contents(final_order, source, order.number, "Order")
            if final_order.state != "open":
                raise ReviewRequired("source_order_not_open")
            self.journal.finish(self.workspace, self.key)
            return Result(order.number, invoice.number)
        except BaseException:
            self.journal.mark_review(self.workspace, self.key)
            raise
