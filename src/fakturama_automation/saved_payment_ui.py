"""Read back a reopened payment definition."""

from .debtor_ui import _tab_items
from .errors import ReviewRequired
from .matching import PAYMENT_CODES
from .payment_ui import PaymentEditorSession
from .selector_ui import exactly_one
from .uia import Locator, enabled_within_scope, unique_control


class SavedPaymentSession(PaymentEditorSession):
    def __init__(self, root, method, expected_standard, root_guard):
        if method not in PAYMENT_CODES or not isinstance(expected_standard, str) or not expected_standard.strip():
            raise ReviewRequired("saved_payment_invalid_expectation")
        self.method = method
        super().__init__(root, root_guard)
        if self.standard_name != expected_standard:
            raise ReviewRequired("saved_payment_standard_mismatch")

    def _pane(self):
        self.root_guard()
        tabs = _tab_items(self.root)
        tab = exactly_one([c for c in tabs if c.element_info.name in {self.method, '*' + self.method}],
                          "tab", "saved_payment")
        if tab.element_info.name != self.method:
            raise ReviewRequired("saved_payment_dirty_tab")
        try:
            if not tab.iface_selection_item.CurrentIsSelected or not enabled_within_scope(tab, self.root):
                raise ReviewRequired("saved_payment_tab_not_selected")
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired("saved_payment_selection_unavailable") from None
        for role, names in [("order", {"New Order", "*New Order"}),
                            ("debtor", {"New Debtor", "*New Debtor"})]:
            exactly_one([c for c in tabs if c.element_info.name in names], role, "saved_payment")
        pane = unique_control(self.root, Locator("Pane", name=self.method))
        if pane.element_info.process_id != self.root.element_info.process_id:
            raise ReviewRequired("saved_payment_process_mismatch")
        if hasattr(self, "editor_handle") and pane.handle != self.editor_handle:
            raise ReviewRequired("saved_payment_identity_changed")
        return pane

    def verify(self):
        first = self.observe()
        second = self.observe()
        if first != second:
            raise ReviewRequired("saved_payment_fields_changed")
        values = second['values']
        self._preflight(self.method)
        if any(values[role] != self.method for role in ('name', 'description')):
            raise ReviewRequired("saved_payment_name_description_mismatch")
        if values['code'].rstrip(' ') != PAYMENT_CODES[self.method]:
            raise ReviewRequired("saved_payment_code_mismatch")
        # Re-read after all validation; no field writes or code selection.
        if self.observe() != second:
            raise ReviewRequired("saved_payment_fields_changed")
        return {**second, 'status': 'REOPENED_PAYMENT_DEFINITION_VERIFIED',
                'payment_definition_verified': True, 'persistence_verified': False,
                'note': 'Clean active editor fields match expectations. Save/reopen lifecycle and complete master lookup were not automatically observed.'}

    def prepare(self, *args, **kwargs):
        raise ReviewRequired("saved_payment_read_only_session")
