"""Unit tests for the deterministic field parser -- no network involved.
This is the golden-set-style regression suite for the module that most
directly fixes the blank opening_date/deadline/amount_range problem.
"""
from callscout.parse_fields import find_amount, find_labeled_date, DEADLINE_LABELS, OPENING_LABELS
from callscout.schema import FieldConfidence


# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------


def test_amount_range_dollar():
    m = find_amount("Grants are available ranging from $10,000 to $75,000 per pilot.")
    assert m is not None
    assert m.currency == "USD"
    assert m.amount_min == 10_000
    assert m.amount_max == 75_000


def test_amount_range_with_dash():
    m = find_amount("Grant size: $50,000 - $200,000")
    assert m is not None
    assert m.amount_min == 50_000
    assert m.amount_max == 200_000


def test_amount_upto():
    m = find_amount("Funding amount: up to $120,000")
    assert m is not None
    assert m.amount_min is None
    assert m.amount_max == 120_000


def test_amount_eur_range():
    m = find_amount("Awards range from EUR 20,000 to EUR 90,000.")
    assert m is not None
    assert m.currency == "EUR"
    assert m.amount_min == 20_000
    assert m.amount_max == 90_000


def test_amount_single_value():
    m = find_amount("A one-time award of $15,000 will be made.")
    assert m is not None
    assert m.amount_min == m.amount_max == 15_000


def test_amount_none_found():
    assert find_amount("This page has no money mentioned at all.") is None


# ---------------------------------------------------------------------------
# Context-gated amount scan (require_context=True) -- this is what stops a
# market-size or funding-gap figure from being reported as the opportunity's
# actual amount_range.
# ---------------------------------------------------------------------------


def test_context_scan_rejects_market_size_language():
    text = "Blended finance has mobilized approximately $231 billion in capital to-date."
    assert find_amount(text, require_context=True) is None


def test_context_scan_rejects_funding_gap_language():
    text = "There remains an estimated $4.2 trillion funding gap per annum to realize the SDGs."
    assert find_amount(text, require_context=True) is None


def test_context_scan_rejects_transaction_range_language():
    text = "Blended finance transactions range considerably in size, from a minimum of $110,000 to a maximum of $8 billion."
    assert find_amount(text, require_context=True) is None


def test_context_scan_accepts_grant_language():
    text = "Selected applicants will receive a grant of up to $75,000 to run their pilot."
    m = find_amount(text, require_context=True)
    assert m is not None
    assert m.amount_max == 75_000


def test_context_scan_accepts_per_project_language():
    text = "Financing of $20,000 to $60,000 is available per selected project."
    m = find_amount(text, require_context=True)
    assert m is not None
    assert m.amount_min == 20_000
    assert m.amount_max == 60_000


def test_context_scan_rejects_plain_amount_with_no_positive_context():
    # A bare figure with nothing nearby to say it's grant money should not
    # be reported, even if nothing negative is nearby either.
    text = "The organisation was founded in 2005 with a starting budget of $12,000 for office rent."
    # "budget" here is generic office spend, not grant money -- and there is
    # no grant/award/funding-available language, so this should be rejected.
    assert find_amount(text, require_context=True) is None


def test_amount_k_suffix():
    m = find_amount("Grants of up to $50k are available.")
    assert m is not None
    assert m.amount_max == 50_000


def test_amount_billion_suffix():
    # Regression: "billion" wasn't a recognized suffix at all -- "$2 billion"
    # parsed as a bare "$2", off by a factor of a billion.
    m = find_amount("Awards of up to $2 billion are available for selected projects.", require_context=True)
    assert m is not None
    assert m.amount_max == 2_000_000_000


def test_amount_bn_suffix():
    m = find_amount("Financing of $1.5bn is available for the selected proposal.", require_context=True)
    assert m is not None
    assert m.amount_min == m.amount_max == 1_500_000_000


# ---------------------------------------------------------------------------
# Date parsing near labels
# ---------------------------------------------------------------------------


def test_deadline_iso_format():
    field = find_labeled_date("Application deadline: 2026-05-15 for all applicants.", DEADLINE_LABELS)
    assert field is not None
    assert field.value == "2026-05-15"
    assert field.confidence == FieldConfidence.PATTERN


def test_deadline_long_format():
    field = find_labeled_date("applications close on March 31, 2026 at midnight.", DEADLINE_LABELS)
    assert field is not None
    assert field.value == "2026-03-31"


def test_deadline_submit_by_phrasing():
    # Regression: a real call page said "Submit your application by October
    # 18" with no "deadline" label anywhere on the page at all -- this is a
    # very common way calls actually state it, and was previously missed
    # entirely (reported as a blank deadline on a page that plainly had one).
    field = find_labeled_date("Submit your application by October 18, 2026 to be considered.", DEADLINE_LABELS)
    assert field is not None
    assert field.value == "2026-10-18"


def test_deadline_apply_by_phrasing():
    field = find_labeled_date("Apply by 2026-11-30 for full consideration.", DEADLINE_LABELS)
    assert field is not None
    assert field.value == "2026-11-30"


def test_opening_date_prose():
    field = find_labeled_date("The call opens on 10 January 2026 for submissions.", OPENING_LABELS)
    assert field is not None
    assert field.value == "2026-01-10"


def test_no_date_found():
    assert find_labeled_date("There is nothing about timing here.", DEADLINE_LABELS) is None
