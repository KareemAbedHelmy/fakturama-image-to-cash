"""OCR cross-check of a candidate extraction against the order image.

On the supplied 385x529 image Tesseract reads the form's header
band, customer block, payment row and printed totals, but NOT the item table
(text about 6 px tall) nor reliably phone/street details. So OCR is used as an
independent verifier of the legible fields; item lines are reconciled through
the printed net/VAT/gross totals (finance.validate_order). Regions are
FRACTIONS of the image size for this form template, never screen coordinates.
Critical mismatches stop the run; soft ones are reported for review.
"""

import io
import re
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image, ImageOps

from .domain import OrderInput
from .errors import ReviewRequired

W, H = 385, 529
REGIONS = {
    'header': (24, 95, 360, 112), 'company': (24, 150, 200, 170), 'alias': (24, 174, 200, 190),
    'contact': (200, 150, 360, 200), 'payment': (24, 315, 360, 335), 'totals': (24, 478, 360, 500),
}


def _prepare(png: bytes):
    with Image.open(io.BytesIO(png)) as im:
        rgba = im.convert('RGBA')
    white = Image.new('RGBA', rgba.size, 'white')
    white.alpha_composite(rgba)
    return white.convert('L')


def _ocr_region(gray, box, executable: Path, scale=8):
    sx, sy = gray.width / W, gray.height / H
    crop = gray.crop((int(box[0] * sx), int(box[1] * sy), int(box[2] * sx), int(box[3] * sy)))
    crop = ImageOps.expand(ImageOps.autocontrast(crop.resize((crop.width * scale, crop.height * scale),
                                                              Image.BICUBIC)), 40, 255)
    with tempfile.TemporaryDirectory(prefix='fakturama-ocrv-') as folder:
        path = Path(folder) / 'r.png'
        crop.save(path)
        done = subprocess.run([str(executable), str(path), '-', '--psm', '6'], capture_output=True,
                              text=True, timeout=45, check=False, shell=False)
    if done.returncode != 0:
        raise ReviewRequired('ocr_process_failed')
    return ' '.join(done.stdout.split())


def _norm(text):
    return re.sub(r'[^a-z0-9]', '', text.casefold())


def _score(expected, text):
    """Best fuzzy match of `expected` against any window of `text` (0..1)."""
    e, t = _norm(expected), _norm(text)
    if not e or e in t:
        return 1.0
    best = 0.0
    for i in range(0, max(1, len(t) - len(e) + 1)):
        best = max(best, SequenceMatcher(None, e, t[i:i + len(e)]).ratio())
    return best


def verify_against_image(png: bytes, order: OrderInput, executable: Path):
    if not executable.is_absolute() or not executable.is_file():
        raise ReviewRequired('ocr_executable_requires_absolute_existing_path')
    gray = _prepare(png)
    text = {name: _ocr_region(gray, box, executable) for name, box in REGIONS.items()}
    d = order.debtor
    checks = [  # (field, expected text, region, critical)
        ('external_reference', order.external_reference, 'header', True),
        ('order_date', order.order_date.isoformat(), 'header', True),
        ('currency', order.currency, 'header', True),
        ('source_customer_id', order.source_customer_id or '', 'header', False),
        ('payment_method', order.payment.method, 'payment', True),
        ('payment_status', order.payment.status, 'payment', True),
        ('payment_date', order.payment.date.isoformat() if order.payment.date else '', 'payment', True),
        ('total_net', f'EUR {order.totals.net}', 'totals', True),
        ('total_vat', f'EUR {order.totals.vat}', 'totals', True),
        ('total_gross', f'EUR {order.totals.gross}', 'totals', True),
        ('company', d.company, 'company', False), ('alias', d.alias or '', 'alias', False),
        ('contact_first', d.first_name, 'contact', False), ('contact_last', d.last_name, 'contact', False),
    ]
    rows, critical = [], []
    for field, expected, region, is_critical in checks:
        score = round(_score(expected, text[region]), 2)
        ok = score >= (0.9 if is_critical else 0.75)
        rows.append({'field': field, 'expected': expected, 'ocr_region': region, 'score': score,
                     'match': ok, 'critical': is_critical})
        if is_critical and not ok:
            critical.append(field)
    return {'status': 'OCR_MISMATCH_REVIEW_REQUIRED' if critical else 'OCR_CROSS_CHECK_PASSED',
            'critical_mismatches': critical, 'checks': rows,
            'soft_mismatches': [r['field'] for r in rows if not r['match'] and not r['critical']],
            'unverified_by_ocr': ['item lines (sku/description/qty/unit price/discount/VAT)',
                                  'street/zip/city', 'email/phone'],
            'ocr_text': text}
