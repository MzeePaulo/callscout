"""Verifies the JS-rendering fallback: a page whose content is injected by
client-side JavaScript should come back empty via the static fetch and
populated once Playwright renders it. This is what protects against the
other real-world failure mode -- JS-heavy funder portals where the
deadline widget isn't in the initial HTML at all.

Skipped automatically if Playwright's Chromium isn't installed in this
environment, so the rest of the suite still runs everywhere.
"""
import pytest

from callscout.fetch import fetch, fetch_static, _visible_text_len
from callscout.extract_text import extract
from callscout.parse_fields import parse_facts

pytestmark = pytest.mark.filterwarnings("ignore")


def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _playwright_available(), reason="Playwright/Chromium not available in this environment")
def test_static_fetch_misses_js_content(fixture_server):
    result = fetch_static(f"{fixture_server}/js_rendered.html")
    assert _visible_text_len(result.html) < 200  # just the empty <div id="app">


@pytest.mark.skipif(not _playwright_available(), reason="Playwright/Chromium not available in this environment")
def test_browser_fetch_sees_js_content(fixture_server):
    result = fetch(f"{fixture_server}/js_rendered.html")
    assert result.method == "browser"
    page = extract(result.html)
    facts = parse_facts(page)
    assert facts.deadline.value == "2026-08-20"
    assert facts.amount_min == 20_000
    assert facts.amount_max == 90_000


@pytest.mark.skipif(not _playwright_available(), reason="Playwright/Chromium not available in this environment")
def test_browser_fetch_raises_on_404(fixture_server):
    """A dead link (common when crawling real listing pages) must surface
    as a failure, not as a successful-looking empty extraction -- Playwright's
    goto() doesn't raise on its own for a 4xx/5xx response.
    """
    with pytest.raises(RuntimeError):
        fetch(f"{fixture_server}/this-page-does-not-exist.html", force_browser=True)
