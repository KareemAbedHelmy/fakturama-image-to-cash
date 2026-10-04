"""UIA bindings for the New Debtor editor: tabs, Main address fields and the generated customer ID. No Save."""

from .domain import Debtor
from .combo_ui import ComboReadRequired, read_combo_options
from .errors import ReviewRequired
from .selector_ui import exactly_one
from .uia import Locator, enabled_within_scope, unique_control

PREFIX = "debtor_editor"


def _tab_items(scope):
    """Use the unfiltered child traversal of the editor tree.

    SWT reports its selected tab headers invisible. Do not rely on a filtered
    descendants query exposing those headers, and do not accept the first match.
    """
    result, stack, count = [], [(scope, 0)], 0
    while stack:
        control, depth = stack.pop()
        count += 1
        if count > 5000 or depth > 30:
            raise ReviewRequired("debtor_editor_tab_tree_truncated")
        if control.element_info.control_type == "TabItem":
            if control.element_info.process_id != scope.element_info.process_id:
                raise ReviewRequired("debtor_editor_process_mismatch")
            result.append(control)
        try:
            children = control.children()
        except Exception:
            raise ReviewRequired("debtor_editor_tab_tree_unavailable") from None
        if len(children) > 500:
            raise ReviewRequired("debtor_editor_tab_tree_truncated")
        stack.extend((child, depth + 1) for child in reversed(children))
    return result


def _tab_role(name):
    return {"New Debtor": "new_debtor_tab", "Addresses": "addresses_tab",
            "Main address": "main_address_tab", "Miscellaneous": "miscellaneous_tab"}[name]


def _tab_diagnostic(root, code):
    tabs = []
    for tab in _tab_items(root):
        name = tab.element_info.name
        if not isinstance(name, str) or len(name) > 1000:
            raise ReviewRequired("debtor_editor_tab_name_unavailable")
        try:
            selected = bool(tab.iface_selection_item.CurrentIsSelected)
        except Exception:
            selected = None
        tabs.append({"name": name, "visible": tab.is_visible(), "selected": selected})
    return {"status": "REVIEW_REQUIRED", "code": code, "observed_tabs": tabs,
            "note": "Read-only tab evidence; no dropdown read, selection, or application change."}


def _selected(scope, name):
    role = _tab_role(name)
    captions = {"New Debtor", "*New Debtor"} if name == "New Debtor" else {name}
    candidates = [c for c in _tab_items(scope) if c.element_info.name in captions]
    tab = exactly_one(candidates, role, PREFIX)
    try:
        if not tab.iface_selection_item.CurrentIsSelected or not enabled_within_scope(tab, scope):
            raise ReviewRequired(f"debtor_editor_{role}_not_selected")
    except ReviewRequired:
        raise
    except Exception:
        raise ReviewRequired(f"debtor_editor_{role}_selection_unavailable") from None


def _value(control):
    try:
        # These fields include ComboBoxes, whose UIA wrapper has no get_value().
        value = control.iface_value.CurrentValue
    except Exception:
        raise ReviewRequired("debtor_editor_value_unavailable") from None
    if not isinstance(value, str) or len(value) > 1000:
        raise ReviewRequired("debtor_editor_value_unavailable")
    return value


def _pair(scope, label_name):
    label = unique_control(scope, Locator("Text", name=label_name))
    siblings = label.parent().children()
    index = siblings.index(label)
    if index + 1 >= len(siblings) or siblings[index + 1].element_info.control_type != "Pane":
        raise ReviewRequired("debtor_editor_pair_structure_changed")
    pane = siblings[index + 1]
    fields = [c for c in pane.children() if c.element_info.control_type == "Edit"
              and c.is_visible() and enabled_within_scope(c, scope)]
    if len(fields) != 2:
        raise ReviewRequired("debtor_editor_pair_missing_or_ambiguous")
    first, second = fields
    a, b = first.rectangle(), second.rectangle()
    if a.right > b.left or a.right <= a.left or b.right <= b.left:
        raise ReviewRequired("debtor_editor_pair_order_unverified")
    return first, second


class DebtorEditorSession:
    def __init__(self, root):
        self.root = root
        pane = self._pane()
        self.editor_handle = pane.handle
        self.generated_id = _value(unique_control(pane, Locator("Edit", name="Customer ID")))
        if not self.generated_id.strip() or not self.editor_handle:
            raise ReviewRequired("debtor_editor_identity_unavailable")

    def _pane(self):
        _selected(self.root, "New Debtor")
        pane = unique_control(self.root, Locator("Pane", name="New Debtor"))
        if pane.element_info.process_id != self.root.element_info.process_id:
            raise ReviewRequired("debtor_editor_process_mismatch")
        order_tabs = [c for c in _tab_items(self.root)
                      if c.element_info.name in {"*New Order", "New Order"}]
        exactly_one(order_tabs, "source_order_tab", PREFIX)
        if hasattr(self, "editor_handle") and pane.handle != self.editor_handle:
            raise ReviewRequired("debtor_editor_identity_changed")
        if hasattr(self, "generated_id") and _value(unique_control(
                pane, Locator("Edit", name="Customer ID"))) != self.generated_id:
            raise ReviewRequired("debtor_editor_generated_id_changed")
        return pane

class DebtorMainAddressSession(DebtorEditorSession):
    def fields(self):
        pane = self._pane()
        _selected(pane, "Addresses")
        address_tab = exactly_one([c for c in pane.descendants(control_type="Tab")
                                  if c.element_info.name == "Addresses"], "addresses", PREFIX)
        _selected(address_tab, "Main address")
        main = exactly_one([c for c in address_tab.descendants(control_type="Tab")
                            if c.element_info.name == "Main address"], "main_address", PREFIX)
        first, last = _pair(pane, "First Name Last Name")
        zip_code, city = _pair(main, "ZIP - City")
        fields = {"company": unique_control(pane, Locator("Edit", name="Company")),
                  "first_name": first, "last_name": last, "zip": zip_code, "city": city,
                  "country": unique_control(main, Locator("ComboBox", name="Country"))}
        for role, label in [("additional_name", "additional name"), ("street", "Street"),
                            ("specification", "Address specification"), ("district", "district"),
                            ("email", "E-Mail"), ("telephone", "Telephone")]:
            fields[role] = unique_control(main, Locator("Edit", name=label))
        if any(c.element_info.process_id != self.root.element_info.process_id for c in fields.values()):
            raise ReviewRequired("debtor_editor_process_mismatch")
        return fields

    def observe(self):
        fields = self.fields()
        return {"status": "OBSERVED_NEW_DEBTOR_MAIN_ADDRESS_ONLY",
                "generated_id": self.generated_id,
                "source_order_tab_present": True, "source_order_number_verified": False,
                "values": {role: _value(control) for role, control in fields.items()},
                "note": "Order tab presence is not Order-number continuity. Payment and delivery roles remain unverified."}

    def fill_main_fields(self, debtor: Debtor):
        """Literal writes to a new editor only; caller verifies absent master first.

        Country selection is not implemented, so a mismatch stops BEFORE any
        field write. This never changes Customer ID, country, payment or saves.
        A failed SetValue may have succeeded: stop without retry or rollback.
        """
        fields = self.fields()
        if _value(fields["country"]) != debtor.billing.country:
            raise ReviewRequired("debtor_editor_country_selection_required")
        recipient = debtor.billing.recipient_name
        if recipient and recipient not in {debtor.company, " ".join(
                p for p in (debtor.first_name, debtor.last_name) if p)}:
            raise ReviewRequired("debtor_editor_billing_recipient_unmapped")
        desired = {"company": debtor.company, "first_name": debtor.first_name, "last_name": debtor.last_name,
                   "street": debtor.billing.street, "zip": debtor.billing.zip, "city": debtor.billing.city,
                   "additional_name": debtor.billing.additional_name or "",
                   "specification": debtor.billing.specification or "", "district": debtor.billing.district or "",
                   "email": debtor.billing.email or "", "telephone": debtor.billing.telephone or ""}
        # Validate every existing value and pattern before the first mutation.
        for role, target in desired.items():
            if _value(fields[role]) not in {"", target}:
                raise ReviewRequired("debtor_editor_existing_value_conflict")
            try:
                if fields[role].iface_value.CurrentIsReadOnly:
                    raise ReviewRequired("debtor_editor_field_read_only")
            except ReviewRequired:
                raise
            except Exception:
                raise ReviewRequired("debtor_editor_value_pattern_unavailable") from None
        for role, target in desired.items():
            fresh = self.fields()
            if _value(fresh["country"]) != debtor.billing.country:
                raise ReviewRequired("debtor_editor_country_changed")
            control = fresh[role]
            if _value(control) not in {"", target}:
                raise ReviewRequired("debtor_editor_existing_value_conflict")
            if _value(control) == target:
                continue
            try:
                if control.iface_value.CurrentIsReadOnly:
                    raise ReviewRequired("debtor_editor_field_read_only")
                control.iface_value.SetValue(target)
            except ReviewRequired:
                raise
            except Exception:
                raise ReviewRequired("debtor_editor_write_uncertain") from None
            if _value(self.fields()[role]) != target:
                raise ReviewRequired("debtor_editor_write_readback_mismatch")
        result = self.observe()
        if result["values"]["country"] != debtor.billing.country:
            raise ReviewRequired("debtor_editor_country_changed")
        return result


class DebtorMiscSession(DebtorEditorSession):
    def fields(self):
        pane = self._pane()
        _selected(pane, "Miscellaneous")
        page = exactly_one([c for c in pane.descendants(control_type="Tab")
                           if c.element_info.name == "Miscellaneous"], "miscellaneous", PREFIX)
        fields = {"alias": unique_control(page, Locator("Edit", name="Alias name")),
                  "payment": unique_control(page, Locator("ComboBox", name="Payment")),
                  "discount": unique_control(page, Locator("Edit", name="Discount")),
                  "price_preference": unique_control(page, Locator("ComboBox", name="Net or Gross"))}
        if any(c.element_info.process_id != self.root.element_info.process_id for c in fields.values()):
            raise ReviewRequired("debtor_editor_process_mismatch")
        return fields

    def observe(self):
        return {"status": "OBSERVED_NEW_DEBTOR_MISC_ONLY", "generated_id": self.generated_id,
                "source_order_tab_present": True, "source_order_number_verified": False,
                "values": {role: _value(c) for role, c in self.fields().items()}}

    def fill_alias(self, debtor: Debtor):
        """Partial literal alias write; caller verifies missing master first. No save."""
        control = self.fields()["alias"]
        desired = debtor.alias or ""
        current = _value(control)
        if current not in {"", desired}:
            raise ReviewRequired("debtor_editor_alias_conflict")
        if current == desired:
            return self.observe()
        try:
            if control.iface_value.CurrentIsReadOnly:
                raise ReviewRequired("debtor_editor_alias_read_only")
            control.iface_value.SetValue(desired)
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired("debtor_editor_alias_write_uncertain") from None
        result = self.observe()
        if result["values"]["alias"] != desired:
            raise ReviewRequired("debtor_editor_alias_readback_mismatch")
        return result

    def payment_options(self, native_factory=None):
        pane = self._pane()
        report = read_combo_options(pane, self.fields()["payment"], native_factory)
        current = self.observe()
        if current["values"]["payment"] != report["current_value"]:
            raise ReviewRequired("debtor_editor_payment_changed_during_read")
        return {**current, "payment_options": report,
                "note": "Read-only observed names. Payment definitions, live alias writes and Debtor persistence are unverified."}


def inspect_debtor_payment_options(pid: int, handle: int) -> dict:
    import platform
    if type(pid) is not int or type(handle) is not int or min(pid, handle) <= 0:
        raise ReviewRequired("invalid_debtor_inspection_scope")
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_debtor_inspection")
    try:
        from pywinauto import Desktop
    except ImportError:
        raise ReviewRequired("windows_dependency_missing") from None
    root = Desktop(backend="uia").window(handle=handle).wrapper_object()
    if (root.handle != handle or root.element_info.process_id != pid or not root.is_visible()
            or root.element_info.class_name != "SWT_Window0"
            or not root.element_info.name.startswith("Fakturama - ")):
        raise ReviewRequired("debtor_main_window_identity_mismatch")
    try:
        return DebtorMiscSession(root).payment_options()
    except ComboReadRequired as exc:
        return exc.report()
    except ReviewRequired as exc:
        if "_tab_" in exc.code and not any(word in exc.code for word in ("tree", "name")):
            return _tab_diagnostic(root, exc.code)
        raise
