"""Deterministic field extraction: dates and amounts.

This is the module that actually fixes the "blank opening_date / deadline
/ amount_range" problem. It runs before any LLM call, is fast, free, and
auditable (every value carries the exact snippet it came from), and in
practice catches the large majority of cases because funding calls use a
fairly small vocabulary of labels ("Deadline:", "Applications close",
"Grant size", "up to $50,000", ...). The LLM fallback in llm_fallback.py
only needs to handle what this misses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

import dateparser
from dateparser.search import search_dates

from .extract_text import PageContent
from .schema import ExtractedField, FieldConfidence

# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

DEADLINE_LABELS = re.compile(
    r"(application\s+)?(deadline|closing\s*date|close\s*date|applications?\s+close|"
    r"submission\s+deadline|due\s+date|proposals?\s+due|closes?\s+on|closes?:|"
    r"last\s+date\s+to\s+apply|"
    # "Submit your application by October 18", "Apply by...", "no later
    # than..." -- these read as ordinary instructions, not a labeled field,
    # but they're one of the most common ways a real call actually states
    # its deadline (seen verbatim on a real funder page).
    r"submit\s+(your\s+)?(application|proposal|entry|submission)s?\s+by|"
    r"apply\s+by|applications?\s+(accepted|received)\s+until|"
    r"no\s+later\s+than|on\s+or\s+before|must\s+be\s+(submitted|received)\s+by)",
    re.IGNORECASE,
)

OPENING_LABELS = re.compile(
    r"(opening\s*date|open\s*date|call\s+opens|applications?\s+open|launch\s*date|"
    r"opens?\s+on|opens?:|start\s+date|application\s+period\s+(begins|starts))",
    re.IGNORECASE,
)

AMOUNT_LABELS = re.compile(
    r"(grant\s+(size|amount|range)|funding\s+(amount|range|available)|award\s+(size|amount|range)|"
    r"amount\s+range|budget\s+range|(maximum|minimum)\s+(grant|award|funding)|financing\s+range|"
    r"ticket\s+size|total\s+(grant\s+)?amount|indicative\s+(grant\s+)?amount|financing\s+amount|"
    r"contribution\s+(amount|size)|subgrant\s+(size|amount|range)|allocation\s+(amount|range))",
    re.IGNORECASE,
)

# A page mentioning big dollar figures is not necessarily an open call -- it
# might be an "about blended finance" primer quoting market size, cumulative
# volume, or funding-gap statistics. Any amount found near these words is
# describing the market, not what a single applicant could receive, and must
# never be reported as this opportunity's amount_range.
NEGATIVE_AMOUNT_CONTEXT = re.compile(
    r"market\s+siz|mobili[sz]ed|raised\s+to.?date|invested\s+(to.?date|since|globally)|"
    r"funding\s+gap|median\s+transaction|\bGDP\b|global(ly)?\s+(market|total)|cumulative(ly)?|"
    r"to.?date,?\s+(blended\s+finance|the\s+facility)|total\s+market|annual(ly)?\s+(gap|need)|"
    r"transactions?\s+range|deployed\s+(globally|worldwide|to.?date)",
    re.IGNORECASE,
)

# Conversely, these words describe money an *applicant* could actually
# receive -- the thing amount_range is supposed to capture.
POSITIVE_AMOUNT_CONTEXT = re.compile(
    r"grant|award|fund(ing)?\s+(available|amount)|budget\s+(range|available|of\s+up\s+to|for\s+(this|the)\s+"
    r"(call|grant|project))|financing|ticket\s+size|contribution|"
    r"disburs|allocat|subgrant|per\s+(project|proposal|organi[sz]ation|grantee|pilot|applicant)|"
    r"selected\s+(proposal|applicant|pilot)|successful\s+applicant",
    re.IGNORECASE,
)

# Any language suggesting this page is (or is not) an actual, currently open
# call -- used to flag pages like informational primers where extracted
# fields, even if found, shouldn't be trusted as a real opportunity's data.
CALL_SIGNAL_RE = re.compile(
    r"call\s+for\s+(proposals|applications)|request\s+for\s+proposals|\bRFP\b|"
    r"apply\s+(by|now|online|here)|application\s+(deadline|form|process)|"
    r"submission\s+deadline|eligib(le|ility)|how\s+to\s+apply|grant\s+application|"
    r"applications?\s+(close|open|due)",
    re.IGNORECASE,
)

DATESEARCH_SETTINGS = {
    "STRICT_PARSING": False,
    "RETURN_AS_TIMEZONE_AWARE": False,
    # A call stating "by October 18" with no year means the nearest
    # upcoming one, not the most recent past one -- without this,
    # dateparser defaults to treating an ambiguous month-day as the
    # current year even after that date has already passed.
    "PREFER_DATES_FROM": "future",
}

_DATE_TOKEN = re.compile(
    r"\b(\d{1,2}\s+)?(January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"(\.|\s+\d{1,4})?[\s,]*\d{0,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "USD": "USD", "US$": "USD",
                      "EUR": "EUR", "GBP": "GBP", "KES": "KES", "KSh": "KES", "Ksh": "KES"}

_NUM = r"[\d][\d,\.]*"
# Longer/more specific alternatives listed before the short forms that are
# their own prefix ("billion" before "b", "million"/"mn" before "m") so the
# regex engine's leftmost-first alternation captures the intended token
# instead of stopping one character in.
_SUFFIX_GROUP = r"(?:\s?(?:thousand|million|billion|mn|bn|k|K|m|M|b))?"

_AMOUNT_RANGE_RE = re.compile(
    rf"(?P<cur1>US\$|USD|EUR|GBP|KES|KSh|Ksh|[$€£])\s?"
    rf"(?P<min>{_NUM})(?P<minsuf>{_SUFFIX_GROUP})\s?"
    rf"(?:-|to|–|—|and)\s?"
    rf"(?P<cur2>US\$|USD|EUR|GBP|KES|KSh|Ksh|[$€£])?\s?"
    rf"(?P<max>{_NUM})(?P<maxsuf>{_SUFFIX_GROUP})",
    re.IGNORECASE,
)

_AMOUNT_UPTO_RE = re.compile(
    rf"(?:up\s+to|maximum\s+of|not\s+exceeding)\s+"
    rf"(?P<cur>US\$|USD|EUR|GBP|KES|KSh|Ksh|[$€£])\s?"
    rf"(?P<max>{_NUM})(?P<maxsuf>{_SUFFIX_GROUP})",
    re.IGNORECASE,
)

_AMOUNT_SINGLE_RE = re.compile(
    rf"(?P<cur>US\$|USD|EUR|GBP|KES|KSh|Ksh|[$€£])\s?(?P<val>{_NUM})(?P<suffix>{_SUFFIX_GROUP})",
    re.IGNORECASE,
)


def _normalize_currency(token: str) -> str:
    token = token.strip()
    return _CURRENCY_SYMBOLS.get(token, _CURRENCY_SYMBOLS.get(token.upper(), token))


def _to_number(raw: str, suffix: str = "") -> float:
    value = float(raw.replace(",", ""))
    suffix = (suffix or "").strip().lower()
    if suffix in ("k", "thousand"):
        value *= 1_000
    elif suffix in ("m", "mn", "million"):
        value *= 1_000_000
    elif suffix in ("b", "bn", "billion"):
        value *= 1_000_000_000
    return value


@dataclass
class AmountMatch:
    text: str
    currency: Optional[str]
    amount_min: Optional[float]
    amount_max: Optional[float]


def _range_to_amount(m: re.Match) -> AmountMatch:
    cur = _normalize_currency(m.group("cur2") or m.group("cur1"))
    amin = _to_number(m.group("min"), m.group("minsuf") or "")
    amax = _to_number(m.group("max"), m.group("maxsuf") or "")
    if amin > amax:
        amin, amax = amax, amin
    return AmountMatch(text=m.group(0), currency=cur, amount_min=amin, amount_max=amax)


def _upto_to_amount(m: re.Match) -> AmountMatch:
    cur = _normalize_currency(m.group("cur"))
    amax = _to_number(m.group("max"), m.group("maxsuf") or "")
    return AmountMatch(text=m.group(0), currency=cur, amount_min=None, amount_max=amax)


def _single_to_amount(m: re.Match) -> AmountMatch:
    cur = _normalize_currency(m.group("cur"))
    val = _to_number(m.group("val"), m.group("suffix") or "")
    return AmountMatch(text=m.group(0), currency=cur, amount_min=val, amount_max=val)


def _amount_candidates(text: str) -> list[tuple[str, re.Match]]:
    """All amount-shaped matches in `text`, ranges and "up to X" first since
    they're less ambiguous than a bare single figure, then in reading order.
    """
    cands: list[tuple[str, re.Match]] = []
    cands += [("range", m) for m in _AMOUNT_RANGE_RE.finditer(text)]
    cands += [("upto", m) for m in _AMOUNT_UPTO_RE.finditer(text)]
    cands += [("single", m) for m in _AMOUNT_SINGLE_RE.finditer(text)]
    priority = {"range": 0, "upto": 0, "single": 1}
    cands.sort(key=lambda c: (c[1].start(), priority[c[0]]))
    return cands


_AMOUNT_CONTEXT_WINDOW = 120


def find_amount(text: str, *, require_context: bool = False) -> Optional[AmountMatch]:
    """Find the first plausible amount in `text`.

    With `require_context=False` (the default), this just returns the first
    amount-shaped match -- appropriate when `text` is already a small window
    that's known to be about this opportunity's funding (a table cell next
    to a "Grant size" label, or the text right after an AMOUNT_LABELS hit).

    With `require_context=True`, every candidate is checked against its
    surrounding text: a match is rejected outright if it sits near language
    describing market size, cumulative volume, or funding gaps rather than
    what a single applicant could receive (NEGATIVE_AMOUNT_CONTEXT), and is
    otherwise only accepted if an applicant-funding keyword or an amount
    label is nearby (POSITIVE_AMOUNT_CONTEXT / AMOUNT_LABELS). Use this for
    an unscoped scan of a whole page or document -- it's what stops a call's
    amount_range from being filled in with "$8 billion" lifted from an
    unrelated sentence about total market volume.
    """
    seen_spans: list[tuple[int, int]] = []
    for kind, m in _amount_candidates(text):
        span = m.span()
        if any(span[0] < e and span[1] > s for s, e in seen_spans):
            continue  # overlaps a match already returned/rejected at this position
        seen_spans.append(span)

        if require_context:
            window = text[max(0, span[0] - _AMOUNT_CONTEXT_WINDOW): span[1] + _AMOUNT_CONTEXT_WINDOW]
            if NEGATIVE_AMOUNT_CONTEXT.search(window):
                continue
            if not (POSITIVE_AMOUNT_CONTEXT.search(window) or AMOUNT_LABELS.search(window)):
                continue

        if kind == "range":
            return _range_to_amount(m)
        if kind == "upto":
            return _upto_to_amount(m)
        return _single_to_amount(m)

    return None


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


def _parse_date_near(text: str, start: int, window: int = 120) -> Optional[tuple[str, str]]:
    """Look for a parseable date within `window` chars after a label match.
    Returns (raw_snippet, iso_date) or None.
    """
    segment = text[start:start + window]
    token_match = _DATE_TOKEN.search(segment)
    if not token_match:
        return None
    candidate = token_match.group(0)
    parsed = dateparser.parse(candidate, settings=DATESEARCH_SETTINGS)
    if not parsed:
        return None
    return candidate, parsed.date().isoformat()


def _label_hits(text: str, label_re: re.Pattern) -> list[re.Match]:
    return list(label_re.finditer(text))


def find_labeled_date(text: str, label_re: re.Pattern) -> Optional[ExtractedField]:
    for match in _label_hits(text, label_re):
        result = _parse_date_near(text, match.end())
        if result:
            raw, iso = result
            snippet_start = max(0, match.start() - 10)
            snippet = text[snippet_start:match.end() + 60].strip()
            return ExtractedField(value=iso, confidence=FieldConfidence.PATTERN, source_snippet=snippet)
    return None


def find_labeled_amount(text: str) -> Optional[ExtractedField]:
    for match in AMOUNT_LABELS.finditer(text):
        window = text[match.end():match.end() + 150]
        found = find_amount(window)
        if found:
            snippet = (text[max(0, match.start() - 5):match.end() + 150]).strip()
            return ExtractedField(value=found.text, confidence=FieldConfidence.PATTERN, source_snippet=snippet), found
    return None


# ---------------------------------------------------------------------------
# Table-first lookups (highest confidence)
# ---------------------------------------------------------------------------


def _table_lookup(tables: list[list[list[str]]], label_re: re.Pattern) -> Optional[tuple[str, str]]:
    """Scan rows for a label cell followed by a value cell in the same row."""
    for table in tables:
        for row in table:
            for i, cell in enumerate(row[:-1]):
                if label_re.search(cell):
                    value_cell = row[i + 1]
                    if value_cell.strip():
                        return cell, value_cell
    return None


def find_table_date(tables: list[list[list[str]]], label_re: re.Pattern) -> Optional[ExtractedField]:
    hit = _table_lookup(tables, label_re)
    if not hit:
        return None
    label, value = hit
    parsed = dateparser.parse(value, settings=DATESEARCH_SETTINGS)
    if not parsed:
        # Value cell might itself contain a date embedded in more text.
        token_match = _DATE_TOKEN.search(value)
        if token_match:
            parsed = dateparser.parse(token_match.group(0), settings=DATESEARCH_SETTINGS)
    if not parsed:
        return None
    return ExtractedField(
        value=parsed.date().isoformat(),
        confidence=FieldConfidence.TABLE,
        source_snippet=f"{label} | {value}",
    )


def find_table_amount(tables: list[list[list[str]]]) -> Optional[tuple[ExtractedField, AmountMatch]]:
    hit = _table_lookup(tables, AMOUNT_LABELS)
    if not hit:
        return None
    label, value = hit
    found = find_amount(value)
    if not found:
        return None
    field = ExtractedField(
        value=found.text,
        confidence=FieldConfidence.TABLE,
        source_snippet=f"{label} | {value}",
    )
    return field, found


# ---------------------------------------------------------------------------
# Top-level: fill a PageContent's facts
# ---------------------------------------------------------------------------


@dataclass
class ParsedFacts:
    opening_date: ExtractedField
    deadline: ExtractedField
    amount_range: ExtractedField
    amount_min: Optional[float]
    amount_max: Optional[float]
    currency: Optional[str]
    looks_like_call: bool


def parse_facts(page: PageContent) -> ParsedFacts:
    # Deadline: tables first (most reliable), then prose.
    deadline = find_table_date(page.tables, DEADLINE_LABELS) or find_labeled_date(page.text, DEADLINE_LABELS) or ExtractedField()
    opening = find_table_date(page.tables, OPENING_LABELS) or find_labeled_date(page.text, OPENING_LABELS) or ExtractedField()

    amount_field = ExtractedField()
    amount_min = amount_max = None
    currency = None

    table_amount = find_table_amount(page.tables)
    if table_amount:
        amount_field, found = table_amount
        amount_min, amount_max, currency = found.amount_min, found.amount_max, found.currency
    else:
        labeled = find_labeled_amount(page.text)
        if labeled:
            amount_field, found = labeled
            amount_min, amount_max, currency = found.amount_min, found.amount_max, found.currency
        else:
            # Unscoped scan of the whole page -- gated by context so a market-size
            # or funding-gap figure elsewhere on the page can never be mistaken
            # for what a single applicant could receive (see find_amount's
            # require_context docstring).
            found = find_amount(page.text, require_context=True)
            if found:
                amount_field = ExtractedField(
                    value=found.text,
                    confidence=FieldConfidence.PATTERN,
                    source_snippet=found.text,
                )
                amount_min, amount_max, currency = found.amount_min, found.amount_max, found.currency

    looks_like_call = bool(
        CALL_SIGNAL_RE.search(page.text)
        or deadline.confidence != FieldConfidence.MISSING
        or opening.confidence != FieldConfidence.MISSING
    )

    return ParsedFacts(
        opening_date=opening,
        deadline=deadline,
        amount_range=amount_field,
        amount_min=amount_min,
        amount_max=amount_max,
        currency=currency,
        looks_like_call=looks_like_call,
    )
