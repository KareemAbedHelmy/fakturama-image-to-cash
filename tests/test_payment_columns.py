import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.payment_copy_ui import _verify_columns_image
from fakturama_automation.payment_table import PAYMENT_COLUMNS, observe_payment_columns
from fakturama_automation.selector_vision import OcrWord


class PaymentColumnTests(unittest.TestCase):
    def setUp(self):
        data = json.loads((Path(__file__).parent / 'fixtures/payment-list.headers.windows.json').read_text())
        self.words = [OcrWord(**w) for w in data]

    def test_actual_windows_capture_identifies_all_six_headers(self):
        headers = observe_payment_columns(self.words)
        self.assertEqual(tuple(h['label'] for h in headers), PAYMENT_COLUMNS)
        self.assertEqual([w['text'] for w in headers[4]['words']], ['Disc.', 'Days'])

    def test_missing_low_confidence_reordered_or_duplicate_headers_stop(self):
        name = next(w for w in self.words if w.text == 'Name')
        desc = next(w for w in self.words if w.text == 'Description')
        samples = [
            [w for w in self.words if w != name],
            [replace(w, confidence=69) if w == name else w for w in self.words],
            [replace(w, left=desc.left) if w == name else replace(w, left=name.left)
             if w == desc else w for w in self.words],
            self.words + [replace(w, top=w.top + 300) for w in self.words if 90 <= w.top < 125],
        ]
        for words in samples:
            with self.subTest(words=words), self.assertRaisesRegex(ReviewRequired, 'headers_missing'):
                observe_payment_columns(words)

    def test_only_header_typography_and_word_order_are_normalized(self):
        words = [replace(w, text=w.text.upper().replace('.', '')) for w in reversed(self.words)]
        self.assertEqual(tuple(h['label'] for h in observe_payment_columns(words)), PAYMENT_COLUMNS)

    def test_capture_uses_exact_grid_size_and_clean_png(self):
        image = Image.new('RGBA', (3054, 850), (255, 255, 255, 255))
        image.info['private'] = 'metadata'
        grid = SimpleNamespace(rectangle=lambda: SimpleNamespace(left=100, top=200, right=3154, bottom=1050),
                               capture_as_image=Mock(return_value=image))
        def ocr(png, executable):
            self.assertEqual(executable, Path('/tesseract'))
            with Image.open(io.BytesIO(png)) as clean:
                self.assertEqual(clean.mode, 'RGB')
                self.assertEqual(clean.size, image.size)
                self.assertNotIn('private', clean.info)
            return self.words, image.size
        with patch('fakturama_automation.payment_copy_ui.ocr_words', side_effect=ocr):
            self.assertEqual(len(_verify_columns_image(grid, Path('/tesseract'))), 6)

    def test_capture_scale_mismatch_or_failed_ocr_stops(self):
        grid = SimpleNamespace(rectangle=lambda: SimpleNamespace(left=0, top=0, right=100, bottom=100),
                               capture_as_image=Mock(return_value=Image.new('RGB', (99, 100))))
        with patch('fakturama_automation.payment_copy_ui.ocr_words') as ocr:
            with self.assertRaisesRegex(ReviewRequired, 'scale_mismatch'):
                _verify_columns_image(grid, Path('/tesseract'))
            ocr.assert_not_called()
        grid.capture_as_image.return_value = Image.new('RGB', (100, 100))
        with patch('fakturama_automation.payment_copy_ui.ocr_words', side_effect=ReviewRequired('ocr_process_failed')):
            with self.assertRaisesRegex(ReviewRequired, 'ocr_process_failed'):
                _verify_columns_image(grid, Path('/tesseract'))
