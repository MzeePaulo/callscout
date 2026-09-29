"""Optional LLM fallback for whatever the deterministic parser in
parse_fields.py couldn't find. Only called for the specific fields that
are still missing, with a strict JSON schema, so it's cheap and its
output is easy to sanity-check against the source text.

Requires ANTHROPIC_API_KEY in the environment. If it's not set, or the
`anthropic` package isn't installed, this module degrades to a no-op so
the rest of the pipeline still runs (deterministic-only mode).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .schema import ExtractedField, FieldConfidence

SYSTEM_PROMPT = (
    "You extract structured facts about a funding/grant opportunity from the "
    "page text and tables given to you. Only report a value if it is "
    "explicitly present in the given text -- never guess or infer from "
    "general knowledge. Respond with strict JSON matching the schema, using "
    "null for anything not present. Include the exact source sentence or "
    "table row for every non-null field."
)

_SCHEMA_HINT = """{
  "opening_date": {"value": "YYYY-MM-DD or null", "source_snippet": "string or null"},
  "deadline": {"value": "YYYY-MM-DD or null", "source_snippet": "string or null"},
  "amount_range": {"value": "string as written, e.g. '$50,000 - $200,000', or null", "source_snippet": "string or null"}
}"""


def available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def extract_missing_fields(
    text: str,
    tables: list[list[list[str]]],
    need: list[str],
    model: str = "claude-haiku-4-5",
) -> dict[str, ExtractedField]:
    """Ask the LLM only for the fields listed in `need`
    (e.g. ["deadline", "amount_range"]). Returns a dict of field name ->
    ExtractedField for whatever it found; missing fields are omitted.
    """
    if not need or not available():
        return {}

    import anthropic

    client = anthropic.Anthropic()

    table_text = "\n\n".join(
        "\n".join(" | ".join(row) for row in table) for table in tables[:10]
    )
    user_content = (
        f"Fields needed: {', '.join(need)}\n\n"
        f"JSON schema to fill (only include the needed fields):\n{_SCHEMA_HINT}\n\n"
        f"PAGE TEXT:\n{text[:8000]}\n\nTABLES:\n{table_text[:4000]}"
    )

    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = "".join(block.text for block in resp.content if hasattr(block, "text"))

    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return {}

    out: dict[str, ExtractedField] = {}
    for field_name in need:
        entry = data.get(field_name)
        if entry and entry.get("value"):
            out[field_name] = ExtractedField(
                value=entry["value"],
                confidence=FieldConfidence.LLM,
                source_snippet=entry.get("source_snippet"),
            )
    return out
