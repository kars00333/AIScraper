"""
HUGO BOSS job scraper (Part 4) — careers.hugoboss.com, Phenom People engine.

STATUS: CONFIRMED — uses Phenom's JSON widget API, no browser required.

The original skeleton used Playwright to drive a headless browser, but
inspection of the live site revealed a JSON API at:
    POST https://careers.hugoboss.com/widgets
(found via the phApp.widgetApiEndpoint config embedded in the page's
JavaScript). This endpoint returns structured job data directly, making
a browser entirely unnecessary.

This version uses plain httpx to hit that API, paginating via the 'from'
parameter until all jobs are fetched. It's faster, more reliable, and has
no dependency on Playwright or browser binaries.

Setup (once):  pip install httpx
Run:           python hugoboss_scraper.py
"""
import json
import time

import httpx

API_URL = "https://careers.hugoboss.com/widgets"
PAGE_SIZE = 50  # max per request (Phenom typically allows up to 100)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
}


def build_payload(offset: int, size: int = PAGE_SIZE) -> dict:
    """Build the Phenom widget API request payload for job search."""
    return {
        "lang": "en_global",
        "deviceType": "desktop",
        "country": "global",
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "sortBy": "",
        "subs498ary": "",
        "from": offset,
        "jobs": True,
        "counts": True,
        "all_fields": [
            "category", "country", "state", "city",
            "hiringType", "workExperience", "contractType",
        ],
        "size": size,
        "clearFilters": False,
        "jdsource": "facets",
        "isFilter": True,
        "globalSearchFeature": True,
        "selected_fields": {},
    }


def fetch_page(client: httpx.Client, offset: int, retries: int = 3) -> dict:
    """POST to the widget API with retries."""
    payload = build_payload(offset)
    for attempt in range(retries):
        try:
            resp = client.post(
                API_URL,
                json=payload,
                headers=HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def build_job_url(job: dict) -> str:
    """Construct the canonical job detail URL from the API response."""
    # Phenom job URLs follow: /global/en/job/<reqId>
    req_id = job.get("reqId", "")
    return f"https://careers.hugoboss.com/global/en/job/{req_id}"


def scrape_all() -> list:
    jobs = []
    offset = 0
    total_hits = None

    with httpx.Client() as client:
        while True:
            data = fetch_page(client, offset)
            search = data.get("refineSearch", {})
            hits = search.get("hits", 0)
            if total_hits is None:
                total_hits = search.get("totalHits", 0)
                print(f"  API reports {total_hits} total jobs")

            job_list = search.get("data", {}).get("jobs", [])
            if not job_list:
                break

            for j in job_list:
                locations = j.get("multi_location", [])
                jobs.append({
                    "title": j.get("title", ""),
                    "url": build_job_url(j),
                    "location": "; ".join(locations) if locations else j.get("cityStateCountry", ""),
                    "category": j.get("category", ""),
                    "posted_date": j.get("postedDate", ""),
                    "req_id": j.get("reqId", ""),
                })

            offset += len(job_list)
            print(f"  fetched {offset} / {total_hits} jobs")

            if offset >= total_hits:
                break

            # Be polite — small delay between pages
            time.sleep(0.5)

    return jobs


if __name__ == "__main__":
    print("=== Scraping HUGO BOSS ===")
    jobs = scrape_all()
    print(f"\nTotal jobs found: {len(jobs)}\n")
    for j in jobs[:10]:
        print(j)
    with open("hugoboss_jobs.json", "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(jobs)} jobs -> hugoboss_jobs.json")
