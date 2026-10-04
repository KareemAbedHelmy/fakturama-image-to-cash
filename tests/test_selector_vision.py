import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.selector_ui import ProductSelectorSession
from fakturama_automation.selector_vision import (
    TSV_HEADER, OcrWord, capture_grid, capture_selector_grid, observe_headers, ocr_words, parse_tsv,
)
from test_selector_ui import Wrapper
import json


def tsv(*rows):
    return (TSV_HEADER + "\n1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t\n" +
            "\n".join(rows) + "\n").encode()


def headers(top=10, offset=0):
    names = ("Item", "No.", "Name", "Description", "Stock", "Price", "VAT")
    return [OcrWord(name, offset + i * 100, top, 50, 15, 95) for i, name in enumerate(names)]


class SelectorVisionTests(unittest.TestCase):
    def test_actual_product_screenshot_header_words(self):
        data = (Path(__file__).parent / 'fixtures/product-selector.headers.tsv').read_bytes()
        report = observe_headers(parse_tsv(data, (2048,1009)), 'Select a product')
        self.assertEqual(len(report['headers']), 6)
        self.assertFalse(report['absence_verified'])

    def test_tsv_quote_is_literal_and_does_not_swallow_following_rows(self):
        rows = parse_tsv(tsv('5\t1\t1\t1\t1\t1\t5\t5\t10\t10\t90\t"',
                             '5\t1\t1\t1\t1\t2\t20\t5\t50\t10\t95\tCHR-ERG-01'), (100,100))
        self.assertEqual([r.text for r in rows], ['"', 'CHR-ERG-01'])

    def test_invalid_confidence_coordinates_and_page_rejected(self):
        for confidence, x, page in [('nan', '5', '1'), ('inf', '5', '1'), ('101', '5', '1'),
                                     ('95', '-1', '1'), ('95', '95', '1'), ('95', '5', '2')]:
            with self.subTest(confidence=confidence, x=x, page=page):
                with self.assertRaisesRegex(ReviewRequired, 'invalid_ocr_tsv'):
                    parse_tsv(tsv(f'5\t{page}\t1\t1\t1\t1\t{x}\t5\t10\t10\t{confidence}\tName'), (100,100))

    def test_empty_candidates_never_verify_absence(self):
        report = observe_headers(headers(), 'Select a product')
        self.assertEqual([h['label'] for h in report['headers']],
                         ['Item No.', 'Name', 'Description', 'Stock', 'Price', 'VAT'])
        self.assertFalse(report['absence_verified'])
        self.assertFalse(report['search_complete'])
        self.assertFalse(report['all_rows_enumerated'])

    def test_headers_may_be_merged_or_split_but_not_fuzzily_corrected(self):
        words = headers()
        words[0] = OcrWord('ItemNo.', 0, 10, 50, 15, 95)
        del words[1]
        self.assertEqual(len(observe_headers(words, 'Select a product')['headers']), 6)
        words[-1] = OcrWord('VAI', 600, 10, 50, 15, 95)
        with self.assertRaisesRegex(ReviewRequired, 'headers_missing'):
            observe_headers(words, 'Select a product')

    def test_low_confidence_duplicate_or_non_aligned_headers_rejected(self):
        low = headers()
        low[-1] = OcrWord('VAT', 600, 10, 50, 15, 69)
        wrong_band = headers()
        wrong_band[-1] = OcrWord('VAT', 600, 60, 50, 15, 95)
        for words in [low, wrong_band, headers() + headers(60)]:
            with self.assertRaisesRegex(ReviewRequired, 'headers_missing'):
                observe_headers(words, 'Select a product')

    def test_ocr_launch_is_literal_no_shell_and_failed_output_is_sanitized(self):
        image = io.BytesIO()
        Image.new('RGB',(100,100)).save(image,format='PNG')
        with patch('fakturama_automation.selector_vision.subprocess.run',
                   return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaisesRegex(ReviewRequired, 'ocr_process_failed'):
                ocr_words(image.getvalue(), Path(__file__).resolve())
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertEqual(run.call_args.args[0][-3:], ['--psm','11','tsv'])

    def test_non_windows_capture_is_blocked(self):
        with patch('fakturama_automation.selector_vision.platform.system', return_value='Linux'):
            with self.assertRaisesRegex(ReviewRequired,'windows_required'):
                capture_selector_grid(51052,658798,'Select a product')


class SelectorCaptureTests(unittest.TestCase):
    def setUp(self):
        tree = json.loads((Path(__file__).parent / 'fixtures/product-selector.uia.json').read_text())
        self.root = Wrapper(tree)
        for i, control in enumerate(self.root.descendants()):
            control.handle = i + 1
            control.rectangle = lambda c=control: SimpleNamespace(**dict(zip(
                ('left','top','right','bottom'),c.data['rect'])))
        self.session = ProductSelectorSession(self.root)
        self.dialog = self.session.dialog()
        bar = self.dialog.descendants('ScrollBar')[0]
        self.pane = bar.parent()
        r = self.pane.data['rect']
        self.size = (r[2]-r[0],r[3]-r[1])
        self.capture = unittest.mock.Mock(return_value=Image.new('RGB', self.size))
        self.pane.capture_as_image = self.capture

    def test_capture_has_no_focus_typing_or_click_and_keeps_query_unverified(self):
        png, report = capture_grid(self.session, lambda: self.dialog.handle)
        self.assertEqual(Image.open(io.BytesIO(png)).size, self.size)
        self.assertEqual(report['order_number'], 'PO000001')
        self.assertEqual(report['result_state'], 'UNVERIFIED')
        self.assertEqual(sum(c.invocations + len(c.writes) for c in self.root.descendants()), 0)

    def test_background_selector_is_not_captured(self):
        with self.assertRaisesRegex(ReviewRequired,'foreground'):
            capture_grid(self.session, lambda: 999999)
        self.capture.assert_not_called()

    def test_focus_query_or_rectangle_change_during_capture_discards_frame(self):
        for change in ('focus', 'query', 'rect'):
            self.setUp()
            foreground = [self.dialog.handle]
            def capture():
                if change == 'focus': foreground[0] = 999999
                elif change == 'query':
                    self.session.search_control(self.dialog).data['patterns']['value']['CurrentValue'] = 'other'
                else: self.pane.data['rect'][0] += 1
                return Image.new('RGB',self.size)
            self.pane.capture_as_image = capture
            with self.subTest(change=change):
                with self.assertRaises(ReviewRequired):
                    capture_grid(self.session,lambda:foreground[0])

    def test_scale_mismatch_blocks_image(self):
        self.pane.capture_as_image = lambda: Image.new('RGB',(100,100))
        with self.assertRaisesRegex(ReviewRequired,'scale_mismatch'):
            capture_grid(self.session,lambda:self.dialog.handle)

    def test_duplicate_table_scrollbar_is_not_arbitrarily_chosen(self):
        bar = self.dialog.descendants('ScrollBar')[0]
        bar.parent()._children.append(Wrapper(bar.data,bar.parent()))
        with self.assertRaisesRegex(ReviewRequired,'horizontal_scrollbar_missing_or_ambiguous'):
            capture_grid(self.session,lambda:self.dialog.handle)
        self.capture.assert_not_called()
