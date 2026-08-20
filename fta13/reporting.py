"""Professional PDF verification report with Arabic/English text support."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


def _arabic_display(text: str) -> str:
    if not any("\u0600" <= char <= "\u06ff" for char in text):
        return text
    import arabic_reshaper
    from bidi.algorithm import get_display

    return get_display(arabic_reshaper.reshape(text))


def build_pdf_report(
    *,
    supplier_outcome: Any,
    supply_outcome: Any,
    extracted_document: dict[str, Any] | None = None,
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = BytesIO()
    styles = getSampleStyleSheet()
    font_name = "Helvetica"
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if font_path.exists():
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(font_path)))
        font_name = "DejaVuSans"
    body = ParagraphStyle("FTA13Body", parent=styles["BodyText"], fontName=font_name)
    cell = ParagraphStyle(
        "FTA13Cell", parent=body, fontSize=7.2, leading=9, splitLongWords=True
    )
    cell_header = ParagraphStyle(
        "FTA13CellHeader",
        parent=cell,
        textColor=colors.white,
        leading=9,
    )
    right = ParagraphStyle(
        "FTA13Arabic", parent=body, alignment=TA_RIGHT, leading=16
    )
    heading = ParagraphStyle(
        "FTA13Heading", parent=styles["Heading1"], fontName=font_name, textColor=colors.HexColor("#17365D")
    )
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="FTA Decision 13 Verification Record",
    )
    story = [
        Paragraph("FTA Decision 13 Verification Record", heading),
        Spacer(1, 5 * mm),
        Paragraph(f"Assessment date: {supply_outcome.as_of.isoformat()}", body),
        Paragraph(f"Supplier reference: {supplier_outcome.supplier_id}", body),
        Paragraph(f"Supply reference: {supply_outcome.supply_id}", body),
        Spacer(1, 5 * mm),
    ]
    if extracted_document:
        for key in ("supplier_name_ar", "supplier_name_en", "invoice_number"):
            item = extracted_document.get(key) or {}
            original = item.get("original", "") if isinstance(item, dict) else ""
            if original:
                style = right if any("\u0600" <= c <= "\u06ff" for c in original) else body
                story.append(Paragraph(f"{key.replace('_', ' ').title()}: {_arabic_display(original)}", style))
        story.append(Spacer(1, 5 * mm))
    for title, outcome in (
        ("Supplier verification", supplier_outcome),
        ("Supply verification", supply_outcome),
    ):
        story.append(Paragraph(title, heading))
        rows = [
            [
                Paragraph("Clause", cell_header),
                Paragraph("Article", cell_header),
                Paragraph("Status", cell_header),
                Paragraph("Requirement", cell_header),
            ]
        ]
        for result in outcome.results:
            if result.verdict.value == "not_applicable":
                continue
            rows.append(
                [
                    Paragraph(escape(result.clause_id), cell),
                    Paragraph(escape(result.article), cell),
                    Paragraph(escape(result.verdict.value), cell),
                    Paragraph(escape(result.requirement), cell),
                ]
            )
        table = Table(rows, colWidths=[23 * mm, 22 * mm, 28 * mm, 103 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17365D")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.extend([table, Spacer(1, 5 * mm)])
    story.extend(
        [
            Paragraph("Important limitation", heading),
            Paragraph(
                "This report addresses FTA Decision No. 13 of 2026 verification measures only. "
                "It does not determine overall input-tax recoverability and is not tax advice.",
                body,
            ),
        ]
    )
    document.build(story)
    return buffer.getvalue()
