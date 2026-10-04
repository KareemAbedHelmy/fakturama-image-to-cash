import unittest

from fakturama_automation.errors import ReviewRequired
from fakturama_automation.selector_vision import OcrWord
from fakturama_automation.tab_navigation_ui import tab_caption_point


class TabCaptionTests(unittest.TestCase):
    def word(self, text, x, y=10):
        return OcrWord(text, x, y, len(text) * 8, 20, 92)

    def test_grounds_unsaved_order_caption_in_current_image(self):
        words = [self.word('*New', 15), self.word('Order', 60), self.word('New', 200),
                 self.word('Debtor', 245)]
        self.assertEqual(tab_caption_point(words, 'New Order'), (57, 20))

    def test_rejects_duplicate_caption(self):
        words = [self.word('New', 10), self.word('Order', 50),
                 self.word('New', 200), self.word('Order', 240)]
        with self.assertRaises(ReviewRequired):
            tab_caption_point(words, 'New Order')

    def test_rejects_words_on_different_lines(self):
        with self.assertRaises(ReviewRequired):
            tab_caption_point([self.word('New', 10), self.word('Order', 50, 100)], 'New Order')

    def test_document_number_ocr_requires_exact_digits(self):
        words = [OcrWord('PoO000004', 240, 18, 130, 21, 26),
                 OcrWord('PoO000003', 430, 18, 130, 21, 28)]
        self.assertEqual(tab_caption_point(words, 'PO000004'), (305, 28))
        with self.assertRaises(ReviewRequired):
            tab_caption_point(words, 'PO000005')


if __name__ == '__main__':
    unittest.main()
