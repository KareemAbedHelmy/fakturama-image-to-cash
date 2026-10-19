"""Bindings for the New Payment (terms of payment) editor of Fakturama 2.2.0: fill a blank form from the
source order and read it back. Never saves and never changes Standard.
"""

import re
import unicodedata
from decimal import Decimal

from .combo_ui import ComboReadRequired, read_combo_options, select_combo_exact
from .debtor_ui import _tab_items
from .errors import ReviewRequired
from .matching import PAYMENT_CODES
from .selector_ui import exactly_one
from .uia import Locator, enabled_within_scope, process_executable_name, unique_control

PREFIX = "payment_editor"
CODE_LABEL = "!editorPaymentPaymentcode!"
EDITOR_CAPTIONS = {"New Term of Payment", "*New Term of Payment"}
EDIT_FIELDS = {"name": "Name", "account": "Account", "description": "Description",
               "cash_discount": "Cash discount", "discount_days": "Discount Days",
               "net_days": "Net Days", "unpaid_text": "Text 'unpaid'",
               "deposit_text": "Text 'deposit'", "paid_text": "Text 'paid'"}


def _value(control):
    try:
        value = control.iface_value.CurrentValue
    except Exception:
        raise ReviewRequired("payment_editor_value_pattern_unavailable") from None
    if (not isinstance(value, str) or len(value) > 1000
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise ReviewRequired("payment_editor_value_invalid")
    return value


class PaymentEditorSession:
    def __init__(self, root, root_guard=None, *, require_debtor=True):
        self.root = root
        self.require_debtor = require_debtor
        self.root_guard = root_guard
        self.write_uncertain = False
        pane = self._pane()
        if not pane.handle:
            raise ReviewRequired("payment_editor_identity_unavailable")
        self.editor_handle = pane.handle
        self.standard_name = _value(self._standard(pane))
        if not self.standard_name.strip():
            raise ReviewRequired("payment_editor_standard_unavailable")

    def _pane(self):
        if self.root_guard:
            self.root_guard()
        tabs = _tab_items(self.root)
        tab = exactly_one([c for c in tabs if c.element_info.name in EDITOR_CAPTIONS],
                          "tab", PREFIX)
        try:
            if not tab.iface_selection_item.CurrentIsSelected or not enabled_within_scope(tab, self.root):
                raise ReviewRequired("payment_editor_tab_not_selected")
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired("payment_editor_tab_selection_unavailable") from None
        required = [("source_order_tab", {"New Order", "*New Order"})]
        if self.require_debtor:
            required.append(("source_debtor_tab", {"New Debtor", "*New Debtor"}))
        for role, names in required:
            exactly_one([c for c in tabs if c.element_info.name in names], role, PREFIX)
        pane = unique_control(self.root, Locator("Pane", name="New Term of Payment"))
        if pane.element_info.process_id != self.root.element_info.process_id:
            raise ReviewRequired("payment_editor_process_mismatch")
        if hasattr(self, "editor_handle") and pane.handle != self.editor_handle:
            raise ReviewRequired("payment_editor_identity_changed")
        return pane

    def _standard(self, pane):
        label = unique_control(pane, Locator("Text", name="Standard"))
        siblings = label.parent().children()
        index = siblings.index(label)
        if index + 1 >= len(siblings) or siblings[index + 1].element_info.control_type != "Pane":
            raise ReviewRequired("payment_editor_standard_structure_changed")
        group = siblings[index + 1]
        field = exactly_one([c for c in group.children() if c.element_info.control_type == "Edit"
                             and c.is_visible() and enabled_within_scope(c, pane)], "standard", PREFIX)
        button = exactly_one([c for c in group.children() if c.element_info.control_type == "Button"
                              and c.element_info.name == "Set as standard" and c.is_visible()],
                             "standard_button", PREFIX)
        if any(c.element_info.process_id != self.root.element_info.process_id for c in (field, button)):
            raise ReviewRequired("payment_editor_process_mismatch")
        try:
            if not field.iface_value.CurrentIsReadOnly:
                raise ReviewRequired("payment_editor_standard_not_read_only")
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired("payment_editor_standard_pattern_unavailable") from None
        return field

    def fields(self):
        pane = self._pane()
        standard = self._standard(pane)
        if _value(standard) != self.standard_name:
            raise ReviewRequired("payment_editor_standard_changed")
        result = {role: unique_control(pane, Locator("Edit", name=label))
                  for role, label in EDIT_FIELDS.items()}
        result["code"] = unique_control(pane, Locator("ComboBox", name=CODE_LABEL))
        if any(c.element_info.process_id != self.root.element_info.process_id for c in result.values()):
            raise ReviewRequired("payment_editor_process_mismatch")
        return result

    def observe(self):
        return {"status": "OBSERVED_NEW_PAYMENT_EDITOR_ONLY", "standard_name": self.standard_name,
                "source_order_tab_present": True, "source_debtor_tab_present": True,
                "source_order_number_verified": False, "source_debtor_id_verified": False,
                "values": {role: _value(c) for role, c in self.fields().items()},
                "note": "Read-only editor fields; inactive Order/Debtor identities and saved payment terms remain unverified."}

    def code_options(self, native_factory=None):
        report = read_combo_options(self._pane(), self.fields()["code"], native_factory)
        current = self.observe()
        if current["values"]["code"] != report["current_value"]:
            raise ReviewRequired("payment_editor_code_changed_during_read")
        return {**current, "payment_code_options": report}

    def _preflight(self, method):
        fields = self.fields()
        values = {role: _value(c) for role, c in fields.items()}
        for role in ("name", "description"):
            if values[role] not in {"", method}:
                raise ReviewRequired("payment_editor_existing_value_conflict")
            try:
                if fields[role].iface_value.CurrentIsReadOnly:
                    raise ReviewRequired("payment_editor_field_read_only")
            except ReviewRequired:
                raise
            except Exception:
                raise ReviewRequired("payment_editor_value_pattern_unavailable") from None
        discount = values["cash_discount"]
        if not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?%", discount) or Decimal(discount[:-1].replace(",", ".")) != 0:
            raise ReviewRequired("payment_editor_nonzero_or_invalid_discount")
        if any(not re.fullmatch(r"[0-9]+", values[role]) or int(values[role]) != 0
               for role in ("discount_days", "net_days")):
            raise ReviewRequired("payment_editor_nonzero_or_invalid_days")
        if any(values[role] for role in ("account", "unpaid_text", "deposit_text", "paid_text")):
            raise ReviewRequired("payment_editor_unexpected_optional_values")
        return fields

    def prepare(self, method, native_factory=None):
        """Prepare an unsaved form. Committing still requires verified absence.

        Native labels may have the trailing ASCII spaces observed in the capture.
        No case-folding, fuzzy matching or arbitrary whitespace normalization.
        Uncertain mutation permanently blocks reuse of this session instance.
        """
        if self.write_uncertain:
            raise ReviewRequired("payment_editor_write_uncertain_reconcile")
        if not isinstance(method, str) or method not in PAYMENT_CODES:
            raise ReviewRequired("payment_editor_unsupported_method")
        self._preflight(method)
        report = self.code_options(native_factory)
        candidates = [text for text in report["payment_code_options"]["options"]
                      if text.rstrip(" ") == PAYMENT_CODES[method]]
        if len(candidates) != 1:
            raise ReviewRequired("payment_editor_code_missing_or_ambiguous")
        target = candidates[0]
        try:
            select_combo_exact(self._pane(), self.fields()["code"], target, native_factory,
                               guard=lambda: self._preflight(method))
            for role in ("name", "description"):
                fields = self._preflight(method)
                if _value(fields["code"]) != target:
                    raise ReviewRequired("payment_editor_code_changed_before_write")
                control = fields[role]
                if _value(control) == method:
                    continue
                try:
                    control.iface_value.SetValue(method)
                except Exception:
                    raise ReviewRequired("payment_editor_write_uncertain") from None
                if _value(self.fields()[role]) != method:
                    raise ReviewRequired("payment_editor_write_readback_mismatch")
            result = self.observe()
            if (result["values"]["code"] != target or any(result["values"][r] != method
                    for r in ("name", "description"))):
                raise ReviewRequired("payment_editor_prepare_readback_mismatch")
            self._preflight(method)
        except Exception:
            self.write_uncertain = True
            raise
        return {**result, "status": "PAYMENT_EDITOR_PREPARED_UNSAVED",
                "note": "Prepared editor read back only; complete lookup and persistence remain unverified. No save or Standard change."}


def _payment_root(pid, handle):
    import platform
    if type(pid) is not int or type(handle) is not int or min(pid, handle) <= 0:
        raise ReviewRequired("invalid_payment_editor_scope")
    if platform.system() != "Windows":
        raise ReviewRequired("windows_required_for_payment_editor")
    try:
        from pywinauto import Desktop
    except ImportError:
        raise ReviewRequired("windows_dependency_missing") from None
    root = Desktop(backend="uia").window(handle=handle).wrapper_object()
    if (root.handle != handle or root.element_info.process_id != pid or not root.is_visible()
            or root.element_info.class_name != "SWT_Window0"
            or not root.element_info.name.startswith("Fakturama - ")):
        raise ReviewRequired("payment_editor_main_window_identity_mismatch")
    return root


def inspect_payment_editor(pid, handle):
    try:
        return PaymentEditorSession(_payment_root(pid, handle)).code_options()
    except ComboReadRequired as exc:
        return exc.report()


def prepare_payment_editor(pid, handle, method):
    """Operator-scoped live preparation test. No lookup/creation/save claim.

    Blank initial names and the observed default code prevent replaying a
    partially prepared form across new Python processes. No write retry.
    """
    if not isinstance(method, str) or method not in PAYMENT_CODES:
        raise ReviewRequired("payment_editor_unsupported_method")
    root = _payment_root(pid, handle)
    title = root.element_info.name

    def guard():
        try:
            if (root.handle != handle or root.element_info.process_id != pid
                    or root.element_info.class_name != "SWT_Window0"
                    or root.element_info.name != title or not root.is_visible()
                    or not root.is_enabled()):
                raise ReviewRequired("payment_editor_main_window_identity_changed")
            executable = process_executable_name(pid)
            if not isinstance(executable, str) or executable.casefold() != "fakturama.exe":
                raise ReviewRequired("payment_editor_executable_identity_mismatch")
            if not root.is_active():
                raise ReviewRequired("payment_editor_must_remain_in_foreground")
        except ReviewRequired:
            raise
        except Exception:
            raise ReviewRequired("payment_editor_write_scope_unavailable") from None

    session = PaymentEditorSession(root, root_guard=guard)
    initial = session.code_options()
    if (any(initial["values"][role] for role in ("name", "description"))
            or initial["values"]["code"].rstrip(" ") != "Mutually defined"):
        raise ReviewRequired("payment_editor_blank_form_required_reconcile")
    prepared = session.prepare(method)
    final = session.code_options()
    if (final["values"] != prepared["values"] or final["standard_name"] != initial["standard_name"]
            or final["payment_code_options"]["options"] != initial["payment_code_options"]["options"]):
        raise ReviewRequired("payment_editor_final_readback_changed")
    return {**prepared, "payment_code_options": final["payment_code_options"],
            "master_absence_verified": False, "persistence_verified": False,
            "note": "Unsaved form prepared and read back only. No term saved or Standard change. Lookup and persistence remain unverified."}
