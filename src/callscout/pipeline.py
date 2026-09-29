"""End-to-end: fetch -> extract (HTML + linked PDFs) -> deterministic
field parse -> optional LLM fallback for whatever's still missing.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import llm_fallback
from .crawler import Candidate, crawl_seeds
from .extract_pdf import fetch_and_extract_pdf, merge_pdf_into_page
from .extract_text import PageContent, extract
from .fetch import fetch
from .parse_fields import parse_facts
from .schema import ExtractedField, FieldConfidence, Opportunity

logger = logging.getLogger(__name__)


def run(
    url: str,
    *,
    follow_pdfs: bool = True,
    max_pdfs: int = 2,
    include_cross_domain_pdfs: bool = False,
    use_llm_fallback: bool = True,
    force_browser: bool = False,
    allow_browser: bool = True,
) -> Opportunity:
    fetch_result = fetch(url, force_browser=force_browser, allow_browser=allow_browser)
    page: PageContent = extract(fetch_result.html, base_url=url, include_cross_domain_pdfs=include_cross_domain_pdfs)

    notes: list[str] = []

    if follow_pdfs and page.links:
        for pdf_url in page.links[:max_pdfs]:
            try:
                pdf_content = fetch_and_extract_pdf(pdf_url)
                page = merge_pdf_into_page(page, pdf_content)
                notes.append(f"merged PDF: {pdf_url}")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"PDF fetch failed ({pdf_url}): {exc}")

    facts = parse_facts(page)

    if not facts.looks_like_call:
        notes.append(
            "page does not read as a live call for proposals (no deadline/apply/"
            "eligibility language found) -- treat any extracted fields as unverified"
        )

    opp = Opportunity(
        source_url=url,
        title=page.title,
        opening_date=facts.opening_date,
        deadline=facts.deadline,
        amount_range=facts.amount_range,
        amount_min=facts.amount_min,
        amount_max=facts.amount_max,
        currency=facts.currency,
        summary=(page.text[:400] + "...") if len(page.text) > 400 else page.text,
        fetch_method=fetch_result.method,
        looks_like_call=facts.looks_like_call,
        extraction_notes=notes,
    )

    if use_llm_fallback:
        missing = [
            name
            for name, field_ in (
                ("opening_date", opp.opening_date),
                ("deadline", opp.deadline),
                ("amount_range", opp.amount_range),
            )
            if field_.confidence == FieldConfidence.MISSING
        ]
        if missing:
            if llm_fallback.available():
                filled = llm_fallback.extract_missing_fields(page.text, page.tables, missing)
                for name, value in filled.items():
                    setattr(opp, name, value)
                    notes.append(f"llm fallback filled {name}")
            else:
                notes.append("llm fallback skipped (ANTHROPIC_API_KEY not set)")

    opp.extraction_notes = notes
    return opp


def discover_and_extract(
    seed_urls: list[str],
    *,
    max_links_per_seed: int = 30,
    known_urls: Optional[set[str]] = None,
    only_same_domain: bool = False,
    use_llm_fallback: bool = True,
    stop_on_error: bool = False,
    allow_browser: bool = True,
) -> list[Opportunity]:
    """Crawl -> extract, end to end, entirely self-hosted: no search API,
    no vendor SDK. `seed_urls` are pages you already know announce or list
    funding calls (a funder's own "open calls" page, an NGO grants
    directory, a DFI's RFP index); crawl_seeds() pulls out every link on
    them that reads like an actual call, and every resulting URL goes
    through the same table/PDF-aware, context-gated extraction as run().

    Extraction failures for one candidate don't abort the batch (unless
    `stop_on_error=True`); the failure is recorded in that Opportunity's
    `extraction_notes` instead so one broken link doesn't lose the rest of
    a run's results.
    """
    candidates: list[Candidate] = crawl_seeds(
        seed_urls,
        max_links_per_seed=max_links_per_seed,
        known_urls=known_urls,
        only_same_domain=only_same_domain,
    )

    results: list[Opportunity] = []
    for candidate in candidates:
        try:
            opp = run(candidate.url, use_llm_fallback=use_llm_fallback, allow_browser=allow_browser)
        except Exception as exc:  # noqa: BLE001
            if stop_on_error:
                raise
            opp = Opportunity(
                source_url=candidate.url,
                title=candidate.title,
                extraction_notes=[f"extraction failed: {exc}"],
            )
        opp.discovered_via_seed = candidate.matched_seed
        results.append(opp)

    return results
