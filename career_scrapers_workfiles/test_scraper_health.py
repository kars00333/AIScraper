"""
Small test harness to check whether the Avature crawler is actually working
right now — not "did it run without an exception", but "is the data it
returned real and complete".

Usage:
    python test_scraper_health.py            # test all 5 companies
    python test_scraper_health.py nbc         # test just one company
    python test_scraper_health.py nbc siemens # test a subset

For each company this runs a LIVE scrape (real network request to the real
site) and checks:
  1. It returned at least one job.
  2. Every job has a non-empty title, a non-empty location, and a URL on the
     expected domain (catches silent redirects to the wrong locale/site).
  3. No duplicate job URLs (catches a broken pagination stop condition that
     re-scrapes the same page forever).
  4. The scraped count matches the site's own reported total (parsed from
     the page banner), when that banner could be found. This is the check
     that catches "only fetched page 1" or "wrong locale returned a
     different, smaller list" bugs.

Exit code is 0 if every tested company passes, 1 otherwise — so this can be
dropped into a cron job or CI step, not just run by hand.
"""
import sys

from avature_multi_scraper import COMPANIES, scrape_company


def parse_reported_total(reported: str):
    """'330' -> 330, '999+' -> None (can't compare an exact count to a
    open-ended '999+' cap, so that check is skipped, not failed)."""
    if not reported:
        return None
    reported = reported.replace(",", "")
    if reported.endswith("+"):
        return None
    try:
        return int(reported)
    except ValueError:
        return None


def test_company(config: dict) -> bool:
    label = config["label"]
    print(f"\n=== {label} ===")

    try:
        jobs, reported_total = scrape_company(config)
    except Exception as e:
        print(f"  FAIL  could not scrape at all: {e}")
        return False

    ok = True

    # 1. Got something at all
    if len(jobs) == 0:
        print("  FAIL  0 jobs returned — site markup or URL likely changed")
        return False
    print(f"  PASS  {len(jobs)} jobs returned")

    # 2. Field completeness + right domain
    bad_title = [j for j in jobs if not j["title"]]
    bad_location = [j for j in jobs if not j["location"]]
    bad_domain = [j for j in jobs if config["domain_check"] not in j["url"]]

    if bad_title:
        print(f"  WARN  {len(bad_title)}/{len(jobs)} jobs have an empty title "
              f"(sample: {bad_title[0]['url']})")
    if bad_location:
        print(f"  WARN  {len(bad_location)}/{len(jobs)} jobs have an empty location "
              f"(sample: {bad_location[0]['url']})")
    if bad_domain:
        print(f"  FAIL  {len(bad_domain)} jobs point off-domain — "
              f"likely redirected to the wrong locale/site "
              f"(sample: {bad_domain[0]['url']})")
        ok = False
    if not bad_title and not bad_location and not bad_domain:
        print("  PASS  every job has title, location, and correct-domain URL")

    # 3. No duplicate URLs
    urls = [j["url"] for j in jobs]
    dupes = len(urls) - len(set(urls))
    if dupes:
        print(f"  FAIL  {dupes} duplicate job URLs — pagination likely stuck "
              f"re-fetching the same page")
        ok = False
    else:
        print("  PASS  no duplicate job URLs")

    # 4. Count matches the site's own banner, if we could read one
    expected = parse_reported_total(reported_total)
    if expected is None:
        print(f"  SKIP  no exact reported total to compare against "
              f"(banner read as {reported_total!r})")
    elif expected == len(jobs):
        print(f"  PASS  scraped count matches site's own total exactly ({expected})")
    else:
        print(f"  FAIL  scraped {len(jobs)} jobs but site reports {expected} — "
              f"pagination is probably incomplete or over-counting")
        ok = False

    return ok


def main():
    requested = sys.argv[1:]
    keys = requested if requested else list(COMPANIES.keys())

    unknown = [k for k in keys if k not in COMPANIES]
    if unknown:
        print(f"Unknown company key(s): {unknown}. "
              f"Valid keys: {list(COMPANIES.keys())}")
        sys.exit(1)

    results = {key: test_company(COMPANIES[key]) for key in keys}

    print("\n=== Summary ===")
    for key, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {COMPANIES[key]['label']}")

    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
