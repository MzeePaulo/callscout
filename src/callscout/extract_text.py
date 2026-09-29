"""Turn raw HTML into: (1) clean prose text, and (2) a list of tables as
rows of cell text. Deadlines and amounts live in tables at least as often
as in prose, and markdown-flattening (what most scrapers do) silently
destroys table structure -- so we keep tables as a first-class citizen
instead of discarding them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import trafilatura
from bs4 import BeautifulSoup


@dataclass
class PageContent:
    title: str | None
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    # tables[i] = list of rows; each row = list of cell strings
    links: list[str] = field(default_factory=list)


def _extract_tables(soup: BeautifulSoup) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for table_tag in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table_tag.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            row = [c.get_text(" ", strip=True) for c in cells]
            if any(row):
                rows.append(row)
        if rows:
            tables.append(rows)
    return tables


def _extract_definition_lists(soup: BeautifulSoup) -> list[list[list[str]]]:
    """Some funder sites use <dl>/<dt>/<dd> "key: value" layouts instead of
    real tables for facts like deadline/amount. Treat each as a 2-column
    pseudo-table so the same downstream field-matching logic applies.
    """
    pseudo_tables: list[list[list[str]]] = []
    for dl in soup.find_all("dl"):
        rows = []
        terms = dl.find_all("dt")
        for dt in terms:
            dd = dt.find_next_sibling("dd")
            if dd:
                rows.append([dt.get_text(" ", strip=True), dd.get_text(" ", strip=True)])
        if rows:
            pseudo_tables.append(rows)
    return pseudo_tables


def extract(html: str, base_url: str | None = None, *, include_cross_domain_pdfs: bool = False) -> PageContent:
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    text = trafilatura.extract(html, include_tables=False, include_links=False) or ""
    if not text.strip():
        # Fallback if trafilatura's readability heuristics reject the page
        # (common on sparse funder pages that are mostly a table + a form).
        text = soup.get_text(" ", strip=True)

    tables = _extract_tables(soup) + _extract_definition_lists(soup)

    links = []
    if base_url:
        from urllib.parse import urljoin, urlparse

        base_domain = urlparse(base_url).netloc
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.lower().endswith(".pdf"):
                continue
            resolved = urljoin(base_url, href)
            # A real funder page routinely links to *other people's* PDFs --
            # background reports, cited research, partner publications --
            # alongside its own guidelines/eligibility document. Those are
            # never about this specific call, so merging their text in
            # (and letting their own dollar figures leak into amount_range)
            # is a real, observed failure mode. Default to only following
            # same-domain PDFs, since a call's actual guidelines doc is
            # overwhelmingly hosted alongside the call itself.
            if not include_cross_domain_pdfs and urlparse(resolved).netloc != base_domain:
                continue
            links.append(resolved)

    return PageContent(title=title, text=text, tables=tables, links=links)
