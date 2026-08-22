"""Professional PDF verification report with Arabic/English text support."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


def _contains_arabic(text: str) -> bool:
    return any("\u0600" <= char <= "\u06ff" for char in text)


def _arabic_display(text: str) -> str:
    if not _contains_arabic(text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError as exc:
        raise RuntimeError(
            "Arabic PDF rendering requires arabic-reshaper and python-bidi."
        ) from exc

    return get_display(arabic_reshaper.reshape(text))


def _pdf_text(value: Any) -> str:
    """Escape user-controlled text for ReportLab's XML-like Paragraph markup."""
    return escape(str(value if value is not None else ""))


def build_pdf_report(
    *,
    supplier_outcome: Any,
    supply_outcome: Any,
    extracted_document: dict[str, Any] | None = None,
    ruleset_label: str = "not stated",
    generated_on_utc: str = "not stated",
    reviewer: str = "",
    exception_available: bool | None = None,
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
    font_candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/local/share/fonts/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    font_path = next((path for path in font_candidates if path.exists()), None)
    extracted_values = [
        (item or {}).get("original", "")
        for item in (extracted_document or {}).values()
        if isinstance(item, dict)
    ]
    if any(_contains_arabic(str(value)) for value in extracted_values) and font_path is None:
        raise RuntimeError(
            "Arabic PDF rendering requires a Unicode font such as DejaVu Sans."
        )
    if font_path is not None:
        pdfmetrics.registerFont(TTFont("FTA13Unicode", str(font_path)))
        font_name = "FTA13Unicode"
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
        Paragraph(f"Ruleset version: {_pdf_text(ruleset_label)}", body),
        Paragraph(f"Generated on (UTC): {_pdf_text(generated_on_utc)}", body),
        Paragraph(
            "Reviewer (self-declared, not verified by this tool): "
            f"{_pdf_text(reviewer or 'not stated')}",
            body,
        ),
        Paragraph(f"Supplier reference: {_pdf_text(supplier_outcome.supplier_id)}", body),
        Paragraph(f"Supply reference: {_pdf_text(supply_outcome.supply_id)}", body),
        Spacer(1, 5 * mm),
    ]
    if extracted_document:
        for key in ("supplier_name_ar", "supplier_name_en", "invoice_number"):
            item = extracted_document.get(key) or {}
            original = item.get("original", "") if isinstance(item, dict) else ""
            if original:
                style = right if _contains_arabic(original) else body
                label = _pdf_text(key.replace("_", " ").title())
                displayed = _pdf_text(_arabic_display(original))
                story.append(Paragraph(f"{label}: {displayed}", style))
        story.append(Spacer(1, 5 * mm))

    if exception_available is None:
        assessment = getattr(supply_outcome, "assessment", None)
        exception_available = bool(
            assessment is not None and not assessment.verification_required
        )

    if exception_available:
        story.extend(
            [
                Paragraph("Supply verification", heading),
                Paragraph(
                    "Article 6 exception available. Based on the values entered, "
                    "this supply is below AED 10,000 and the supplier-spend ceiling "
                    "has not been exceeded. Continue monitoring trailing and "
                    "expected supplier totals.",
                    body,
                ),
                Spacer(1, 5 * mm),
            ]
        )
        report_sections = ()
    else:
        report_sections = (
            ("Supplier verification", supplier_outcome),
            ("Supply verification", supply_outcome),
        )

    for title, outcome in report_sections:
        story.append(Paragraph(title, heading))
        rows = [
            [
                Paragraph("Clause", cell_header),
                Paragraph("Article", cell_header),
                Paragraph("Status", cell_header),
                Paragraph("Requirement", cell_header),
                Paragraph("Detail", cell_header),
            ]
        ]
        for result in outcome.results:
            if result.verdict.value == "not_applicable":
                continue
            rows.append(
                [
                    Paragraph(_pdf_text(result.clause_id), cell),
                    Paragraph(_pdf_text(result.article), cell),
                    Paragraph(
                        _pdf_text(result.verdict.value.replace("_", " ").title()),
                        cell,
                    ),
                    Paragraph(_pdf_text(result.requirement), cell),
                    Paragraph(_pdf_text(getattr(result, "detail", "")), cell),
                ]
            )
        table = Table(
            rows,
            colWidths=[20 * mm, 18 * mm, 22 * mm, 60 * mm, 58 * mm],
            repeatRows=1,
        )
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
