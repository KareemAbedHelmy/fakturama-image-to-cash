"""Sessions for the address and product selector dialogs: search, cancel and exact-match helpers."""

from .errors import ReviewRequired
from .uia import Locator, enabled_within_scope, invoke_once, unique_control


def exactly_one(controls, role, prefix="address_selector"):
    if len(controls) != 1:
        raise ReviewRequired(f"{prefix}_{role}_missing_or_ambiguous")
    return controls[0]


def retained_order_number(root, prefix="address_selector"):
    editors = [c for c in root.descendants(control_type="Pane")
               if c.element_info.name == "New Order" and c.is_visible()]
    editor = exactly_one(editors, "source_order", prefix)
    labels = [c for c in editor.descendants(control_type="Text")
              if c.element_info.name == "No." and c.is_visible()]
    label = exactly_one(labels, "number_label", prefix)
    siblings = label.parent().children()
    index = siblings.index(label)
    if index + 1 >= len(siblings) or siblings[index + 1].element_info.control_type != "Pane":
        raise ReviewRequired(f"{prefix}_number_structure_changed")
    fields = [c for c in siblings[index + 1].children()
              if c.element_info.control_type == "Edit" and c.is_visible()]
    number = exactly_one(fields, "number", prefix).get_value()
    if not isinstance(number, str) or not number.strip():
        raise ReviewRequired(f"{prefix}_number_unavailable")
    return number


class _SelectorSession:
    dialog_title = ""
    prefix = "selector"
    subject = "record"

    def __init__(self, root):
        self.root = root
        self.number = retained_order_number(root, self.prefix)
        self.dialog()

    def dialog(self):
        if retained_order_number(self.root, self.prefix) != self.number:
            raise ReviewRequired(f"{self.prefix}_source_order_changed")
        candidates = [c for c in self.root.descendants(control_type="Window")
                      if c.element_info.name == self.dialog_title
                      and c.element_info.class_name == "#32770"
                      and c.is_visible() and c.is_enabled()]
        dialog = exactly_one(candidates, "dialog", self.prefix)
        if dialog.element_info.process_id != self.root.element_info.process_id:
            raise ReviewRequired(f"{self.prefix}_process_mismatch")
        return dialog

    def search_control(self, dialog):
        label = unique_control(dialog, Locator("Text", name="Search:"))
        candidates = [c for c in label.parent().descendants(control_type="Edit")
                      if c.is_visible() and enabled_within_scope(c, dialog)]
        return exactly_one(candidates, "search", self.prefix)

    def observe(self):
        dialog = self.dialog()
        query = self.search_control(dialog).get_value()
        return {"order_number": self.number, "query": query,
                "result_state": "UNVERIFIED", "rows_accessible": bool(
                    dialog.descendants(control_type="DataGrid")
                    or dialog.descendants(control_type="Table")),
                "note": f"An empty-looking table is not a verified absent {self.subject}."}

    def set_query(self, query: str):
        if not isinstance(query, str) or len(query) > 1024 or any(ord(c) < 32 for c in query):
            raise ReviewRequired(f"{self.prefix}_invalid_query")
        control = self.search_control(self.dialog())
        try:
            pattern = control.iface_value
            if pattern.CurrentIsReadOnly:
                raise ReviewRequired(f"{self.prefix}_search_read_only")
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired(f"{self.prefix}_value_pattern_unavailable") from None
        try:
            pattern.SetValue(query)  # Literal text; no keyboard/hotkey interpolation.
        except Exception:
            raise ReviewRequired(f"{self.prefix}_query_write_uncertain") from None
        if self.search_control(self.dialog()).get_value() != query:
            raise ReviewRequired(f"{self.prefix}_query_readback_mismatch")
        # Query readback is not a search-completion signal.
        return self.observe()

    def read_rows(self):
        observation = self.observe()
        code = (f"{self.prefix}_result_reader_not_implemented" if observation["rows_accessible"]
                else f"{self.prefix}_rows_not_accessible")
        raise ReviewRequired(code)

    def cancel(self):
        invoke_once(self.dialog(), Locator("Button", name="Cancel"))
        if retained_order_number(self.root, self.prefix) != self.number:
            raise ReviewRequired(f"{self.prefix}_source_order_changed")


class AddressSelectorSession(_SelectorSession):
    dialog_title = "Select the address"
    prefix = "address_selector"
    subject = "customer"


class ProductSelectorSession(_SelectorSession):
    dialog_title = "Select a product"
    prefix = "product_selector"
    subject = "product"
