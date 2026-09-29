"""Unit tests for HTML -> PageContent extraction, in particular the
same-domain PDF filter -- a real, observed failure was extraction
following an off-domain PDF (an unrelated background report) linked from
a genuine call page, and merging its unrelated figures into the result.
"""
from callscout.extract_text import extract

HTML = """
<html><head><title>Test</title></head>
<body>
  <a href="own-guidelines.pdf">Our guidelines</a>
  <a href="https://otherhost.invalid/unrelated-report.pdf">Unrelated report</a>
  <a href="/downloads/another-own-doc.pdf">Another own document</a>
</body></html>
"""


def test_pdf_links_default_to_same_domain_only():
    page = extract(HTML, base_url="https://example.org/calls/2026-call")
    assert page.links == [
        "https://example.org/calls/own-guidelines.pdf",
        "https://example.org/downloads/another-own-doc.pdf",
    ]


def test_pdf_links_can_include_cross_domain_when_asked():
    page = extract(HTML, base_url="https://example.org/calls/2026-call", include_cross_domain_pdfs=True)
    assert "https://otherhost.invalid/unrelated-report.pdf" in page.links
    assert len(page.links) == 3
