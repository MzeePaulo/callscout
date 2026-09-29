"""Discovery, built from scratch for this domain: no third-party search API,
no vendor SDK, no per-query cost. You give it a list of "seed" pages --
pages you already know list or announce funding opportunities (a funder's
own "open calls" page, an NGO grants-directory site, a DFI's RFP index,
a foundation's news page) -- and it crawls each one with the same
`httpx`/Playwright fetch layer already used for extraction, pulls out
every link that reads like an actual call, and hands the results to the
same extraction pipeline.

This is deliberately narrower than general web search: it won't find a
call you don't already have *some* source pointed at, but everything it
does find is precise to funding/NGO language, self-hosted, and free to
run as often as you like.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .fetch import fetch

# Link text or URL slugs that mark an actual call/opportunity, as opposed to
# "About us", "Contact", "News" and other listing-page chrome. Deliberately
# broad across the vocabulary NGOs and funders actually use, including
# procurement/UN-system terms (RFQ, EOI, solicitation, NOFO) alongside the
# more familiar "call for proposals"/"grant".
CALL_LINK_PATTERN = re.compile(
    r"call[\s\-_]?for[\s\-_]?(proposals?|applications?)|"
    r"request[\s\-_]?for[\s\-_]?(proposals?|quotations?|qualifications?)|"
    r"\bRFP\b|\bRFQ\b|\bRFI\b|\bEOI\b|expression[\s\-_]?of[\s\-_]?interest|"
    r"grant[s]?[\s\-_]?(opportunit\w*|opening|call|application)|"
    r"fund(ing)?[\s\-_]?opportunit\w*|open[\s\-_]?call|solicitation|"
    r"notice[\s\-_]?of[\s\-_]?funding|\bNOFO\b|tender|apply[\s\-_]?(now|here|online)",
    re.IGNORECASE,
)

# Never treat these as a call link even if a stray keyword matches --
# common listing-page chrome that would otherwise slip through.
_EXCLUDE_PATTERN = re.compile(
    r"^(privacy|terms|cookie|sitemap|login|sign[\s\-_]?in|newsletter)s?$",
    re.IGNORECASE,
)


@dataclass
class Candidate:
    url: str
    title: Optional[str]
    snippet: Optional[str]
    matched_seed: str


def crawl_seed(
    seed_url: str,
    *,
    max_links: int = 30,
    link_pattern: re.Pattern = CALL_LINK_PATTERN,
    only_same_domain: bool = False,
    force_browser: bool = False,
) -> list[Candidate]:
    """Fetch one seed/listing page and return every link on it that looks
    like an actual funding call, in page order, capped at `max_links`.
    """
    result = fetch(seed_url, force_browser=force_browser)
    soup = BeautifulSoup(result.html, "lxml")

    seed_domain = urlparse(seed_url).netloc
    candidates: list[Candidate] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        text = a.get_text(" ", strip=True)

        if _EXCLUDE_PATTERN.match(text):
            continue
        if not (link_pattern.search(text) or link_pattern.search(href)):
            continue

        resolved = urljoin(seed_url, href)
        if only_same_domain and urlparse(resolved).netloc != seed_domain:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)

        candidates.append(Candidate(url=resolved, title=text or None, snippet=None, matched_seed=seed_url))
        if len(candidates) >= max_links:
            break

    return candidates


def crawl_seeds(
    seed_urls: list[str],
    *,
    max_links_per_seed: int = 30,
    known_urls: Optional[set[str]] = None,
    link_pattern: re.Pattern = CALL_LINK_PATTERN,
    only_same_domain: bool = False,
) -> list[Candidate]:
    """Crawl every seed page and return the deduplicated union of
    candidate call links, skipping anything already in `known_urls`
    (e.g. URLs already in your Google Sheet, so repeat runs don't
    re-process the same opportunity).
    """
    known_urls = known_urls or set()
    seen: set[str] = set(known_urls)
    all_candidates: list[Candidate] = []

    for seed_url in seed_urls:
        for candidate in crawl_seed(
            seed_url,
            max_links=max_links_per_seed,
            link_pattern=link_pattern,
            only_same_domain=only_same_domain,
        ):
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            all_candidates.append(candidate)

    return all_candidates
