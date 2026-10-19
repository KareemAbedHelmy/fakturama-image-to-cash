import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fakturama_automation import launcher
from fakturama_automation.errors import ReviewRequired


class LauncherTests(unittest.TestCase):
    def test_window_mode(self):
        self.assertEqual(launcher.parse_window_mode('maximize'), 'maximize')
        self.assertEqual(launcher.parse_window_mode('1920x1080'), (1920, 1080))
        for bad in ('', 'full', '1920*1080', '10x10', None):
            with self.subTest(bad=bad), self.assertRaises(ReviewRequired):
                launcher.parse_window_mode(bad)

    def test_executable_from_environment_must_be_an_absolute_fakturama_exe(self):
        with tempfile.TemporaryDirectory() as folder:
            good = Path(folder) / 'Fakturama.exe'
            good.write_bytes(b'')
            other = Path(folder) / 'notepad.exe'
            other.write_bytes(b'')
            with patch.dict(os.environ, {'FAKTURAMA_EXE': str(good)}):
                self.assertEqual(launcher.find_executable(), good)
            for value in (str(other), 'Fakturama.exe', str(Path(folder) / 'missing' / 'Fakturama.exe')):
                with self.subTest(value=value), patch.dict(os.environ, {'FAKTURAMA_EXE': value}), \
                        self.assertRaises(ReviewRequired):
                    launcher.find_executable()

    def test_running_window_is_reused_without_launching(self):
        window = {'name': 'Fakturama - C:/w', 'handle': 5, 'pid': 9, 'executable': 'Fakturama.exe'}
        with patch.object(launcher, 'main_window', return_value=window), \
                patch.object(launcher.subprocess, 'Popen') as popen:
            self.assertEqual(launcher.ensure_running(), (window, False))
            popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class KeyboardTextTests(unittest.TestCase):
    def test_special_characters_and_spaces_are_escaped(self):
        from fakturama_automation.keyboard_text import escape_literal
        self.assertEqual(escape_literal('A&B (GmbH) +1%'), 'A&B{SPACE}{(}GmbH{)}{SPACE}{+}1{%}')
        self.assertEqual(escape_literal('Straße ~ ^'), 'Straße{SPACE}{~}{SPACE}{^}')

    def test_control_characters_and_empty_text_are_refused(self):
        from fakturama_automation.keyboard_text import escape_literal
        for bad in ('', 'a\tb', 'a\nb', None, 'x' * 201):
            with self.subTest(bad=bad), self.assertRaises(ReviewRequired):
                escape_literal(bad)

    def test_type_into_clicks_selects_and_types_the_escaped_text(self):
        from unittest.mock import Mock
        from fakturama_automation.keyboard_text import type_into
        control, sent = Mock(), []
        type_into(control, 'Acme (EU)', sleep=lambda _: None, send=lambda keys, **kw: sent.append(keys))
        control.click_input.assert_called_once()
        self.assertEqual(sent, ['^a', 'Acme{SPACE}{(}EU{)}'])
