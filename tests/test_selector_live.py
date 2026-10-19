import unittest

from PIL import Image, ImageDraw

from fakturama_automation.selector_live_ui import grid_has_rows, debtor_matcher, decide, parse_rows, product_matcher, ground_selector_row
from fakturama_automation.selector_vision import OcrWord
from fakturama_automation.errors import ReviewRequired
from test_order_finalize import SOURCE


class GridEmptinessTests(unittest.TestCase):
    def grid(self, row_text):
        image = Image.new('RGB', (600, 400), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 10, 600, 60), fill=(230, 230, 230))
        draw.rectangle((40, 25, 120, 45), fill='black')     # header text band
        if row_text:
            draw.rectangle((40, 100, 160, 120), fill='black')  # a result row below the header
        draw.rectangle((0, 380, 600, 400), fill=(170, 170, 170))  # light scrollbar footer
        return image

    def test_selected_rows_drawn_light_on_dark_are_rows(self):
        image = self.grid(False)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 90, 600, 130), fill=(0, 100, 200))       # selected row background
        draw.rectangle((40, 100, 160, 120), fill=(250, 250, 250))   # white text on it
        self.assertTrue(grid_has_rows(image))

    def test_faint_grid_lines_are_not_rows(self):
        image = Image.new('RGB', (600, 400), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 14, 160, 34), fill='black')              # header text only, no fill
        for y in range(60, 340, 50):
            draw.line((0, y, 600, y), fill=(192, 192, 192))          # faint row lines
        for x in range(100, 600, 120):
            draw.line((x, 0, x, 340), fill=(192, 192, 192))          # faint column lines
        self.assertFalse(grid_has_rows(image))
        draw.rectangle((40, 110, 160, 130), fill='black')            # a real row of text
        self.assertTrue(grid_has_rows(image))

    def test_selected_rows_directly_under_a_filled_header(self):
        image = Image.new('RGB', (600, 400), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 600, 47), fill=(160, 160, 160))        # filled header bar
        draw.rectangle((40, 14, 160, 34), fill='white')              # header text
        empty = image.copy()
        draw.rectangle((0, 48, 600, 98), fill=(0, 120, 215))         # selected row, no gap below header
        draw.rectangle((40, 60, 160, 85), fill=(250, 250, 250))
        self.assertTrue(grid_has_rows(image))
        self.assertFalse(grid_has_rows(empty))

    def test_empty_grid_is_detected_and_a_row_is_seen(self):
        self.assertFalse(grid_has_rows(self.grid(False)))
        self.assertTrue(grid_has_rows(self.grid(True)))


class SelectorDecisionTests(unittest.TestCase):
    def test_parse_rows(self):
        self.assertEqual(parse_rows("a\tb\r\nc\td\r\n"), [["a", "b"], ["c", "d"]])
        self.assertEqual(parse_rows(""), [])

    def test_product_exact_single_row_is_selected(self):
        m = product_matcher("CHR-ERG-01")
        self.assertEqual(decide([["CHR-ERG-01", "Ergonomic Desk Chair", "250,00"]], m), "SELECT")
        self.assertEqual(decide([["CHR-ERG-010", "Other"]], m), "NOT_FOUND")
        self.assertEqual(decide([], m), "NOT_FOUND")
        self.assertEqual(decide([["CHR-ERG-01"], ["CHR-ERG-01"]], m), "AMBIGUOUS")
        self.assertEqual(decide([["CHR-ERG-01"], ["CHR-ERG-010"]], m), "AMBIGUOUS")

    def test_debtor_requires_company_names_zip_city(self):
        m = debtor_matcher(SOURCE)
        row = ["CUST000001", "Marta", "Klein", "Northstar Office GmbH", "10117", "Berlin"]
        self.assertEqual(decide([row], m), "SELECT")
        self.assertEqual(decide([row[:4] + ["99999", "Berlin"]], m), "NOT_FOUND")
        self.assertFalse(m([row[0], row[2], row[1], *row[3:]]))

    def test_row_click_uses_two_corroborating_cells_when_number_is_unreadable(self):
        row = ['CUST000001', 'Marta', 'Klein', 'Northstar Office GmbH', '10117', 'Berlin']
        words = [OcrWord('Marta', 100, 60, 50, 20, 97), OcrWord('Klein', 200, 60, 50, 20, 96)]
        self.assertEqual(ground_selector_row(words, row), words[0])
        with self.assertRaises(ReviewRequired):
            ground_selector_row(words[:1], row)
        with self.assertRaises(ReviewRequired):
            ground_selector_row(words + [OcrWord('Marta', 100, 90, 50, 20, 97),
                                        OcrWord('Klein', 200, 90, 50, 20, 96)], row)


if __name__ == "__main__":
    unittest.main()


class SettledRowsTests(unittest.TestCase):
    def test_waits_for_the_filter_before_judging(self):
        from unittest.mock import patch
        from fakturama_automation import selector_live_ui as ui
        answers = iter([True, True, False, False, False, False])  # old rows, then the emptied list

        class Grid:
            def capture_as_image(self):
                return None

        with patch.object(ui, 'grid_has_rows', side_effect=lambda image: next(answers)), \
                patch.object(ui.time, 'sleep'):
            self.assertFalse(ui._settled_has_rows(Grid()))

    def test_unsettled_grid_stops(self):
        from itertools import cycle
        from unittest.mock import patch
        from fakturama_automation import selector_live_ui as ui
        flipping = cycle([True, False])

        class Grid:
            def capture_as_image(self):
                return None

        with patch.object(ui, 'grid_has_rows', side_effect=lambda image: next(flipping)), \
                patch.object(ui.time, 'sleep'), patch.object(ui.time, 'monotonic', side_effect=[0, 1, 2, 3, 20, 21, 22]), \
                self.assertRaises(ReviewRequired):
            ui._settled_has_rows(Grid(), timeout=8)


class RealGridCaptureTests(unittest.TestCase):
    """Captures taken from Fakturama 2.2.0 grids; synthetic data only."""

    def test_empty_address_grid_has_no_rows_despite_its_header_border(self):
        from pathlib import Path
        image = Image.open(Path(__file__).parent / 'fixtures' / 'empty-address-grid.png')
        self.assertFalse(grid_has_rows(image))

    def test_selected_rows_under_a_filled_header_are_rows(self):
        from pathlib import Path
        image = Image.open(Path(__file__).parent / 'fixtures' / 'vat-grid-selected-rows.png')
        self.assertTrue(grid_has_rows(image))
