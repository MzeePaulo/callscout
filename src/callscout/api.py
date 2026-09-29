"""Thin FastAPI wrapper so n8n's HTTP Request node can call this straight
out of a container, replacing the Firecrawl node in a workflow like
LAC Funding Scout.

Run locally:
    uvicorn callscout.api:app --host 0.0.0.0 --port 8000

n8n HTTP Request node: POST http://<host>:8000/extract  body: {"url": "..."}

Auth: if CALLSCOUT_API_KEY is set in the environment, every request must
send a matching `X-API-Key` header or gets a 401. This matters once the
service has a public URL (e.g. deployed for n8n Cloud to reach) -- an
unauthenticated endpoint that fetches arbitrary URLs on request is an easy
abuse vector for anyone who finds it, not just your own workflow. Leaving
CALLSCOUT_API_KEY unset (e.g. for local/offline testing) disables the
check entirely, so this is opt-in, not a breaking change.

Memory: set CALLSCOUT_ALLOW_BROWSER=false to disable the headless-browser
fetch fallback for JS-rendered pages. This is here specifically for free
hosting tiers with a small RAM ceiling (e.g. Render's free plan) -- the
Chromium fallback only launches occasionally (when a static fetch looks
too thin), but when it does it can be the difference between fitting in
512MB and getting OOM-killed. Leaving it unset keeps the fallback on,
which is more accurate but a little more memory-hungry.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .pipeline import discover_and_extract, run
from .schema import Opportunity

app = FastAPI(title="callscout", version="0.1.0")

def _allow_browser() -> bool:
    return os.environ.get("CALLSCOUT_ALLOW_BROWSER", "true").strip().lower() not in ("false", "0", "no")


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.environ.get("CALLSCOUT_API_KEY")
    if not expected:
        return  # auth disabled -- no key configured
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key header")


class ExtractRequest(BaseModel):
    url: str
    use_llm_fallback: bool = True
    force_browser: bool = False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extract", response_model=Opportunity, dependencies=[Depends(require_api_key)])
def extract(req: ExtractRequest):
    try:
        return run(
            req.url,
            use_llm_fallback=req.use_llm_fallback,
            force_browser=req.force_browser,
            allow_browser=_allow_browser(),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"extraction failed: {exc}") from exc


@app.post("/extract/sheet-row", dependencies=[Depends(require_api_key)])
def extract_sheet_row(req: ExtractRequest):
    """Same as /extract but flattened to the exact columns the Google
    Sheet expects, so the n8n HTTP node can feed the response straight
    into a Google Sheets "Append/Update" node with no Set/Function node
    in between.
    """
    try:
        opp = run(
            req.url,
            use_llm_fallback=req.use_llm_fallback,
            force_browser=req.force_browser,
            allow_browser=_allow_browser(),
        )
        return opp.to_sheet_row()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"extraction failed: {exc}") from exc


class DiscoverRequest(BaseModel):
    seed_urls: list[str]
    known_urls: Optional[list[str]] = None
    max_links_per_seed: int = 30
    only_same_domain: bool = False
    use_llm_fallback: bool = True


@app.post("/discover", dependencies=[Depends(require_api_key)])
def discover(req: DiscoverRequest):
    """Crawl your own seed/listing pages for call-like links, then extract
    every one found -- no search API, no vendor SDK, nothing but this
    codebase's own fetch/parse layer. This is the node that can replace an
    n8n workflow's search + fetch + LangChain-agent stage entirely: give
    it pages you already know list or announce funding calls, get back
    sheet-ready rows for every candidate found on them.
    """
    try:
        opportunities = discover_and_extract(
            req.seed_urls,
            known_urls=set(req.known_urls) if req.known_urls else None,
            max_links_per_seed=req.max_links_per_seed,
            only_same_domain=req.only_same_domain,
            use_llm_fallback=req.use_llm_fallback,
            allow_browser=_allow_browser(),
        )
        return [opp.to_sheet_row() for opp in opportunities]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"discovery failed: {exc}") from exc
