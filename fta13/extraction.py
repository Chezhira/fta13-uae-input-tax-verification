"""Bilingual Arabic/English document extraction with reviewable evidence."""

from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, field_validator


Language = Literal["ar", "en", "mixed", "unknown"]
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class UploadValidationError(ValueError):
    """Raised when a user upload cannot safely enter the extraction request."""


class SourceReference(BaseModel):
    page: int | None = Field(default=None, ge=1)
    quote: str = ""
    language: Language = "unknown"


class ExtractedValue(BaseModel):
    original: str = ""
    normalized: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sources: list[SourceReference] = Field(default_factory=list)


class DocumentExtraction(BaseModel):
    document_type: Literal[
        "tax_invoice",
        "commercial_licence",
        "certificate_of_incorporation",
        "identity_document",
        "bank_confirmation",
        "payment_evidence",
        "goods_evidence",
        "other",
    ] = "other"
    detected_languages: list[Language] = Field(default_factory=list)
    supplier_name_ar: ExtractedValue = Field(default_factory=ExtractedValue)
    supplier_name_en: ExtractedValue = Field(default_factory=ExtractedValue)
    supplier_reference: ExtractedValue = Field(default_factory=ExtractedValue)
    trn: ExtractedValue = Field(default_factory=ExtractedValue)
    invoice_number: ExtractedValue = Field(default_factory=ExtractedValue)
    invoice_date: ExtractedValue = Field(default_factory=ExtractedValue)
    country_of_incorporation: ExtractedValue = Field(default_factory=ExtractedValue)
    currency: ExtractedValue = Field(default_factory=ExtractedValue)
    consideration_ex_vat: ExtractedValue = Field(default_factory=ExtractedValue)
    payment_method: ExtractedValue = Field(default_factory=ExtractedValue)
    payee_country: ExtractedValue = Field(default_factory=ExtractedValue)
    supply_description: ExtractedValue = Field(default_factory=ExtractedValue)
    is_goods: bool | None = None
    third_party_in_payment: bool | None = None
    supplier_is_intermediary: bool | None = None
    evidence_kinds: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("detected_languages")
    @classmethod
    def unique_languages(cls, values: list[Language]) -> list[Language]:
        return list(dict.fromkeys(values))

    def decimal_value(self) -> Decimal | None:
        value = self.consideration_ex_vat.normalized.strip().replace(",", "")
        if not value:
            return None
        try:
            return Decimal(value)
        except InvalidOperation:
            return None

    def invoice_date_value(self) -> date | None:
        try:
            return date.fromisoformat(self.invoice_date.normalized.strip())
        except (TypeError, ValueError):
            return None

    def review_rows(self) -> list[dict]:
        rows = []
        for name in type(self).model_fields:
            value = getattr(self, name)
            if not isinstance(value, ExtractedValue) or not value.original:
                continue
            pages = sorted({s.page for s in value.sources if s.page is not None})
            rows.append(
                {
                    "Field": name.replace("_", " ").title(),
                    "Extracted": value.original,
                    "Normalized": value.normalized,
                    "Confidence": f"{value.confidence:.0%}",
                    "Page": ", ".join(str(p) for p in pages),
                }
            )
        return rows

    def source_rows(self) -> list[dict]:
        """Return the page-level quotations supporting extracted values."""
        rows = []
        for name in type(self).model_fields:
            value = getattr(self, name)
            if not isinstance(value, ExtractedValue):
                continue
            for source in value.sources:
                if source.quote:
                    rows.append(
                        {
                            "Field": name.replace("_", " ").title(),
                            "Page": source.page or "—",
                            "Language": source.language,
                            "Source quote": source.quote,
                        }
                    )
        return rows


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_identity(value: ExtractedValue) -> str:
    text = value.normalized or value.original
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^\w]+", "", text, flags=re.UNICODE)


def batch_identity_rows(
    items: list[DocumentExtraction], filenames: list[str]
) -> list[dict]:
    """Show the identity anchors extracted from each uploaded document."""
    rows = []
    for index, item in enumerate(items):
        rows.append(
            {
                "Document": (
                    filenames[index]
                    if index < len(filenames)
                    else f"Document {index + 1}"
                ),
                "Type": item.document_type.replace("_", " ").title(),
                "Supplier (Arabic)": item.supplier_name_ar.original,
                "Supplier (English)": item.supplier_name_en.original,
                "TRN": item.trn.normalized or item.trn.original,
                "Invoice": (
                    item.invoice_number.normalized or item.invoice_number.original
                ),
            }
        )
    return rows


def batch_identity_conflicts(items: list[DocumentExtraction]) -> list[str]:
    """Return blocking conflicts for a one-supplier, one-supply batch."""
    checks = {
        "Arabic supplier name": "supplier_name_ar",
        "English supplier name": "supplier_name_en",
        "TRN": "trn",
        "invoice number": "invoice_number",
    }
    conflicts = []
    for label, field_name in checks.items():
        values = {
            _canonical_identity(getattr(item, field_name))
            for item in items
            if _canonical_identity(getattr(item, field_name))
        }
        if len(values) > 1:
            conflicts.append(
                f"Conflicting {label} values were found across the uploaded documents."
            )
    return conflicts


def merge_extractions(items: list[DocumentExtraction]) -> DocumentExtraction:
    """Combine documents, retaining the most confident supported value per field."""
    if not items:
        return DocumentExtraction()
    conflicts = batch_identity_conflicts(items)
    if conflicts:
        raise ValueError(" ".join(conflicts))
    merged = items[0].model_copy(deep=True)
    merged.detected_languages = []
    merged.evidence_kinds = []
    merged.warnings = []
    for item in items:
        merged.detected_languages.extend(item.detected_languages)
        merged.evidence_kinds.extend(item.evidence_kinds)
        merged.warnings.extend(item.warnings)
        for name in type(item).model_fields:
            candidate = getattr(item, name)
            current = getattr(merged, name)
            if isinstance(candidate, ExtractedValue):
                if candidate.original and candidate.confidence > current.confidence:
                    setattr(merged, name, candidate.model_copy(deep=True))
            elif name in {"is_goods", "third_party_in_payment", "supplier_is_intermediary"}:
                if candidate is not None:
                    setattr(merged, name, candidate)
    merged.detected_languages = list(dict.fromkeys(merged.detected_languages))
    merged.evidence_kinds = list(dict.fromkeys(merged.evidence_kinds))
    merged.warnings = list(dict.fromkeys(merged.warnings))
    return merged


def validate_upload(filename: str, mime_type: str, content: bytes) -> None:
    allowed = {"application/pdf", "image/png", "image/jpeg"}
    if mime_type not in allowed:
        raise ValueError("Only PDF, PNG and JPEG documents are supported.")
    if not content:
        raise ValueError("The uploaded document is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadValidationError(
            "Each document must be 5 MB or smaller because files are sent inline "
            "to the AI extraction service. Compress or split the document and retry."
        )
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    expected = {
        "application/pdf": {"pdf"},
        "image/png": {"png"},
        "image/jpeg": {"jpg", "jpeg"},
    }
    if suffix not in expected[mime_type]:
        raise ValueError("The filename extension does not match the file type.")


def _data_url(mime_type: str, content: bytes) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


EXTRACTION_INSTRUCTIONS = """
Extract only facts visibly supported by the supplied document. Read Arabic and
English with equal care, including Arabic-Indic digits. Preserve the exact
original script in `original`; put normalized Latin digits, ISO dates
(YYYY-MM-DD), ISO-2 country codes and plain decimal amounts in `normalized`.
For every extracted value include the page number, a short exact source quote,
the quote language and a calibrated confidence score. Never translate a legal
name into the other language unless both forms are printed. Use empty strings,
nulls and warnings when a fact is absent or uncertain. Do not infer compliance,
tax recoverability or whether a legal requirement is satisfied.
""".strip()


def extract_document(
    *,
    filename: str,
    mime_type: str,
    content: bytes,
    api_key: str,
    model: str = "gpt-5.6",
    language_hint: Literal["auto", "ar", "en", "mixed"] = "auto",
) -> DocumentExtraction:
    """Extract bilingual fields using OpenAI file/image input and a strict schema."""
    validate_upload(filename, mime_type, content)
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    file_item = (
        {
            "type": "input_file",
            "filename": filename,
            "file_data": _data_url(mime_type, content),
            "detail": "high",
        }
        if mime_type == "application/pdf"
        else {
            "type": "input_image",
            "image_url": _data_url(mime_type, content),
            "detail": "high",
        }
    )
    response = client.responses.parse(
        model=model,
        store=False,
        input=[
            {
                "role": "system",
                "content": EXTRACTION_INSTRUCTIONS,
            },
            {
                "role": "user",
                "content": [
                    file_item,
                    {
                        "type": "input_text",
                        "text": (
                            "Extract supplier, invoice, payment and evidence facts "
                            "for a human-reviewed UAE FTA Decision 13 assessment. "
                            f"Expected document language: {language_hint}."
                        ),
                    },
                ],
            },
        ],
        text_format=DocumentExtraction,
    )
    if response.output_parsed is None:
        raise RuntimeError("The extraction returned no structured result.")
    return response.output_parsed
