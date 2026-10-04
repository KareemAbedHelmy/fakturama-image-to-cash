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
