"""Pull text and tables out of linked PDFs (funding calls very often
publish the actual deadline/amount only in a downloadable guidelines PDF,
not on the landing page a generic web scraper sees).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

import httpx
import pdfplumber

from .extract_text import PageContent
from .fetch import USER_AGENT


@dataclass
class PdfContent:
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)


def extract_pdf_bytes(data: bytes, max_pages: int = 25) -> PdfContent:
    text_parts: list[str] = []
    tables: list[list[list[str]]] = []
    with pdfplumber.open(BytesIO(data)) as pdf:
        for page in pdf.pages[:max_pages]:
            page_text = page.extract_text() or ""
            if page_text:
                text_parts.append(page_text)
            for table in page.extract_tables() or []:
                rows = [[cell or "" for cell in row] for row in table]
                if rows:
                    tables.append(rows)
    return PdfContent(text="\n".join(text_parts), tables=tables)


def fetch_and_extract_pdf(url: str, timeout: float = 30.0) -> PdfContent:
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return extract_pdf_bytes(resp.content)


def merge_pdf_into_page(page: PageContent, pdf: PdfContent) -> PageContent:
    """Fold PDF text/tables into a PageContent so downstream field
    parsing doesn't need to know whether a fact came from HTML or a PDF.
    """
    return PageContent(
        title=page.title,
        text=(page.text + "\n" + pdf.text).strip(),
        tables=page.tables + pdf.tables,
        links=page.links,
    )
