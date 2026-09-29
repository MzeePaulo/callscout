# callscout

Open-source scraper/extractor built for one specific, recurring failure: general-purpose
tools (Firecrawl, Apify, Serper/Tavily-driven agents) reliably *fetch* a funding-call page
but silently drop the fields that actually matter for triage — **opening date, deadline,
amount range** — because those facts usually live in an HTML table, a `<dl>` layout, or a
linked PDF, not in the clean prose that markdown-flattening extraction was built for.

callscout fixes this by treating tables and PDFs as first-class sources and running a fast,
deterministic date/amount parser *before* ever calling an LLM. It only asks an LLM (optional)
to fill in whatever the deterministic pass still couldn't find, so the common case is free
and auditable, and every extracted value carries the exact source snippet it came from.

## Why this exists

Built against a real, reported failure: an n8n workflow ("LAC Funding Scout") using a
LangChain agent with Tavily search + Firecrawl fetch was returning blank `opening_date`,
`deadline`, and `amount_range` for most opportunities, even though the information was
present on the source sites. The failure modes turned out to be structural, not
prompt-tuning problems:

1. **Tables and `<dl>` layouts get flattened to unstructured text** before the LLM ever
   sees them, so "Deadline | 2026-05-15" becomes an ambiguous run-on sentence.
2. **The real numbers are often in a linked PDF** (guidelines/RFP document), not the landing
   page most scrapers stop at.
3. **JS-rendered funder portals** return near-empty HTML to a plain HTTP GET; the content
   only exists after client-side rendering.

## How it works

```
fetch (httpx, escalates to headless Chromium if the static page looks too thin)
  -> extract_text (trafilatura for prose + BeautifulSoup for tables and <dl> pairs)
  -> extract_pdf (pdfplumber on any linked .pdf, merged into the same page content)
  -> parse_fields (deterministic: table/label lookup, dateparser, currency-range regex)
  -> llm_fallback (optional, Anthropic API — only called for fields still missing)
  -> Opportunity (pydantic model, .to_sheet_row() matches the existing Google Sheet columns)
```

Every field (`opening_date`, `deadline`, `amount_range`) carries a `confidence`:
`table` > `pattern` > `llm` > `missing`, plus the `source_snippet` it was read from — so you
can audit *why* a value was accepted, and tell "genuinely not published" apart from
"extraction failed."

### Precision: false positives are worse than blanks

A blank field is a visible gap you notice and check by hand. A *wrong* value that looks
plausible — a market-size statistic mistaken for a grant amount, say — is worse, because it
sails into the sheet unchallenged. So the amount parser doesn't just pattern-match any
currency figure it finds:

- `find_amount(..., require_context=True)` (used for any unscoped scan of a whole page) checks
  the text around every candidate. It's rejected outright near market/cumulative-volume
  language ("mobilized $231 billion to-date", "a $4.2 trillion funding gap", "transactions
  range from $110,000 to $8 billion") and only accepted near applicant-funding language
  ("grant of up to...", "financing available per project...") or a recognized amount label.
- Every `Opportunity` carries a `looks_like_call` flag: whether the page actually reads as a
  live call (deadline/apply/eligibility language present) versus an informational or
  market-primer page. `False` doesn't mean the extracted fields are wrong — it means review
  the row before trusting it, since the whole page might not be an actual open opportunity.

`tests/fixtures/market_primer.html` and `tests/test_pipeline.py::test_market_primer_page_yields_no_false_amount`
are a regression test built from this exact failure mode (reproduced from a real informational
blended-finance page), so a future change can't silently reintroduce it.

### Fixes found by testing against a real page

Running this against one real, live call page (not a fixture) turned up three more bugs,
now fixed and covered by `tests/test_pipeline.py::test_off_domain_pdf_is_not_followed_and_deadline_phrasing_is_caught`:

- **Off-domain PDFs were contaminating extraction.** A real call page linked both its own
  guidelines PDF and an unrelated background report hosted on a different domain; following
  both meant the background report's own dollar figures leaked into `amount_range`. PDF
  links are now followed same-domain-only by default (`extract(..., include_cross_domain_pdfs=True)`
  / `run(..., include_cross_domain_pdfs=True)` to opt back in).
- **"Submit your application by \<date\>" wasn't recognized as a deadline at all.** No word
  "deadline" appears in that sentence, and it's a very common way real calls actually word
  it. `DEADLINE_LABELS` now covers "submit ... by", "apply by", "no later than", "on or
  before", and similar phrasing.
- **"$2 billion" was silently parsed as "$2".** The amount regex's suffix vocabulary never
  included "billion"/"bn" -- a bare number a thousand-million short is a much worse failure
  than a blank field. Fixed, alongside adding `PREFER_DATES_FROM: future` so a bare
  "October 18" with no year resolves to the next upcoming one rather than one that's
  already passed.

Also worth knowing: `fetch.py`'s `USER_AGENT` was changed from a self-identifying bot string
to an ordinary browser string, because the honest bot UA got both the real call page and its
PDF blocked with a 403 by that site's bot protection (only Playwright's real Chromium engine,
a different UA, got through). This is a genuine trade-off between politeness and reliability,
documented in `fetch.py` where you can change it back if you'd rather.

## Install

```bash
pip install -e .
playwright install chromium   # only needed if you want the JS-rendering fallback
```

## Use it

```bash
# single URL
callscout extract "https://example-funder.org/call" --json

# a batch, written to CSV in the same columns as the Google Sheet
callscout batch urls.txt --out-csv opportunities.csv
```

Or as a service, for n8n:

```bash
uvicorn callscout.api:app --host 0.0.0.0 --port 8000
```

In n8n, replace the Firecrawl node in a workflow like LAC Funding Scout with an
**HTTP Request** node: `POST http://<host>:8000/extract/sheet-row` with body
`{"url": "{{$json.url}}"}`. The response's keys already match the Opportunities sheet
columns, so it can feed a Google Sheets "Append/Update" node directly — no Set/Function
node needed in between. This also means you can likely drop the LangChain agent step
entirely and cut the Anthropic token spend, keeping the LLM fallback only for the fields
that are still empty.

Set `ANTHROPIC_API_KEY` in the environment to enable the LLM fallback; without it,
callscout still runs, just deterministic-only (`extraction_notes` will say so).

If `CALLSCOUT_API_KEY` is set in the environment, every `/extract`, `/extract/sheet-row`,
and `/discover` request must send a matching `X-API-Key` header or gets a 401. Leave it
unset for local/offline use; set it once the service has a public URL (see below) so it
isn't an open endpoint anyone can point at arbitrary URLs.

### Deploying so n8n Cloud can reach it

n8n Cloud (n8n.io) can't reach `localhost` or your private network -- callscout needs a
public HTTPS URL for its HTTP Request node to call. See **[DEPLOY.md](DEPLOY.md)** for a
step-by-step, **free** deployment on Render (Dockerfile-based; `CALLSCOUT_ALLOW_BROWSER=false`
keeps it comfortably inside the free plan's RAM allowance) and what to set in n8n once it's
live.

## Discovery: crawl + extract, no search API

`callscout discover` replaces the search + fetch + LangChain-agent stage of a workflow
like LAC Funding Scout with one call — built entirely from this codebase's own fetch and
parsing layer, with no third-party search API, no vendor SDK, and no per-query cost.

You give it **seed pages**: pages you already know announce or list funding calls (a
funder's own "open calls" page, an NGO grants directory, a DFI's RFP index, a foundation's
news page — the same kind of page you'd normally check by hand). `crawler.py` fetches
each one with the same `httpx`/Playwright fetch layer used everywhere else in callscout,
pulls out every link that reads like an actual call (`CALL_LINK_PATTERN` in `crawler.py`
covers "call for proposals", "RFP"/"RFQ"/"RFI", "EOI", "grant opportunity", "NOFO",
"tender", and more — edit it for your own vocabulary), and runs the *same* table/PDF-aware,
context-gated extraction from `extract`/`batch` on every one found.

This is narrower than general web search — it only finds calls linked from a seed page
you already pointed it at — but everything it finds is on-topic by construction, and the
whole thing runs on infrastructure you own.

```bash
cat > seeds.txt <<EOF
https://some-funder.org/open-calls
https://some-ngo-directory.org/grants
EOF

callscout discover seeds.txt --out-csv discovered.csv

# skip URLs you've already tracked (export source_url from your Google Sheet)
callscout discover seeds.txt --known-urls-file already_tracked.txt --out-csv discovered.csv
```

As a service, for n8n — this can replace the search/fetch/agent nodes entirely, feeding
straight into your Google Sheets node:

```bash
uvicorn callscout.api:app --host 0.0.0.0 --port 8000
```

`POST /discover` with body `{"seed_urls": ["https://some-funder.org/open-calls"], "known_urls": [...]}`
returns a list of sheet-ready rows, one per discovered opportunity.

Two things worth knowing:

- **The seed list is yours to curate.** callscout ships with no hardcoded list of funder
  or directory sites — that would be a guess about which sites are still live, freely
  crawlable, and relevant to your programme, which is exactly the kind of unverifiable
  claim not worth shipping. Start from the pages you already check by hand.
- `crawl_seed()`/`crawl_seeds()` in `crawler.py` are independent of extraction, so you can
  point them at a seed page and inspect the candidate links found before spending an
  extraction pass on each one.

## Testing

```bash
pip install -e ".[dev]"
python -m pytest -v
```

The suite runs fully offline: `tests/conftest.py` spins up a local HTTP server over
`tests/fixtures/`, so `fetch.py`'s real `httpx` client (and, where relevant, real
Playwright) run against real network I/O with known, hand-checked expected values —
no mocking of the fetch layer.

- `tests/test_parse_fields.py` — unit tests for the date/amount regex and label matching
  (the module most directly responsible for fixing blank fields).
- `tests/test_extract_text.py` — the same-domain PDF filter that stops an unrelated,
  off-domain document from contaminating extraction.
- `tests/test_pipeline.py` — end-to-end fixtures mirroring the real layout patterns behind
  the reported failure: a definition-table page, a prose-only page, a `<dl>`-based page, a
  landing page whose real deadline/amount only exist in a linked PDF, and a page combining
  an own-domain PDF with an off-domain one alongside "submit by" deadline phrasing.
- `tests/test_browser_fallback.py` — confirms the static-fetch-looks-thin heuristic actually
  escalates to a headless browser, and that the browser render recovers JS-injected content.
  Skips automatically if Chromium isn't available.
- `tests/test_crawler.py` — link-pattern matching, dedup, and a full crawl-to-extraction
  run against a local fixture "listing" page, including a dead link that must fail
  gracefully rather than take down the rest of the batch.

### Building your own golden set

Before pointing this at your real target sites (e.g. Convergence A4FM, Humanity Insured,
AFCIA/CTCN), save 15-20 real pages you already know the right answer for as local
fixtures, hand-label the expected `opening_date` / `deadline` / `amount_range`, and add
them to `tests/fixtures/` with a matching test case. That turns "did the scraper run"
into "did extraction accuracy actually improve," and protects against silent regressions
when a funder redesigns their site. Track it as a simple per-field precision/recall count,
not just pass/fail on the whole record — a tool that gets 2 of 3 fields right on a page is
more useful than one that returns nothing.

## Project layout

```
src/callscout/
  fetch.py          static + headless-browser fetch, with auto-escalation
  extract_text.py   HTML -> prose + tables + <dl> pairs
  extract_pdf.py    linked-PDF text + tables
  parse_fields.py   deterministic date/amount extraction (the core fix)
  llm_fallback.py   optional Anthropic API fallback for fields still missing
  crawler.py        from-scratch discovery: crawl your own seed pages -> candidate URLs
  schema.py         Opportunity pydantic model, sheet-row flattening
  pipeline.py       orchestrates the above (run() and discover_and_extract())
  cli.py            `callscout extract` / `batch` / `discover`
  api.py            FastAPI service for n8n's HTTP Request node (/extract, /discover)
tests/
  fixtures/         synthetic pages covering each real-world failure mode
  test_*.py
```

## License

MIT — see `LICENSE`.
