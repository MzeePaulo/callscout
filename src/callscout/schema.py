"""Data model for a single funding opportunity record.

Field names match the columns already used in Paul's LAC Funding Scout
Google Sheet, so the pipeline output can be dropped straight into that
workflow (or any other n8n sheet-writer) with no remapping.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FieldConfidence(str, Enum):
    """How a field's value was obtained, most to least trustworthy."""

    TABLE = "table"          # parsed from an HTML/PDF table cell
    PATTERN = "pattern"      # deterministic regex/dateparser hit in prose
    LLM = "llm"              # LLM fallback extraction
    MISSING = "missing"      # genuinely not found (not just "we didn't try")


class ExtractedField(BaseModel):
    value: Optional[str] = None
    confidence: FieldConfidence = FieldConfidence.MISSING
    source_snippet: Optional[str] = Field(
        default=None,
        description="The raw text/table cell the value was pulled from, for auditing.",
    )


class Opportunity(BaseModel):
    """One funding call, normalized for the Opportunities sheet."""

    source_url: str
    title: Optional[str] = None

    opening_date: ExtractedField = ExtractedField()
    deadline: ExtractedField = ExtractedField()
    amount_range: ExtractedField = ExtractedField()

    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    currency: Optional[str] = None

    summary: Optional[str] = None
    fetch_method: Optional[str] = None  # "static" | "browser"
    discovered_via_seed: Optional[str] = Field(
        default=None,
        description="The seed/listing page this URL was crawled from, when it came from discover_and_extract() rather than a direct URL list.",
    )
    looks_like_call: Optional[bool] = Field(
        default=None,
        description=(
            "Whether the page actually reads as a live call for proposals/RFP "
            "(deadline/apply/eligibility language present), as opposed to an "
            "informational or market-primer page. False is a signal to review "
            "the row before trusting any extracted field, not proof it's wrong."
        ),
    )
    extraction_notes: list[str] = Field(default_factory=list)

    def to_sheet_row(self) -> dict:
        """Flatten to the columns the existing Google Sheet expects."""
        return {
            "source_url": self.source_url,
            "title": self.title or "",
            "opening_date": self.opening_date.value or "",
            "deadline": self.deadline.value or "",
            "amount_range": self.amount_range.value or "",
            "amount_min": self.amount_min if self.amount_min is not None else "",
            "amount_max": self.amount_max if self.amount_max is not None else "",
            "currency": self.currency or "",
            "summary": self.summary or "",
            "looks_like_call": "" if self.looks_like_call is None else str(self.looks_like_call),
            "discovered_via_seed": self.discovered_via_seed or "",
            "field_confidence": (
                f"opening_date={self.opening_date.confidence.value},"
                f"deadline={self.deadline.confidence.value},"
                f"amount_range={self.amount_range.confidence.value}"
            ),
        }
