"""Read-only binding of the New Order header fields from the current UIA tree. Locators are rebuilt from
each tree; no automation IDs or screen coordinates are stored.
"""

import re
from dataclasses import dataclass

from .errors import ReviewRequired
from .uia import inspect_window


@dataclass(frozen=True)
class Node:
    path: tuple[int, ...]
    data: dict


class Tree:
    def __init__(self, root: dict):
        self.nodes: dict[tuple[int, ...], Node] = {}

        def visit(data, path):
            if len(self.nodes) >= 5000 or len(path) > 30 or data.get("truncated"):
                raise ReviewRequired("order_ui_tree_truncated")
            self.nodes[path] = Node(path, data)
            for index, child in enumerate(data.get("children", [])):
                visit(child, path + (index,))

        visit(root, ())

    def under(self, parent: Node, kind: str, name: str | None = None):
        return [node for path, node in self.nodes.items()
                if path[:len(parent.path)] == parent.path
                and node.data.get("control_type") == kind
                and (name is None or node.data.get("name") == name)]

    def parent(self, node: Node):
        if not node.path:
            raise ReviewRequired("order_ui_structure_changed")
        return self.nodes[node.path[:-1]]

    def effective_enabled(self, node: Node):
        return all(self.nodes[node.path[:depth]].data.get("enabled") is True
                   for depth in range(len(node.path) + 1))


def one(nodes, role: str):
    if len(nodes) != 1:
        raise ReviewRequired(f"order_ui_{role}_missing_or_ambiguous")
    return nodes[0]


def observed_value(node: Node):
    value = node.data.get("patterns", {}).get("value", {}).get("CurrentValue")
    if node.data.get("visible") is not True or not isinstance(value, str):
        raise ReviewRequired("order_ui_value_unavailable")
    return value


def resolve_order_fields(tree: Tree, editor: Node):
    """Bind captured roles by labels and sibling structure, never saved IDs."""
    date_label = one(tree.under(editor, "Text", "Date"), "date_label")
    header = tree.parent(date_label)
    date_container = [child for child in tree.under(header, "Pane")
                      if child.path[:-1] == header.path and child.path[-1] > date_label.path[-1]]
    date_pane = one(date_container, "date_container")
    date_field = one(tree.under(date_pane, "Edit"), "date")
    number_field = one([child for child in tree.under(header, "Edit")
                        if child.path[:-1] == header.path and child.path[-1] < date_label.path[-1]], "number")
    # Ensure this anonymous header is actually the group adjacent to the No. label.
    number_label = one(tree.under(editor, "Text", "No."), "number_label")
    if header.path[:-1] != number_label.path[:-1] or header.path[-1] != number_label.path[-1] + 1:
        raise ReviewRequired("order_ui_number_structure_changed")

    vat_total = one(tree.under(editor, "Edit", "VAT"), "vat_total")
    totals = tree.parent(vat_total)
    children = totals.data.get("children", [])
    if (len(children) < 4 or children[0].get("control_type") != "Text"
            or children[1].get("control_type") != "Edit"
            or children[0].get("name") != children[1].get("name")
            or children[2].get("name") != "Discount"
            or children[2].get("control_type") != "Text"
            or children[3].get("name") != "Discount"
            or children[3].get("control_type") != "Edit"):
        raise ReviewRequired("order_ui_totals_structure_changed")
    return {"number": number_field, "date_text": date_field,
              "price_mode": one(tree.under(header, "ComboBox"), "price_mode"),
              "reference": one(tree.under(editor, "Edit", "Cust.Ref."), "reference"),
              "vat_mode": one(tree.under(editor, "ComboBox", "VAT"), "vat_mode"),
              "discount_text": one(tree.under(editor, "Edit", "Discount"), "discount"),
              "shipping_text": one(tree.under(editor, "ComboBox", "Shipping"), "shipping"),
              "subtotal_text": tree.nodes[totals.path + (1,)],
              "vat_total_text": vat_total,
              "total_text": one(tree.under(editor, "Edit", "Total"), "total")}


def analyze_order_header(root: dict, expected_currency: str) -> dict:
    """Interpret current observations only. This result never authorizes a save."""
    if not re.fullmatch(r"[A-Z]{3}", expected_currency):
        raise ReviewRequired("invalid_expected_currency")
    if (root.get("control_type") != "Window" or root.get("class_name") != "SWT_Window0"
            or root.get("visible") is not True):
        raise ReviewRequired("order_ui_root_not_visible_fakturama_shell")
    tree = Tree(root)
    window = tree.nodes[()]
    editor = one(tree.under(window, "Pane", "New Order"), "editor")
    if not tree.effective_enabled(editor):
        raise ReviewRequired("order_ui_editor_disabled")
    fields = resolve_order_fields(tree, editor)
    values = {role: observed_value(node) for role, node in fields.items()}
    if not values["number"].strip():
        raise ReviewRequired("order_ui_proposed_number_missing")
    codes = []
    for role in ("subtotal_text", "vat_total_text", "total_text"):
        match = re.fullmatch(r"([A-Z]{3})\s*[-+]?[0-9][0-9., ]*", values[role])
        euro = re.fullmatch(r"(?:€\s*[-+]?[0-9][0-9., ]*|[-+]?[0-9][0-9., ]*\s*(?:€|EUR))", values[role])
        codes.append(match.group(1) if match else ("EUR" if euro else None))
    currency = codes[0] if codes[0] and len(set(codes)) == 1 else None
    issues = []
    if currency is None:
        issues.append("display_currency_unverified")
    elif currency != expected_currency:
        issues.append("display_currency_mismatch")

    followup_group = one(tree.under(editor, "Group", "Create a follow-up document"), "followup_group")
    followup = one(tree.under(followup_group, "Button", "Invoice"), "followup_invoice")
    save = one(tree.under(window, "Button", "Save the current contents"), "save")
    item_grids = tree.under(editor, "DataGrid") + tree.under(editor, "Table")
    if not item_grids:
        issues.append("item_grid_not_exposed_in_uia")
    return {"status": "OBSERVED_ORDER_HEADER_ONLY", "values": values,
            "currency": currency, "expected_currency": expected_currency,
            "save_invokable": (tree.effective_enabled(save)
                               and save.data.get("visible") is True
                               and "invoke" in save.data.get("patterns", {})),
            "followup_invoice_invokable": (tree.effective_enabled(followup)
                                           and followup.data.get("visible") is True
                                           and "invoke" in followup.data.get("patterns", {})),
            "accessible_item_grid_count": len(item_grids), "review_issues": issues,
            "note": "Read-only observations. Selectors, item cells, master editors and persistence are still unimplemented."}


def inspect_order_header(pid: int, handle: int, expected_currency: str) -> dict:
    """Read the live window and resolve its header afresh, without side effects."""
    return analyze_order_header(inspect_window(pid, handle)["tree"], expected_currency)
