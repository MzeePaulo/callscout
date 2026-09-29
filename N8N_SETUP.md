# Using n8n_workflow.json: LAC Funding Scout, rebuilt on callscout

This replaces the old workflow's Tavily search node, Firecrawl fetch node, and
the whole LangChain agent (`LAC Discovery Agent` + `Structured Output Parser`
+ `Anthropic Chat Model`) with callscout's own `/discover` endpoint plus a
single lightweight classification step. Everything downstream of discovery
(Google Sheets, the urgent/digest emails, the LAC filter) is unchanged.

## What changed, and why

The old workflow's agent did two jobs in one adaptive loop: *find and fetch*
candidates (via Tavily search + Firecrawl fetch, as tools it called
repeatedly), and *judge* them (LAC fit, relevance score, eligibility
summary) against Save the Children's mandate. Since you chose to replace
both Tavily and Firecrawl and rebuild discovery around seed pages, the new
workflow splits those two jobs apart instead of trying to keep them in one
agent loop:

- **Finding and fetching** is now callscout's `/discover` endpoint: you give
  it seed pages you already know list or announce funding calls, and it
  crawls them, fetches each candidate, and runs the same deterministic
  table/PDF-aware date-and-amount extraction as the rest of callscout — no
  search API, no per-query cost, and (per the bug you found on
  climatefinancelab.org) with the off-domain-PDF and amount-suffix and
  deadline-phrasing fixes already in place.
- **Judging** is now a single, non-agentic call to Anthropic's Messages API
  per surviving candidate (the `Classify (Anthropic)` node) — it only
  classifies (LAC fit, relevance score, eligibility summary, instrument
  type, sector focus, applicant type), since callscout already found the
  page and extracted its dates/amounts. This is deliberately not an agent:
  there's nothing left for it to search or fetch, so a tool-calling loop
  would just be slower and more expensive for the same result.

One consequence worth knowing: **the new workflow is narrower than
free-form web search.** It only finds calls linked from seed pages you
point it at, not anything the old agent's Tavily searches might have
turned up from across the open web. Widen coverage by adding more seed
pages to `Build Discover Request`, not by trying to make callscout do
search — see callscout's README for why that trade-off was made
deliberately (`Discovery: crawl + extract, no search API` section).

## New node-by-node flow

```
Weekly Trigger
  -> Get Existing URLs        (Google Sheets read -- feeds known_urls, so
                                already-tracked opportunities aren't reprocessed)
  -> Build Discover Request   (Code -- EDIT SEED_URLS here, see below)
  -> Discover Funding Calls   (HTTP Request -> your callscout /discover)
  -> Looks Like Call?         (Filter -- drops looks_like_call == false)
  -> Classify (Anthropic)     (HTTP Request -> api.anthropic.com/v1/messages)
  -> Parse Classification + Merge  (Code -- combines callscout's fields +
                                     the classifier's judgment fields)
  -> Normalize Deadline        (unchanged from the old workflow)
  -> Upsert to Google Sheet    (unchanged -- same sheet, same columns)
  -> ... everything from here down (Get Undigested Rows, filter, Sort
       Opportunities, Deadline Within 2 Weeks?, the two digest builders,
       both Gmail sends, Filter - LAC Only, Mark as Sent) is byte-for-byte
       the same as your original workflow.
```

## Before you import this into n8n

Three things need filling in -- the workflow runs without errors once these
are set, everything else is ready to go:

1. **Deploy callscout somewhere with a public HTTPS URL.** n8n Cloud can't
   reach localhost. See `DEPLOY.md` for step-by-step Render instructions
   (Dockerfile-based, includes headless Chromium). You'll get a URL like
   `https://callscout-xxxx.onrender.com`.

2. **In n8n, open `Discover Funding Calls`:**
   - Set the **URL** field to `<your callscout URL>/discover`.
   - Under **Credential for HTTP Header Auth**, create a new credential
     (type: *Header Auth*), header name `X-API-Key`, value = the
     `CALLSCOUT_API_KEY` you set when deploying. This replaces the
     `REPLACE_WITH_CREDENTIAL_ID` placeholder in the JSON.

3. **Open `Build Discover Request` and edit `SEED_URLS`** at the top of the
   code (currently placeholder example.org URLs) to the real funder/NGO
   pages you want crawled -- your funder "open calls" pages, grant
   directories, DFI RFP indexes. This is the one part of the whole
   pipeline that has to reflect your own judgment about which sites to
   watch (callscout intentionally ships with no built-in list -- see its
   README).

`Classify (Anthropic)` reuses your existing Anthropic credential
(`Anthropic account 11`) via n8n's Predefined Credential Type auth, so it
should work as soon as it's imported -- if your n8n version doesn't offer
"Anthropic API" in that node's Predefined Credential Type dropdown, switch
it to Header Auth with header `x-api-key` set to your Anthropic key instead
(the node's notes say this too).

## Testing before turning it on

1. Import `n8n_workflow.json` (Import from File in n8n).
2. Fill in the three items above.
3. Manually execute just `Build Discover Request` -> `Discover Funding
   Calls` first (select those two nodes, "Execute step") with 1-2 real
   seed URLs, and check the output looks right before letting it run the
   whole pipeline -- this is the same "test with a URL you already know
   the right answer for" approach used to validate callscout itself.
4. Once that looks right, run the full workflow manually once (don't wait
   for the weekly trigger) and check the Google Sheet gets sensible rows,
   including for a seed page you expect to yield zero new opportunities
   (confirms `known_urls` de-duplication is working).
5. Only then set `active: true` / turn the workflow on.

## What you lose vs. what you gain

Lost: broad web search for opportunities you haven't specifically pointed
callscout at, and the single-call convenience of one agent doing discovery
and judgment together (now two steps, which also means two things that can
individually fail -- check `classification_failed` in the sheet if a row's
judgment fields look empty, meaning Anthropic's response didn't parse as
JSON that run).

Gained: no Tavily/Firecrawl subscription cost, deterministic and auditable
date/amount extraction with `field_confidence` and `looks_like_call` you
can actually check row by row, and a `/discover` endpoint you fully own and
can keep improving without depending on how well a general-purpose scraper
handles funding-call pages specifically.
