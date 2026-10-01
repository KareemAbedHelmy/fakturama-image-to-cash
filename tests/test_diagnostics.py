import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fakturama_automation.diagnostics import capture_bundle, choose_process
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.uia import observed_patterns


class DiagnosticsTests(unittest.TestCase):
    def test_choose_process_requires_single_or_explicit_match(self):
        rows = [{"pid": 12}, {"pid": 12}]
        self.assertEqual(choose_process(rows, None), 12)
        with self.assertRaisesRegex(ReviewRequired, "fakturama_window_not_found"):
            choose_process([], None)
        with self.assertRaisesRegex(ReviewRequired, "multiple_fakturama_processes_choose_pid"):
            choose_process([{ "pid": 12}, {"pid": 13}], None)
        with self.assertRaisesRegex(ReviewRequired, "pid_not_identified_as_fakturama"):
            choose_process(rows, 13)

    def test_capture_only_selected_process_files_no_directory_leak(self):
        rows = [{"pid": 12, "handle": 99, "name": "Fakturama"}]
        tree = {"pid": 12, "window_handle": 99, "tree": {"visible": True, "name": "New Order"}}
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"unrelated-secret.txt").write_text("never include")
            with patch("fakturama_automation.diagnostics.discover_fakturama_windows", return_value=rows), \
                 patch("fakturama_automation.diagnostics.list_process_windows", return_value=rows), \
                 patch("fakturama_automation.diagnostics.inspect_window", return_value=tree):
                bundle = capture_bundle("new-order", root)
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(set(archive.namelist()), {"manifest.json", "window-99.json"})
                self.assertEqual(json.loads(archive.read("manifest.json"))["pid"], 12)

    def test_invalid_label_no_discovery(self):
        for label in ["../escape", "x/y", "bad label", "UPPER", ""]:
            with patch("fakturama_automation.diagnostics.discover_fakturama_windows") as discover:
                with self.assertRaisesRegex(ReviewRequired, "invalid_capture_label"):
                    capture_bundle(label, Path("unused"))
                discover.assert_not_called()

    def test_pattern_inspection_reads_and_never_invokes(self):
        class InvokePattern:
            def Invoke(self):
                raise AssertionError("must not invoke")
        control = SimpleNamespace(iface_value=SimpleNamespace(CurrentValue="Order Reference", CurrentIsReadOnly=False),
                                  iface_invoke=InvokePattern())
        observed = observed_patterns(control)
        self.assertEqual(observed["value"]["CurrentValue"], "Order Reference")
        self.assertIn("invoke", observed)
