"""Render a styled 'sales order input' form image from a reviewed order JSON.

  .\.venv\Scripts\python.exe tools\make_form_image.py examples/order.demo-new-2.json examples/order.demo-new-2.png

The layout imitates the supplied order (header band, boxed sections, item table). The data comes only from
the JSON, so the image can serve as an unseen input for extraction tests; the JSON is the ground truth.
Drawn at twice the size and scaled down to 615x847, the size of the clear rendering of the supplied order.
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

S = 2
W, H = 615, 847
NAVY, BLUE, GRAY, INK, BOX, LINE, GREEN = "#0f2a47", "#2b6cb0", "#6b7787", "#1c2a3a", "#eaf0f7", "#c5d0de", "#2f855a"
FONTS = Path("C:/Windows/Fonts")


def font(size, bold=False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), ("arialbd.ttf" if bold else "arial.ttf")):
        if (FONTS / name).is_file():
            return ImageFont.truetype(str(FONTS / name), int(size * S))
    return ImageFont.load_default(size=int(size * S))


def render(data):
    im = Image.new("RGB", (W * S, H * S), "white")
    draw = ImageDraw.Draw(im)

    def text(x, y, value, size=9, bold=False, color=INK, anchor="la"):
        draw.text((x * S, y * S), str(value), font=font(size, bold), fill=color, anchor=anchor)

    def box(x0, y0, x1, y1, fill=BOX, outline=LINE, radius=4):
        draw.rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), radius=radius * S, fill=fill, outline=outline,
                               width=S)

    def rule(x0, y0, x1, y1, color=LINE):
        draw.line((x0 * S, y0 * S, x1 * S, y1 * S), fill=color, width=S)

    def label(x, y, value):
        text(x, y, value.upper(), 6.5, True, GRAY)

    d, billing, delivery = data["debtor"], data["debtor"]["billing"], data["debtor"]["delivery"]
    contact = f"{d['first_name']} {d['last_name']}"

    draw.rectangle((0, 0, W * S, 48 * S), fill=NAVY)
    text(40, 16, "TJM Labs  |  Automation Assessment", 10, True, "white")
    text(575, 19, "SYNTHETIC TEST DATA", 7, True, "white", "ra")
    text(40, 66, "SALES ORDER INPUT", 24, True, NAVY)
    text(40, 98, "Source document - extract all transaction values from this image", 8.5, False, GRAY)
    box(512, 66, 580, 88, "#e6f4ea", GREEN, 11)
    text(546, 70, data["payment"]["status"], 9, True, GREEN, "ma")

    text(40, 121, "ORDER", 9, True, BLUE)
    box(40, 133, 580, 196)
    for x, name, value, bold in ((52, "External reference", data["external_reference"], True),
                                 (202, "Order date", data["order_date"], False),
                                 (333, "Customer ID", data["source_customer_id"], False),
                                 (448, "Currency", data["currency"], True)):
        label(x, 148, name)
        text(x, 160, value, 10.5, bold)

    text(40, 213, "CUSTOMER AND CONTACT", 9, True, BLUE)
    box(40, 227, 580, 320, "white")
    rule(310, 233, 310, 314)
    label(52, 243, "Company")
    text(52, 253, d["company"], 10.5, True)
    label(52, 279, "Customer alias")
    text(52, 290, d["alias"], 10)
    label(325, 239, "Contact name")
    text(325, 250, contact, 10)
    label(325, 266, "Email")
    text(325, 277, billing["email"], 9.5)
    label(325, 297, "Phone")
    text(325, 308, billing["telephone"], 9.5)

    text(40, 340, "ADDRESSES", 9, True, BLUE)
    box(40, 350, 580, 462, "white")
    rule(310, 356, 310, 456)
    for x, name, a in ((52, "Billing address", billing), (325, "Delivery address", delivery)):
        label(x, 366, name)
        for i, line in enumerate((a["recipient_name"], a["street"], f"{a['zip']} {a['city']}", a["country"])):
            text(x, 382 + i * 16, line, 9.5)

    text(40, 481, "PAYMENT", 9, True, BLUE)
    box(40, 493, 580, 551)
    pay = data["payment"]
    for x, name, value, bold in ((52, "Payment method", pay["method"], True), (277, "Paid status", pay["status"], True),
                                 (409, "Payment date", pay["date"] or "-", False)):
        label(x, 508, name)
        text(x, 520, value, 10, bold)

    text(40, 570, "ITEMS", 9, True, BLUE)
    cols = [40, 62, 142, 300, 330, 362, 420, 460, 502, 580]
    heads = ["#", "SKU", "Description", "Qty", "Unit", "Unit net\n(EUR)", "Disc.", "VAT", "Line net\n(EUR)"]
    draw.rectangle((40 * S, 582 * S, 580 * S, 607 * S), fill=NAVY)
    for i, head in enumerate(heads):
        text((cols[i] + cols[i + 1]) / 2, 586 if "\n" not in head else 583, head, 6.5, True, "white", "ma")
    top = 607
    rows = data["items"]
    for r in range(4):
        y0, y1 = top + r * 33, top + (r + 1) * 33
        draw.rectangle((40 * S, y0 * S, 580 * S, y1 * S), fill="white" if r % 2 == 0 else "#f3f6fa")
        rule(40, y1, 580, y1)
        if r < len(rows):
            it = rows[r]
            cells = [str(r + 1), it["sku"], it["description"], str(it["quantity"]), it["unit"] or "pcs",
                     it["unit_net"], f"{it['discount_percent']}%", f"{it['vat_percent']}%", it["source_line_net"]]
            for i, cell in enumerate(cells):
                bold = i in (1, 8)
                if i in (2,):
                    text(cols[i] + 5, y0 + 12, cell, 8)
                else:
                    text((cols[i] + cols[i + 1]) / 2, y0 + 12, cell, 8, bold, INK, "ma")
    for x in cols:
        rule(x, 607, x, top + 4 * 33)

    t = data["totals"]
    box(40, 748, 580, 812)
    for x, name, value, color in ((130, "Net total", t["net"], GRAY), (310, "VAT total", t["vat"], GRAY),
                                  (490, "Gross total", t["gross"], BLUE)):
        text(x, 760, name.upper(), 7, True, color, "ma")
        text(x, 777, f"EUR {value}", 11, True, INK, "ma")
    rule(220, 754, 220, 806)
    rule(400, 754, 400, 806)
    rule(40, 822, 580, 822)
    text(40, 831, "All data is synthetic  |  Prices are net  |  Dates: YYYY-MM-DD  |  Currency: EUR", 6.5, False, GRAY)
    text(580, 831, data["external_reference"], 6.5, False, GRAY, "ra")
    return im.resize((W, H), Image.LANCZOS)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    if out.exists():
        sys.exit(f"{out} exists; refusing to overwrite")
    render(data).save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
