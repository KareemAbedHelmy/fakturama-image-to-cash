"""Generate synthetic input from the JSON fixture, never desktop evidence.

Run from the repository root. Requires a local DejaVuSans font or uses Pillow's
fallback. Do not replace the employer's actual image with this test document.
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[1]
data = json.loads((root / "examples/order.synthetic.json").read_text())
im = Image.new("RGB", (1600, 1750), "white")
draw = ImageDraw.Draw(im)
font_root = Path("/usr/share/fonts/truetype/dejavu")


def font(size, bold=False):
    path = font_root / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default(size=size)


def text(x, y, value, size=28, bold=False, color="#213044"):
    draw.text((x, y), value, font=font(size, bold), fill=color)


text(75, 60, "PURCHASE ORDER - SYNTHETIC TEST DATA", 40, True)
text(75, 130, f"Order date: {data['order_date']}")
text(75, 175, f"External reference: {data['external_reference']}")
text(75, 220, f"Currency: {data['currency']}")
debtor = data["debtor"]
billing = debtor["billing"]
text(75, 310, "CUSTOMER / DEBTOR", 30, True)
for index, value in enumerate([
    f"Company: {debtor['company']}",
    f"Contact first name: {debtor['first_name']}    Last name: {debtor['last_name']}",
    f"Alias: {debtor['alias']}",
    f"Billing street: {billing['street']}",
    f"ZIP: {billing['zip']}    City: {billing['city']}    Country: {billing['country']}",
    f"Email: {billing['email']}",
    "Delivery address: identical to billing address",
    "No salutation, telephone or additional address details supplied.",
]):
    text(75, 365+index*45, value)
text(75, 785, "ITEMS (all unit prices and line totals are NET)", 30, True)
for index, item in enumerate(data["items"]):
    y = 850 + index*145
    text(75, y, f"SKU: {item['sku']}    Description: {item['description']}", bold=True)
    text(75, y+47, f"Quantity: {item['quantity']}    Unit net: {item['unit_net']}    VAT: {item['vat_percent']}%")
    text(75, y+92, f"Line discount: {item['discount_percent']}%    Line net total: {item['source_line_net']}")
text(75, 1195, "TOTALS", 30, True)
text(75, 1250, "Overall discount: 0%    Shipping net: 0.00 (free of shipping costs)")
text(75, 1300, f"Total net: {data['totals']['net']}    VAT: {data['totals']['vat']}    Total: {data['totals']['gross']} EUR", bold=True)
text(75, 1400, "PAYMENT", 30, True)
text(75, 1455, f"Payment method: {data['payment']['method']}")
text(75, 1505, f"Paid status: {data['payment']['status']}    Payment date: {data['payment']['date']}")
text(75, 1625, "This is synthetic data for testing. It is not a Fakturama screenshot.", 24, color="#697489")
im.save(root / "examples/order.synthetic.png")
