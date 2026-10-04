import sys
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.native_ui import inspect_native_controls, inspect_native_selector


class NativeControl:
    def __init__(self, kind="#32770", children=(), pid=12):
        self.kind, self._children = kind, list(children)
        self.element_info = SimpleNamespace(process_id=pid)
        self.handle = id(self)
        self.text_reads = 0
    def class_name(self): return self.kind
    def children(self): return self._children
    def window_text(self):
        self.text_reads += 1
        return "Select a product" if self.kind == "#32770" else ""
    def is_visible(self): return True
    def is_enabled(self): return True
    def item_count(self): return 2


class NativeSelectorTests(unittest.TestCase):
    def inspect(self, windows):
        self.criteria = None
        def select(**kwargs):
            self.criteria = kwargs
            return windows
        module = SimpleNamespace(Desktop=lambda **_: SimpleNamespace(windows=select))
        with patch.dict(sys.modules, {"pywinauto": module}), \
             patch("fakturama_automation.native_ui.platform.system", return_value="Windows"):
            return inspect_native_selector(12, "Select a product")

    def test_scope_is_pid_and_exact_observed_title(self):
        report = self.inspect([NativeControl(children=[NativeControl("SysListView32")])])
        self.assertEqual(self.criteria, {"process": 12, "title": "Select a product", "visible_only": True})
        self.assertEqual(report["nodes"][1]["native_item_count"], 2)
        self.assertFalse(report["truncated"])

    def test_ambiguous_native_dialog_not_first_match(self):
        with self.assertRaisesRegex(ReviewRequired, "missing_or_ambiguous"):
            self.inspect([NativeControl(), NativeControl()])

    def test_selector_class_must_match_observed_modal(self):
        with self.assertRaisesRegex(ReviewRequired, "class_mismatch"):
            self.inspect([NativeControl("OtherClass")])

    def test_foreign_process_child_not_read(self):
        child = NativeControl("Edit", pid=13)
        with self.assertRaisesRegex(ReviewRequired, "process_mismatch"):
            self.inspect([NativeControl(children=[child])])
        self.assertEqual(child.text_reads, 0)

    def test_deep_tree_truncation_explicit(self):
        child = NativeControl("Static")
        for _ in range(32):child = NativeControl("SWT_Window0", children=[child])
        report = self.inspect([NativeControl(children=[child])])
        self.assertTrue(report["truncated"])
        self.assertLessEqual(len(report["nodes"]), 31)

    def test_invalid_pid_or_unobserved_title_stops_before_loading_backend(self):
        for pid, title in [(True, "Select a product"), (0, "Select a product"), (12, "New product")]:
            with self.assertRaisesRegex(ReviewRequired, "invalid_native_selector_scope"):
                inspect_native_selector(pid, title)

    def test_repeated_descendants_and_cycles_are_read_once(self):
        child = NativeControl('Edit')
        group = NativeControl('SWT_Window0', children=[child])
        root = NativeControl(children=[group, child, group])
        child._children.append(root)
        report = inspect_native_controls(root, 12)
        self.assertEqual(len(report['nodes']), 3)
        self.assertEqual(report['duplicate_entries_skipped'], 3)
        self.assertEqual([c.text_reads for c in (root, group, child)], [1, 1, 1])
        self.assertFalse(report['truncated'])

    def test_duplicate_foreign_pid_is_rejected_before_read_or_skip(self):
        first = NativeControl('Edit')
        foreign = NativeControl('Edit', pid=13)
        foreign.handle = first.handle
        root = NativeControl(children=[first, foreign])
        with self.assertRaisesRegex(ReviewRequired, 'process_mismatch'):
            inspect_native_controls(root, 12)
        self.assertEqual(foreign.text_reads, 0)

    def test_invalid_native_handle_is_rejected_before_text(self):
        for handle in [0, -1, True, '123']:
            root = NativeControl()
            root.handle = handle
            with self.subTest(handle=handle), self.assertRaisesRegex(ReviewRequired, 'invalid_handle'):
                inspect_native_controls(root, 12)
            self.assertEqual(root.text_reads, 0)

    def test_replay_windows_payment_list_deduplicates_222_entries_to_13_controls(self):
        observed = json.loads((Path(__file__).parent / 'fixtures/payment-list.native.windows.json').read_text())
        controls = {}
        for row in observed['nodes']:
            control = NativeControl(row['class_name'], pid=51052)
            control.handle = row['handle']
            control.window_text = lambda r=row, c=control: self.read_observed_text(r, c)
            controls[tuple(row['path'])] = control
        for path, control in controls.items():
            if path:
                controls[path[:-1]]._children.append(control)
        report = inspect_native_controls(controls[()], 51052)
        expected = {r['handle'] for r in observed['nodes']}
        self.assertEqual(len(observed['nodes']), 222)
        self.assertEqual(len(expected), 13)
        self.assertEqual({r['handle'] for r in report['nodes']}, expected)
        self.assertEqual(len(report['nodes']), 13)
        self.assertEqual(sum(c.text_reads for c in controls.values()), 13)
        self.assertGreater(report['duplicate_entries_skipped'], 0)
        self.assertFalse(report['truncated'])

    @staticmethod
    def read_observed_text(row, control):
        control.text_reads += 1
        return row['name']
