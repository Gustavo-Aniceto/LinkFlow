"""Geração de PDF a partir do texto do documento.

Marcação simples (a mesma que a IA e o editor usam):
  # Nome / título          → título grande
  ## SEÇÃO                 → título de seção com linha
  ### Cargo — Empresa      → subtítulo em negrito
  - item                   → marcador
  linha em branco          → espaçamento
"""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

PURPLE = colors.HexColor("#6d28d9")


def _styles():
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=10, leading=14, textColor=colors.HexColor("#1a1a1a"))
    return {
        "base": base,
        "justify": ParagraphStyle("j", parent=base, alignment=TA_JUSTIFY),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11, leading=14,
                             textColor=PURPLE, spaceBefore=10, spaceAfter=1),
        "h3": ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=10.5, spaceBefore=5),
    }


def text_to_pdf(content: str, title: str = "Documento") -> bytes:
    st = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title=title, author="LinkFlow")
    story, bullets = [], []

    def flush():
        nonlocal bullets
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(escape(b), st["base"]), leftIndent=12) for b in bullets],
                                      bulletType="bullet", start="•", leftIndent=12, bulletFontSize=8))
            bullets = []

    for raw in content.replace("\r", "").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("• "):
            bullets.append(stripped[2:].strip())
            continue
        flush()
        if not stripped:
            story.append(Spacer(1, 4))
        elif stripped.startswith("### "):
            story.append(Paragraph(escape(stripped[4:]), st["h3"]))
        elif stripped.startswith("## "):
            story.append(Paragraph(escape(stripped[3:].upper()), st["h2"]))
            story.append(HRFlowable(width="100%", thickness=0.6, color=PURPLE, spaceAfter=3))
        elif stripped.startswith("# "):
            story.append(Paragraph(escape(stripped[2:]), st["h1"]))
        else:
            story.append(Paragraph(escape(stripped), st["justify"]))
    flush()
    if not story:
        story.append(Paragraph("(documento vazio)", st["base"]))
    doc.build(story)
    return buf.getvalue()
