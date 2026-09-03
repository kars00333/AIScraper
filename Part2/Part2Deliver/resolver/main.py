#!/usr/bin/env python3
"""
LinkedIn → Career Page Resolver — CLI entrypoint.

Usage:
    python -m resolver.main [--dev-set] [--full] [--clear]

Modes:
    --dev-set   Run against dev-set.csv (105 rows). Company names come from CSV.
    --full      Run against task1-input-3000.csv. Company names extracted from LinkedIn.
    --clear     Clear checkpoint DB before running.
"""

import csv
import html
import sys
import concurrent.futures

import logging
import argparse

from resolver.infra.checkpoint import Checkpoint
from resolver.extractors.linkedin import get_company
from resolver.extractors.career_page import (
    find_career_page,
    is_job_listing_page,
    to_listing_url,
)
from resolver.extractors.ats_detect import detect_ats
from resolver.infra import client
from resolver.config import (
    DEV_SET_PATH,
    INPUT_3000_PATH,
    CHECKPOINT_DB_PATH,
    PREDICTIONS_PATH,
    RESULTS_PATH,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def read_dev_set(path: str) -> list[dict]:
    """Read dev-set.csv — returns rows with linkedin_url and company."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "linkedin_url": row["linkedin_url"].strip(),
                "company": row.get("company", "").strip(),
            })
    return rows


def read_input_urls(path: str) -> list[str]:
    """Read task1-input-3000.csv — returns list of linkedin_urls."""
    urls = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            urls.append(row["linkedin_url"].strip())
    return urls


def process_row(
    linkedin_url: str,
    checkpoint: Checkpoint,
    company_name: str | None = None,
    skip_linkedin: bool = False,
) -> None:
    """
    Process a single row: extract company → find career page → detect ATS.
    Results are saved to checkpoint immediately.
    Failures are recorded as lookup_unavailable; an unwritten row would read
    as "never attempted" and be skipped on resume.
    """
    if checkpoint.already_done(linkedin_url):
        return
    try:
        _resolve_row(linkedin_url, checkpoint, company_name, skip_linkedin)
    except Exception:
        logger.exception("Unhandled error resolving %s", linkedin_url)
        checkpoint.save(linkedin_url, company_name, None, "", "lookup_unavailable")


def _resolve_row(
    linkedin_url: str,
    checkpoint: Checkpoint,
    company_name: str | None,
    skip_linkedin: bool,
) -> None:
    """Resolve one row. Raises on unexpected failure; process_row records it."""

    # ── Get company name ──
    if company_name and skip_linkedin:
        # Dev-set mode: company name provided in CSV
        company = company_name
        c_status = "ok"
    else:
        company, c_status = get_company(linkedin_url)
    if not company:
        checkpoint.save(linkedin_url, None, None, "", c_status)
        logger.info("[%s] %s → no company", c_status, linkedin_url)
        return

    # Names arrive HTML-escaped ("Clyde &amp; Co"); "amp" would otherwise
    # become a matches-anything token.
    company = html.unescape(company).strip()

    # A closed posting still resolves: the job-list page outlives it.
    posting_closed = c_status == "posting_closed"

    # ── Find career page ──
    page_url, p_status, method = find_career_page(company)
    if not page_url:
        via = f"{method}+posting_closed" if posting_closed else method
        checkpoint.save(linkedin_url, company, None, "", p_status, via)
        logger.info("[%s] %s → %s (no page)", p_status, linkedin_url, company)
        return

    # ── Detect ATS ──
    # Re-fetch the page to get HTML for ATS detection
    resp = client.get(page_url)
    page_html = resp.text if resp else ""
    final_url = str(resp.url) if resp and hasattr(resp, 'url') else page_url
    ats = detect_ats(final_url, page_html)

    # Expired posting reported honestly, with the career page filled in.
    final_status = "posting_closed" if posting_closed else "ok"
    checkpoint.save(linkedin_url, company, final_url, ats, final_status, method)
    logger.info("[%s] %s → %s → %s (ats=%s, via=%s)",
                final_status, linkedin_url, company, final_url, ats or "unknown", method)


def run_parallel(target_rows: list[dict], checkpoint: Checkpoint, is_dev: bool) -> None:
    """Run processing in parallel."""
    print(f"Total rows to process: {len(target_rows)}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = []
        for row in target_rows:
            url = row["linkedin_url"]
            company = row.get("company") if is_dev else None
            skip_linkedin = is_dev
            futures.append(
                executor.submit(
                    process_row, url, checkpoint, company, skip_linkedin
                )
            )
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            if completed % 10 == 0:
                logger.info("── Progress: %d/%d ──", completed, len(target_rows))
            try:
                future.result()
            except Exception as e:
                logger.error("Error processing row: %s", e)


def run_dev_set(checkpoint: Checkpoint) -> None:
    """Run against dev-set.csv using provided company names."""
    rows = read_dev_set(DEV_SET_PATH)
    run_parallel(rows, checkpoint, is_dev=True)

    # Export predictions for scorer
    checkpoint.export_predictions_csv(PREDICTIONS_PATH)

    # Print summary
    counts = checkpoint.status_counts()
    logger.info("Dev-set complete. Status counts: %s", counts)
    logger.info("Predictions written to %s", PREDICTIONS_PATH)


def run_full(checkpoint: Checkpoint) -> None:
    """Run against task1-input-3000.csv, extracting company from LinkedIn URL."""
    urls = read_input_urls(INPUT_3000_PATH)
    # create pseudo-rows for parallel
    rows = [{"linkedin_url": url} for url in urls]
    run_parallel(rows, checkpoint, is_dev=False)
    checkpoint.export_csv(RESULTS_PATH)
    counts = checkpoint.status_counts()
    logger.info("Full run complete. Status counts: %s", counts)
    logger.info("Results written to %s", RESULTS_PATH)
    total_done = checkpoint.count()
    logger.info("=" * 50)
    logger.info("FULL RUN COMPLETE")
    logger.info("Total rows: %d", total_done)
    for status, count in sorted(counts.items(), key=lambda x: -x[1]):
        logger.info("  %s: %d (%.1f%%)", status, count, count / total_done * 100)
    logger.info("Results: %s", RESULTS_PATH)
    logger.info("Predictions: %s", PREDICTIONS_PATH)
    logger.info("=" * 50)


def redetect_ats(checkpoint: Checkpoint) -> None:
    """
    Re-derive the ats column for every resolved row.
    Needed when the confirmation rule changes: values written under an older
    rule persist otherwise, and a wrong ats is worse than an empty one.
    """
    rows = checkpoint.resolved_rows()
    logger.info("Re-deriving ATS for %d resolved rows", len(rows))
    changed = cleared = trimmed = 0
    def one(row):
        # Trim to the list first: the deliverable is the openings page.
        url = to_listing_url(row["career_page_url"])
        resp = client.get(url)
        html = resp.text if resp else ""
        final_url = str(resp.url) if resp and hasattr(resp, "url") else url
        if url != row["career_page_url"]:
            # Keep the trim only if the list page validates.
            if not (resp and resp.status_code in (200, 401)
                    and is_job_listing_page(final_url, html)):
                url = row["career_page_url"]
                resp = client.get(url)
                html = resp.text if resp else ""
                final_url = str(resp.url) if resp and hasattr(resp, "url") else url
        return row, url, detect_ats(final_url, html)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        for future in concurrent.futures.as_completed([ex.submit(one, r) for r in rows]):
            try:
                row, url, ats = future.result()
            except Exception:
                logger.exception("Finalize failed for a row")
                continue
            if url != row["career_page_url"]:
                trimmed += 1
                checkpoint.update_url(row["linkedin_url"], url)
            if ats != row["ats"]:
                changed += 1
                if not ats:
                    cleared += 1
                checkpoint.update_ats(row["linkedin_url"], ats)
    logger.info("Finalize complete: %d ats changed (%d cleared), %d urls trimmed to list",
                changed, cleared, trimmed)


def main():
    parser = argparse.ArgumentParser(description="LinkedIn → Career Page Resolver")
    parser.add_argument("--dev-set", action="store_true", help="Run against dev-set.csv")
    parser.add_argument("--full", action="store_true", help="Run against task1-input-3000.csv")
    parser.add_argument("--clear", action="store_true", help="Clear checkpoint DB before running")
    parser.add_argument("--redetect-ats", action="store_true",
                        help="Re-derive the ats column for resolved rows and re-export")
    args = parser.parse_args()
    if args.redetect_ats:
        checkpoint = Checkpoint(CHECKPOINT_DB_PATH)
        try:
            redetect_ats(checkpoint)
            checkpoint.export_csv(RESULTS_PATH)
        finally:
            checkpoint.close()
            client.close()
        return
    if not args.dev_set and not args.full:
        print("Specify --dev-set or --full. Use --help for details.")
        sys.exit(1)
    checkpoint = Checkpoint(CHECKPOINT_DB_PATH)
    if args.clear:
        logger.info("Clearing checkpoint DB")
        checkpoint.clear()
    try:
        if args.dev_set:
            run_dev_set(checkpoint)
        elif args.full:
            run_full(checkpoint)
    finally:
        checkpoint.close()
        client.close()


if __name__ == "__main__":
    main()
