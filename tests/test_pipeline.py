"""End-to-end tests against the fixture HTTP server: real fetch, real
HTML/PDF parsing, real field extraction -- no mocks. This is the same
shape of golden-set test recommended for the live LAC Funding Scout
calibration sites (Convergence A4FM, Humanity Insured, AFCIA/CTCN);
these fixtures mirror the layout patterns of those real sites so the
suite runs offline and deterministically.
"""
from callscout.pipeline import run
from callscout.schema import FieldConfidence


def test_table_based_page(fixture_server):
    opp = run(f"{fixture_server}/table_based.html", use_llm_fallback=False, follow_pdfs=False)
    assert opp.opening_date.value == "2026-03-03"
    assert opp.deadline.value == "2026-05-15"
    assert opp.amount_min == 50_000
    assert opp.amount_max == 200_000
    assert opp.deadline.confidence == FieldConfidence.TABLE


def test_prose_based_page(fixture_server):
    opp = run(f"{fixture_server}/prose_based.html", use_llm_fallback=False, follow_pdfs=False)
    assert opp.opening_date.value == "2026-01-10"
    assert opp.deadline.value == "2026-03-31"
    assert opp.amount_min == 10_000
    assert opp.amount_max == 75_000


def test_definition_list_page(fixture_server):
    opp = run(f"{fixture_server}/dl_based.html", use_llm_fallback=False, follow_pdfs=False)
    assert opp.opening_date.value == "2026-04-01"
    assert opp.deadline.value == "2026-06-30"
    assert opp.amount_max == 120_000


def test_pdf_linked_page_fills_blank_landing_page(fixture_server):
    """This is the exact failure mode reported against the live LAC
    Funding Scout workflow: the landing page itself has no dates or
    amounts, and the real values only exist in a linked PDF. A tool that
    only reads the HTML landing page (as Firecrawl-into-an-agent does by
    default) will return blanks here; callscout should not.
    """
    opp = run(f"{fixture_server}/pdf_linked.html", use_llm_fallback=False, follow_pdfs=True)
    assert opp.deadline.value == "2026-06-01"
    assert opp.amount_max == 500_000
    assert any("merged PDF" in note for note in opp.extraction_notes)


def test_market_primer_page_yields_no_false_amount(fixture_server):
    """Regression test for a real reported failure: an informational page
    about blended finance (market size, cumulative volume, funding gap
    figures) was returning e.g. "$8 billion" as the opportunity's
    amount_range -- a figure describing the whole market, not what a single
    applicant could receive. This fixture reproduces that page verbatim
    (mirroring Convergence's public blended-finance primer) and asserts the
    dangerous figures are all correctly suppressed, and that the page is
    flagged as not looking like a live call at all.
    """
    opp = run(f"{fixture_server}/market_primer.html", use_llm_fallback=False, follow_pdfs=False)
    assert opp.amount_range.confidence == FieldConfidence.MISSING
    assert opp.amount_min is None and opp.amount_max is None
    assert opp.deadline.confidence == FieldConfidence.MISSING
    assert opp.looks_like_call is False
    assert any("does not read as a live call" in note for note in opp.extraction_notes)


def test_off_domain_pdf_is_not_followed_and_deadline_phrasing_is_caught(fixture_server):
    """Regression test for a real observed failure: a genuine open-call
    page linked to both its own guidelines PDF and an unrelated background
    report hosted on a different domain. Following that off-domain PDF
    pulled in irrelevant figures (a global market report's numbers, not
    this call's), previously reported as this opportunity's amount_range.

    Also covers a real phrasing gap found on the same page: "Submit your
    application by <date>" wasn't recognized as a deadline at all before
    DEADLINE_LABELS was extended (a very common way calls actually state
    their deadline, without the word "deadline" appearing at all).
    """
    opp = run(f"{fixture_server}/domain_filter.html", use_llm_fallback=False, follow_pdfs=True)

    assert opp.deadline.value == "2026-10-18"
    assert opp.amount_max == 75_000

    assert any("own-guidelines.pdf" in note for note in opp.extraction_notes)
    assert not any("otherhost.invalid" in note for note in opp.extraction_notes)
    assert "otherhost.invalid" not in (opp.summary or "")


def test_without_pdf_following_fields_are_blank(fixture_server):
    """Sanity check that the PDF-following step is actually what's
    supplying the values above, not a false pass.
    """
    opp = run(f"{fixture_server}/pdf_linked.html", use_llm_fallback=False, follow_pdfs=False)
    assert opp.deadline.confidence == FieldConfidence.MISSING
    assert opp.amount_range.confidence == FieldConfidence.MISSING
