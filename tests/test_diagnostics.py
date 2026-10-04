import json
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fakturama_automation.diagnostics import capture_bundle, choose_process
from fakturama_automation.errors import ReviewRequired
from fakturama_automation.uia import inspect_window, is_fakturama_candidate, observed_patterns, window_inventory


class DiagnosticsTests(unittest.TestCase):
    def test_hidden_reported_main_window_is_not_an_editor_dump(self):
        root = SimpleNamespace(element_info=SimpleNamespace(process_id=12),
                               is_visible=lambda: False)
        app = SimpleNamespace(connect=lambda **_: app,
                              window=lambda **_: SimpleNamespace(wrapper_object=lambda: root))
        module = SimpleNamespace(Application=lambda **_: app)
        with patch.dict(sys.modules, {"pywinauto": module}), \
             patch("fakturama_automation.uia.platform.system", return_value="Windows"):
            with self.assertRaisesRegex(ReviewRequired, "window_not_visible"):
                inspect_window(12, 99)

    def test_capture_skips_hidden_helper_but_captures_visible_editor(self):
        rows = [{"pid": 12, "handle": 99, "name": ""},
                {"pid": 12, "handle": 100, "name": "Fakturama"}]
        tree = {"pid": 12, "window_handle": 100, "tree": {"visible": True}}
        with TemporaryDirectory() as folder, \
             patch("fakturama_automation.diagnostics.list_process_windows", return_value=rows), \
             patch("fakturama_automation.diagnostics.inspect_window",
                   side_effect=[ReviewRequired("window_not_visible"), tree]):
            bundle = capture_bundle("new-order", Path(folder), requested_pid=12)
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(set(archive.namelist()), {"manifest.json", "window-100.json"})

    def test_capture_does_not_suppress_process_mismatch(self):
        rows = [{"pid": 12, "handle": 99, "name": "Fakturama"}]
        with TemporaryDirectory() as folder, \
             patch("fakturama_automation.diagnostics.list_process_windows", return_value=rows), \
             patch("fakturama_automation.diagnostics.inspect_window",
                   side_effect=ReviewRequired("window_process_mismatch")):
            with self.assertRaisesRegex(ReviewRequired, "window_process_mismatch"):
                capture_bundle("new-order", Path(folder), requested_pid=12)

    def test_native_fakturama_with_blank_title_is_discovered(self):
        window = SimpleNamespace(element_info=SimpleNamespace(name="", process_id=12),
                                 handle=99, is_visible=lambda: True)
        module = SimpleNamespace(Desktop=lambda **_: SimpleNamespace(windows=lambda: [window]))
        with patch.dict(sys.modules, {"pywinauto": module}), \
             patch("fakturama_automation.uia.platform.system", return_value="Windows"), \
             patch("fakturama_automation.uia.process_executable_name", return_value="Fakturama.exe"):
            rows = window_inventory()
        self.assertTrue(rows[0]["candidate"])
        self.assertIsNone(rows[0]["rejection_reason"])
        self.assertEqual(rows[0]["name"], "")

    def test_blank_title_unrelated_java_not_auto_selected(self):
        self.assertFalse(is_fakturama_candidate("", "javaw.exe"))

    def test_inventory_exposes_filter_rejections(self):
        class Window:
            def __init__(self, name, pid):
                self.element_info = SimpleNamespace(name=name, process_id=pid)
                self.handle = pid*10
            def is_visible(self): return True
        windows = [Window("Fakturama", 1), Window("Fakturama", 2),
                   Window("fakturama-image-to-cash - VS Code", 3)]
        desktop = SimpleNamespace(windows=lambda: windows)
        module = SimpleNamespace(Desktop=lambda **_: desktop)
        with patch.dict(sys.modules, {"pywinauto": module}), \
             patch("fakturama_automation.uia.platform.system", return_value="Windows"), \
             patch("fakturama_automation.uia.process_executable_name", side_effect=["Fakturama2.exe", None, "Code.exe"]):
            rows = window_inventory()
        self.assertEqual([r["rejection_reason"] for r in rows],
                         ["executable_not_matched", "executable_identity_unavailable", "executable_not_matched"])
        self.assertEqual(rows[0]["pid"], 1)

    def test_explicit_read_only_pid_does_not_depend_on_auto_filter(self):
        rows = [{"pid": 12, "handle": 99, "name": "Actual App Window"}]
        tree = {"pid": 12, "window_handle": 99, "tree": {"visible": True}}
        with TemporaryDirectory() as folder, \
             patch("fakturama_automation.diagnostics.discover_fakturama_windows") as discover, \
             patch("fakturama_automation.diagnostics.list_process_windows", return_value=rows), \
             patch("fakturama_automation.diagnostics.inspect_window", return_value=tree):
            bundle = capture_bundle("new-order", Path(folder), requested_pid=12)
            self.assertTrue(bundle.is_file())
            discover.assert_not_called()

    def test_invalid_explicit_pid_stops_before_reading_windows(self):
        with patch("fakturama_automation.diagnostics.list_process_windows") as windows:
            for pid in [-1, 0, True]:
                with self.assertRaisesRegex(ReviewRequired, "invalid_process_id"):
                    capture_bundle("new-order", Path("unused"), requested_pid=pid)
            windows.assert_not_called()

    def test_project_titles_do_not_match_editor_explorer_or_browser(self):
        title = "fakturama-image-to-cash - Visual Studio Code"
        for executable in ["Code.exe", "explorer.exe", "chrome.exe", "powershell.exe", "python.exe"]:
            with self.subTest(executable=executable):
                self.assertFalse(is_fakturama_candidate(title, executable))

    def test_fakturama_native_or_java_window_matches(self):
        for executable in ["Fakturama.exe", "javaw.exe", r"C:\Program Files\Java\bin\java.exe"]:
            with self.subTest(executable=executable):
                self.assertTrue(is_fakturama_candidate("Fakturama 2.2.0", executable))

    def test_unknown_executable_or_unrelated_java_title_not_auto_selected(self):
        self.assertFalse(is_fakturama_candidate("Fakturama", None))
        self.assertFalse(is_fakturama_candidate("Other Java Application", "javaw.exe"))

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
