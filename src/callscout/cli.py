from __future__ import annotations

import csv
import json
import sys

import typer

from .pipeline import discover_and_extract, run

app = typer.Typer(help="callscout: extract opening dates, deadlines, and amount ranges from funding-call pages.")


@app.command()
def extract(
    url: str,
    json_out: bool = typer.Option(False, "--json", help="Print the full record as JSON."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip the LLM fallback even if ANTHROPIC_API_KEY is set."),
    force_browser: bool = typer.Option(False, "--browser", help="Always render with a headless browser."),
):
    """Extract one URL and print the result."""
    opp = run(url, use_llm_fallback=not no_llm, force_browser=force_browser)
    if json_out:
        typer.echo(opp.model_dump_json(indent=2))
    else:
        row = opp.to_sheet_row()
        for k, v in row.items():
            typer.echo(f"{k}: {v}")


@app.command()
def batch(
    urls_file: str = typer.Argument(..., help="Text file, one URL per line."),
    out_csv: str = typer.Option("opportunities.csv", help="Output CSV path."),
    no_llm: bool = typer.Option(False, "--no-llm"),
):
    """Extract many URLs and write a CSV matching the Google Sheet columns."""
    with open(urls_file) as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    rows = []
    for url in urls:
        typer.echo(f"extracting {url} ...", err=True)
        try:
            opp = run(url, use_llm_fallback=not no_llm)
            rows.append(opp.to_sheet_row())
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"  failed: {exc}", err=True)
            rows.append({"source_url": url, "title": "", "opening_date": "", "deadline": "",
                         "amount_range": "", "amount_min": "", "amount_max": "", "currency": "",
                         "summary": f"ERROR: {exc}", "looks_like_call": "",
                         "discovered_via_seed": "", "field_confidence": ""})

    if rows:
        with open(out_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    typer.echo(f"wrote {len(rows)} rows to {out_csv}")


@app.command()
def discover(
    seeds_file: str = typer.Argument(..., help="Text file, one seed/listing URL per line -- pages you already know announce or list funding calls."),
    known_urls_file: str = typer.Option(None, help="Text file of URLs already tracked (e.g. exported from your Google Sheet) to skip re-discovering."),
    out_csv: str = typer.Option("discovered.csv", help="Output CSV path."),
    max_links_per_seed: int = typer.Option(30, help="Max call-like links to pull from each seed page."),
    same_domain_only: bool = typer.Option(False, "--same-domain-only", help="Only follow links that stay on the seed page's own domain."),
    no_llm: bool = typer.Option(False, "--no-llm"),
):
    """Crawl your own seed/listing pages for call-like links, then extract
    every one found. No search API, no API key -- entirely self-hosted.
    """
    with open(seeds_file) as f:
        seed_urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    known_urls = None
    if known_urls_file:
        with open(known_urls_file) as f:
            known_urls = {line.strip() for line in f if line.strip()}

    typer.echo(f"crawling {len(seed_urls)} seed page(s) ...", err=True)
    opportunities = discover_and_extract(
        seed_urls,
        known_urls=known_urls,
        max_links_per_seed=max_links_per_seed,
        only_same_domain=same_domain_only,
        use_llm_fallback=not no_llm,
    )

    rows = [opp.to_sheet_row() for opp in opportunities]
    if rows:
        with open(out_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    typer.echo(f"found {len(rows)} candidate URLs, wrote to {out_csv}")


if __name__ == "__main__":
    app()
