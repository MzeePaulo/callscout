# Deploying callscout for free, for n8n Cloud

n8n Cloud (n8n.io) runs on n8n's own infrastructure and cannot reach
`localhost` or anything on your private network. For its HTTP Request node
to call callscout, callscout needs a public HTTPS URL somewhere. This is
the free way to get one.

## Why Render's free plan

Render's free web service plan costs nothing, deploys straight from this
repo's `Dockerfile` with no extra setup, and gives you 750 free instance
hours a month -- more than enough for a workflow that only calls it a
handful of times a week. The trade-off, and the reason this is "free" and
not "free with no catches": a free service spins down after 15 minutes
with no traffic and takes about a minute to wake back up on the next
request, and its RAM allowance is small. Neither matters for a weekly n8n
run once you know to expect that first-request delay -- see the notes at
the bottom.

## Step 1: get the code into a Git repository

Render deploys from Git. If this project isn't in one yet:

```bash
cd callscout
git init
git add .
git commit -m "callscout: funding-call scraper/extractor"
```

Create a new repository on GitHub (github.com/new -- private is fine,
Render just needs read access) and push:

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

## Step 2: create a free Render account and service

1. Go to [render.com](https://render.com) and sign up (GitHub sign-in is
   the fastest route -- it also handles the repo access grant for you).
2. Dashboard -> **New +** -> **Blueprint**.
3. Point it at the GitHub repo from step 1. Render reads this project's
   `render.yaml` and pre-fills everything: Docker runtime, the free plan,
   the `/health` check.
4. Click through to create the service.

(No Blueprint option, or you'd rather do it by hand: **New +** -> **Web
Service** instead, pick the repo, set **Runtime** to `Docker`, and pick the
**Free** instance type. Leave build/start commands blank -- the
`Dockerfile` handles both.)

## Step 3: set the two environment variables

Render's Blueprint flow will prompt for these; if you went the manual
route, add them under the service's **Environment** tab:

- `CALLSCOUT_API_KEY` -- **set this to a random string.** Generate one
  with `openssl rand -hex 32` (or any password generator) and save it
  somewhere -- you'll paste it into n8n as a header value in a minute.
  Skipping this leaves the service open to anyone who finds the URL and
  points it at arbitrary sites, which is a real abuse risk once it's
  public.
- `CALLSCOUT_ALLOW_BROWSER` -- already set to `false` in `render.yaml`.
  This keeps the headless-Chromium fallback off, so the free plan's small
  RAM allowance isn't at risk of an out-of-memory crash. It means a
  JS-rendered funder portal (rare, but it happens) falls back to whatever
  the plain static fetch got rather than escalating to a real browser --
  a real, if occasional, accuracy trade for staying on the free plan. See
  "What CALLSCOUT_ALLOW_BROWSER=false costs you" below.
- `ANTHROPIC_API_KEY` -- optional, only if you want the LLM fallback for
  fields the deterministic parser still can't find (also used by the
  n8n workflow's own classification step, which calls Anthropic directly,
  not through this service).

## Step 4: deploy and verify

Render builds the image (a few minutes the first time) and gives you a
URL like `https://callscout-xxxx.onrender.com`. Confirm it's alive:

```bash
curl https://callscout-xxxx.onrender.com/health
# {"status": "ok"}
```

If that hangs for 30-60 seconds before responding, that's the free
service waking up from being spun down -- expected, not a bug. Then try a
real extraction:

```bash
curl -X POST https://callscout-xxxx.onrender.com/extract/sheet-row \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your CALLSCOUT_API_KEY>" \
  -d '{"url": "https://some-real-open-call-page"}'
```

A 401 means the header is missing or wrong. A 502 means callscout reached
the URL but extraction failed -- check `extraction_notes` in the response
body for why.

## Step 5: point n8n at it

This repo's `n8n_workflow.json` already has the right node for this
(`Discover Funding Calls`) -- see `N8N_SETUP.md` for exactly what to fill
in. In short: set that node's URL to
`https://callscout-xxxx.onrender.com/discover`, and create a Header Auth
credential with header `X-API-Key` set to your `CALLSCOUT_API_KEY`.

## What CALLSCOUT_ALLOW_BROWSER=false costs you

callscout's fetch layer normally escalates to a real headless browser when
a static HTTP fetch looks suspiciously thin (under ~350 characters of
visible text) -- the classic sign of a JS-rendered page where the actual
deadline/amount content only exists after client-side rendering. With the
fallback off, those pages just get whatever the thin static fetch
returned, which can mean a blank field rather than a wrong one -- still
consistent with callscout's "a blank is safer than a guess" design, but
less complete than it could be.

If you hit a real funder site that needs the browser fallback and are
willing to spend a little, the fix is a one-line change: set
`CALLSCOUT_ALLOW_BROWSER` to `true` (or remove it) and upgrade the Render
service to the **Starter** plan (paid, more RAM, no spin-down). Everything
else about the deployment stays the same.

## Notes on cold starts and timeouts

- The first request after 15 minutes of inactivity takes ~30-60s while
  Render wakes the service back up. If n8n's HTTP Request node has a
  short timeout configured, raise it (to ~120s) for the callscout nodes,
  or set `CALLSCOUT_ALLOW_BROWSER` aside and rely on the fact that a
  weekly scheduled run happens rarely enough that this barely matters in
  practice.
- `/discover` can take a while on a seed page with many candidate links,
  since it extracts every one it finds. If a seed page routinely produces
  a large batch, raise the node's timeout further, or lower
  `max_links_per_seed` in the request body (the `Build Discover Request`
  code node in `n8n_workflow.json` sets this to 30 by default).
- Render's free plan gives 750 instance-hours per workspace per month; a
  service that's only called a few times a week uses a tiny fraction of
  that, so you're very unlikely to hit the monthly cap.
