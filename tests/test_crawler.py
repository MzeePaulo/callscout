"""Tests for the from-scratch seed-page crawler: no search API, no
mocked HTTP -- crawl_seed/crawl_seeds run against the local fixture
server exactly as they would against a real funder's "open calls" page.
"""
from callscout.crawler import crawl_seed, crawl_seeds
from callscout.pipeline import discover_and_extract
from callscout.schema import FieldConfidence


def test_crawl_seed_picks_only_call_like_links(fixture_server):
    candidates = crawl_seed(f"{fixture_server}/listing.html")
    urls = {c.url for c in candidates}

    # Real calls: matched by link text ("Call for Proposals", "Request for
    # Proposals") -- one via a same-page link, one via an href pattern.
    assert f"{fixture_server}/table_based.html" in urls
    assert f"{fixture_server}/calls/rfp-technical-assistance" in urls

    # Noise that must NOT be picked up: nav chrome, and -- just as
    # important -- an informational page with neither call language in
    # its link text nor its URL, even though it's about the same topic.
    assert f"{fixture_server}/market_primer.html" not in urls
    assert f"{fixture_server}/about.html" not in urls
    assert f"{fixture_server}/contact.html" not in urls
    assert f"{fixture_server}/privacy" not in urls


def test_crawl_seed_resolves_relative_urls(fixture_server):
    candidates = crawl_seed(f"{fixture_server}/listing.html")
    # The absolute-path href (/calls/...) must resolve against the seed's
    # own host, not stay a bare relative path.
    assert any(c.url.startswith(fixture_server) for c in candidates)


def test_crawl_seeds_dedupes_against_known_urls(fixture_server):
    candidates = crawl_seeds(
        [f"{fixture_server}/listing.html"],
        known_urls={f"{fixture_server}/table_based.html"},
    )
    urls = {c.url for c in candidates}
    assert f"{fixture_server}/table_based.html" not in urls
    assert f"{fixture_server}/calls/rfp-technical-assistance" in urls


def test_discover_and_extract_crawls_and_extracts(fixture_server):
    """End to end: crawl the listing page, extract every call-like link
    found, and don't let one broken link (the RFP link 404s -- it isn't a
    real fixture file) take down the rest of the batch.
    """
    opportunities = discover_and_extract([f"{fixture_server}/listing.html"], use_llm_fallback=False)
    by_url = {opp.source_url: opp for opp in opportunities}

    good = by_url[f"{fixture_server}/table_based.html"]
    assert good.deadline.value == "2026-05-15"
    assert good.discovered_via_seed == f"{fixture_server}/listing.html"

    broken = by_url[f"{fixture_server}/calls/rfp-technical-assistance"]
    assert any("extraction failed" in note for note in broken.extraction_notes)
