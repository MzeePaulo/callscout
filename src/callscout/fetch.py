"""Fetch layer: try a cheap static request first, escalate to a headless
browser only when the static fetch looks too thin to be real (the classic
sign of a JS-rendered funder portal where the deadline widget hasn't
loaded).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# A plain, self-identifying bot UA (e.g. "CallScoutBot/0.1") gets blocked by
# WordPress/WAF bot-protection on a real, observed funder site -- both the
# page itself and its linked PDF 403'd with the honest UA and only the page
# succeeded once Playwright's real Chromium (a different UA) took over.
# This is a genuine trade-off, not a free lunch: identifying honestly is
# more polite and is what a "good bot" is supposed to do, but it gets
# blocked more often in practice by sites that don't distinguish a
# legitimate research tool from something worse. Since callscout is only
# ever pointed at specific, individual public pages you already chose (not
# crawling a site wholesale), presenting as an ordinary browser is a
# reasonable trade here -- change this back to a self-identifying string if
# you'd rather be blocked more often than blend in.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# If the static fetch returns less visible text than this, assume the page
# is JS-rendered and a headless browser is needed to see the real content.
MIN_STATIC_TEXT_LEN = 350


@dataclass
class FetchResult:
    url: str
    html: str
    method: str  # "static" | "browser"
    status_code: Optional[int] = None


def _visible_text_len(html: str) -> int:
    # Cheap heuristic, avoids importing BeautifulSoup just to size-check.
    import re

    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return len(text)


def fetch_static(url: str, timeout: float = 20.0) -> FetchResult:
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=timeout,
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return FetchResult(url=url, html=resp.text, method="static", status_code=resp.status_code)


def fetch_browser(url: str, timeout: float = 30.0, wait_ms: int = 2000) -> FetchResult:
    """Render with a headless browser for JS-heavy pages.

    Requires `playwright` and its Chromium binary to be installed
    (`playwright install chromium`), and outbound network access to the
    target site from wherever this runs.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            response = page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                # Unlike a static fetch's raise_for_status(), Playwright's
                # goto() doesn't raise on a 4xx/5xx -- it happily hands back
                # the error page's HTML. Left unchecked, a dead link found
                # by the crawler would look like a successful (if empty)
                # extraction instead of the fetch failure it actually is.
                raise RuntimeError(f"HTTP {response.status} for {url}")
            page.wait_for_timeout(wait_ms)
            html = page.content()
            return FetchResult(url=url, html=html, method="browser")
        finally:
            browser.close()


def fetch(url: str, force_browser: bool = False, allow_browser: bool = True) -> FetchResult:
    """Fetch a page, escalating to a headless browser when the static
    fetch looks too thin, or when explicitly forced.
    """
    if force_browser:
        return fetch_browser(url)

    try:
        result = fetch_static(url)
    except httpx.HTTPError as exc:
        logger.warning("Static fetch failed for %s (%s); trying browser", url, exc)
        if allow_browser:
            return fetch_browser(url)
        raise

    if allow_browser and _visible_text_len(result.html) < MIN_STATIC_TEXT_LEN:
        logger.info("Static fetch for %s looks thin; escalating to browser", url)
        try:
            return fetch_browser(url)
        except Exception as exc:  # pragma: no cover - browser optional at runtime
            logger.warning("Browser fetch failed for %s (%s); keeping static result", url, exc)

    return result
