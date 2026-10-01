"""Optional PDF renderer (requires reportlab); run from any working directory."""

import os
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

root = Path(__file__).resolve().parents[1]
font_candidates = [Path("/usr/share/fonts/truetype/dejavu"), Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"]
regular, bold = "Helvetica", "Helvetica-Bold"
for folder in font_candidates:
    if (folder / "DejaVuSans.ttf").is_file() and (folder / "DejaVuSans-Bold.ttf").is_file():
        pdfmetrics.registerFont(TTFont("DesignSans", str(folder / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DesignSansBold", str(folder / "DejaVuSans-Bold.ttf")))
        regular, bold = "DesignSans", "DesignSansBold"
        break
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="BodyCustom", fontName=regular, fontSize=9.5, leading=13.5,
                          spaceAfter=8, textColor=colors.HexColor("#243247")))
styles["Heading1"].fontName = bold
styles["Heading1"].fontSize = 17
styles["Heading1"].leading = 22
styles["Heading2"].fontName = bold
styles["Heading2"].fontSize = 12
styles["Heading2"].leading = 17
for name in ["Heading1", "Heading2"]:
    styles[name].textColor = colors.HexColor("#173e63")

sections = (root / "docs/design.md").read_text().split("\n## ")
story = [Paragraph("Fakturama Image-to-Cash Automation", styles["Heading1"]),
         Paragraph("Proposed design | Kareem Abed | 1 October 2026", styles["BodyCustom"])]


def section(content, new_page=False):
    if new_page:
        story.append(PageBreak())
    title, body = content.split("\n", 1)
    story.append(Paragraph(escape(title), styles["Heading2"]))
    for paragraph in body.strip().split("\n\n"):
        story.append(Paragraph(escape(paragraph), styles["BodyCustom"]))


section(sections[1])
section(sections[2])
title, body = sections[3].split("\n", 1)
paragraphs = body.strip().split("\n\n")
section(title + "\n" + "\n\n".join(paragraphs[:4]), True)
section("3. Desktop workflow (continued)\n" + "\n\n".join(paragraphs[4:]), True)
section(sections[4], True)


def footer(canvas, document):
    canvas.setFont(regular, 8)
    canvas.setFillColor(colors.HexColor("#627187"))
    canvas.drawString(42, 25, "TJM Labs take-home | Design document")
    canvas.drawRightString(553, 25, str(document.page))


SimpleDocTemplate(str(root / "docs/design.pdf"), pagesize=(595, 842),
                  rightMargin=42, leftMargin=42, topMargin=36, bottomMargin=43,
                  title="Fakturama Image-to-Cash Automation - Design",
                  author="Kareem Abed").build(story, onFirstPage=footer, onLaterPages=footer)
