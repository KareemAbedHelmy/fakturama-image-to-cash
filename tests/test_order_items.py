import copy
import unittest
from decimal import Decimal
from unittest import mock
from types import SimpleNamespace
from PIL import Image

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.order_items_ui import complete_line, copied_lines, ground_cells, grid_words, ItemGridDriver
from fakturama_automation.selector_vision import OcrWord
from fakturama_automation.finance import calculate_totals
from test_order_finalize import SOURCE


def expected(item):
    return {key: getattr(item, key) for key in
            ('sku', 'quantity', 'unit_net', 'vat_percent', 'discount_percent', 'source_line_net')}


class Driver:
    def __init__(self, rows, commit=True):
        self.rows, self.writes, self.commit = rows, [], commit

    def read(self):
        return copy.deepcopy(self.rows)

    def write(self, role, value):
        self.writes.append((role, value))
        if self.commit:
            row = self.rows[-1]
            row[role] = value
            row['source_line_net'] = (row['quantity'] * row['unit_net'] *
                                      (1 - row['discount_percent'] / 100)).quantize(Decimal('.01'))


class LineTests(unittest.TestCase):
    def test_applies_extracted_quantity_and_discount(self):
        driver = Driver([{**expected(SOURCE.items[0]), 'quantity': Decimal(1),
                          'discount_percent': Decimal(0), 'source_line_net': Decimal(250)}])
        result = complete_line(SOURCE, 1, driver, {})
        self.assertEqual(driver.writes, [('quantity', Decimal(2)), ('discount_percent', Decimal(10))])
        self.assertEqual(result['values']['source_line_net'], '450.00')

    def test_second_line_and_zero_discount_reset(self):
        driver = Driver([expected(SOURCE.items[0]), {**expected(SOURCE.items[1]),
                          'quantity': Decimal(1), 'discount_percent': Decimal(10)}])
        complete_line(SOURCE, 2, driver, {})
        self.assertEqual(driver.writes, [('quantity', Decimal(3)), ('discount_percent', Decimal(0))])
        self.assertEqual(driver.rows[0], expected(SOURCE.items[0]))

    def test_existing_product_price_overridden_on_line(self):
        driver = Driver([{**expected(SOURCE.items[0]), 'unit_net': Decimal(300)}])
        complete_line(SOURCE, 1, driver, {})
        self.assertEqual(driver.writes, [('unit_net', Decimal(250))])

    def test_rounded_master_price_line_total_is_made_exact(self):
        item = SOURCE.items[0]
        driver = Driver([{**expected(item), 'source_line_net': item.source_line_net + Decimal('0.01')}])
        complete_line(SOURCE, 1, driver, {})
        self.assertEqual(driver.writes, [('unit_net', item.unit_net)])
        self.assertEqual(driver.rows[0]['source_line_net'], item.source_line_net)

    def test_matching_line_has_no_writes(self):
        driver = Driver([expected(SOURCE.items[0])])
        complete_line(SOURCE, 1, driver, {})
        self.assertEqual(driver.writes, [])

    def test_wrong_count_sku_or_vat_blocks_writes(self):
        for rows in ([], [expected(SOURCE.items[1])],
                     [{**expected(SOURCE.items[0]), 'vat_percent': Decimal(7)}],
                     [expected(SOURCE.items[0]), expected(SOURCE.items[1])]):
            driver = Driver(rows)
            with self.subTest(rows=rows), self.assertRaises(ReviewRequired):
                complete_line(SOURCE, 1, driver, {})
            self.assertEqual(driver.writes, [])

    def test_failed_commit_stops_without_retry(self):
        driver = Driver([{**expected(SOURCE.items[0]), 'quantity': Decimal(1)}], commit=False)
        with self.assertRaises(ReviewRequired):
            complete_line(SOURCE, 1, driver, {})
        self.assertEqual(len(driver.writes), 1)

    def test_repeated_sku_uses_source_row_and_fractional_quantity(self):
        item = SOURCE.items[0].model_copy(update={'quantity': Decimal('1.5'),
            'discount_percent': Decimal('12.5'), 'source_line_net': Decimal('328.13')})
        source = SOURCE.model_copy(update={'items': (SOURCE.items[0], item)})
        source = source.model_copy(update={'totals': calculate_totals(source)})
        driver = Driver([expected(SOURCE.items[0]), {**expected(item), 'quantity': Decimal(1),
                                                  'discount_percent': Decimal(0)}])
        # Use the application's cent rounding for the fractional test.
        from fakturama_automation.finance import cents
        original_write = driver.write
        def write(role, value):
            original_write(role, value)
            row = driver.rows[-1]
            row['source_line_net'] = cents(row['quantity'] * row['unit_net'] *
                                           (1 - row['discount_percent'] / 100))
        driver.write = write
        complete_line(source, 2, driver, {})
        self.assertEqual(driver.rows[0], expected(SOURCE.items[0]))
        self.assertEqual(driver.rows[1], expected(item))

    def test_discount_sign_and_fraction_in_clipboard(self):
        row = copied_lines('2.00\tCHR-ERG-01\tnull\tChair\t\tVAT taxValue: [0.19] name: [VAT 19%]\tEUR 250.0\t-0.1\tEUR 450.0')
        self.assertEqual(row, [expected(SOURCE.items[0])])
        with self.assertRaises(ReviewRequired):
            copied_lines('2\tSKU')

    def test_targets_move_with_headers_and_row(self):
        labels = ('Pos.', 'Qty.', 'Item', 'Picture', 'Name', 'Description', 'VAT', 'U.Price', 'Discount', 'Price')
        def words(dx, dy):
            return [OcrWord(label, dx+i*150, dy, 80, 20, 95) for i, label in enumerate(labels)] + [
                OcrWord('CHR-ERG-01', dx+300, dy+50, 130, 20, 95)]
        a, b = ground_cells(words(0, 0), 'CHR-ERG-01'), ground_cells(words(37, 53), 'CHR-ERG-01')
        self.assertEqual(b, {k: (x+37, y+53) for k, (x, y) in a.items()})
        with self.assertRaises(ReviewRequired):
            ground_cells(words(0, 0), 'CHR-ERG-010')
        with self.assertRaises(ReviewRequired):
            ground_cells(words(0, 0), 'CHR-ERG-01', ['CHR-ERG-01', 'CHR-ERG-01'])
        repeated = words(0, 0) + [OcrWord('CHR-ERG-01', 300, 100, 130, 20, 95)]
        target = ground_cells(repeated, 'CHR-ERG-01', ['CHR-ERG-01', 'CHR-ERG-01'])
        self.assertEqual(target['quantity'][1], 110)

    def test_selected_sku_ocr_is_recovered_without_duplicate_rows(self):
        raw = [OcrWord('CHR-ERG-01', 300, 50, 130, 20, 62),
               OcrWord('Qty.', 110, 10, 40, 20, 96)]
        contrast = [OcrWord('CHR-ERG-01', 301, 50, 130, 20, 91),
                    OcrWord('MAT-DESK-02', 301, 100, 130, 20, 93)]
        with mock.patch('fakturama_automation.order_items_ui.ocr_words',
                        side_effect=[(raw, (600, 160)), (contrast, (600, 160))]):
            result = grid_words(Image.new('RGB', (600, 160)), None, ['CHR-ERG-01', 'MAT-DESK-02'])
        self.assertEqual([w.text for w in result].count('CHR-ERG-01'), 1)
        self.assertIn(contrast[1], result)
        self.assertIn(raw[1], result)

    def test_highlighted_third_row_of_a_real_capture_is_read(self):
        from pathlib import Path
        tesseract = Path('C:/Program Files/Tesseract-OCR/tesseract.exe')
        if not tesseract.is_file():
            self.skipTest('Tesseract is not installed')
        image = Image.open(Path(__file__).parent / 'fixtures' / 'item-grid-three-rows-selected.png').convert('RGB')
        skus = ['CHR-ERG-01', 'MAT-DESK-02', 'CHR-ERG-01']
        cells = ground_cells(grid_words(image, tesseract, skus), skus[-1], skus)
        self.assertGreater(cells['position'][1], 150)  # the third row, below rows one and two

    def test_numeric_editor_commits_on_leaving_cell_after_enter(self):
        driver = object.__new__(ItemGridDriver)
        bounds = SimpleNamespace(left=0, top=0, right=100, bottom=100)
        field = mock.Mock(handle=12)
        field.element_info = SimpleNamespace(class_name='Edit', process_id=1)
        field.iface_value = SimpleNamespace(CurrentIsReadOnly=False, CurrentValue='1.00')
        field.rectangle.return_value = bounds
        field.is_visible.return_value = True
        driver.root = SimpleNamespace(handle=10, element_info=SimpleNamespace(process_id=1))
        driver.grid = mock.Mock()
        driver.grid.rectangle.return_value = bounds
        driver.grid.descendants.return_value = [field]
        driver.cells = {'quantity': (50, 50), 'position': (5, 50)}
        driver.guard = mock.Mock()
        driver.clipboard = SimpleNamespace(assert_focus=mock.Mock())
        actions = []
        def keys(text):
            actions.append(text)
            if text == '2':
                field.iface_value.CurrentValue = text
            # Enter alone deliberately leaves the editor visible, as SWT can.
        def click(role):
            actions.append(role)
            if role == 'position':
                driver.grid.descendants.return_value = []
        driver.keys, driver.click_cell = keys, click
        with mock.patch('fakturama_automation.order_items_ui.time.sleep'):
            driver.write('quantity', Decimal(2))
        self.assertEqual(actions, ['quantity', '^a', '2', '{ENTER}', 'position'])


if __name__ == '__main__':
    unittest.main()
