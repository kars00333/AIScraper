"""
HPE job scraper (Part 3 correction) — careers.hpe.com, Phenom People engine.

for 6th company testing, external

Setup:  pip install httpx
Run:    python hpe_scraper.py
"""
import json
import time

import httpx

API_URL = "https://careers.hpe.com/widgets"
PAGE_SIZE = 50

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
}


def build_payload(offset: int, size: int = PAGE_SIZE) -> dict:
    return {
        "lang": "en_us",
        "deviceType": "desktop",
        "country": "us",
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "sortBy": "",
        "subs498ary": "",
        "from": offset,
        "jobs": True,
        "counts": True,
        "all_fields": ["category", "country", "state", "city"],
        "size": size,
        "clearFilters": False,
        "jdsource": "facets",
        "isFilter": True,
        "globalSearchFeature": True,
        "selected_fields": {},
    }


def fetch_page(client: httpx.Client, offset: int, retries: int = 3) -> dict:
    payload = build_payload(offset)
    for attempt in range(retries):
        try:
            resp = client.post(API_URL, json=payload, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def build_job_url(job: dict) -> str:
    # Confirmed live: /us/en/job/<reqId> resolves directly with real job
    # content (not a redirect). applyUrl is a Workday apply-flow URL, not
    # a careers.hpe.com page, so it's not used as the canonical URL here.
    return f"https://careers.hpe.com/us/en/job/{job.get('reqId', '')}"


def scrape_all() -> list:
    jobs, seen = [], set()
    offset = 0
    total_hits = None

    with httpx.Client() as client:
        while True:
            data = fetch_page(client, offset)
            search = data.get("refineSearch", {})
            if total_hits is None:
                total_hits = search.get("totalHits", 0)
                print(f"  API reports {total_hits} total jobs")

            job_list = search.get("data", {}).get("jobs", [])
            if not job_list:
                break

            new_count = 0
            for j in job_list:
                req_id = j.get("reqId", "")
                if not req_id or req_id in seen:
                    continue
                seen.add(req_id)
                new_count += 1
                locations = j.get("multi_location", [])
                jobs.append({
                    "title": j.get("title", ""),
                    "url": build_job_url(j),
                    "location": "; ".join(locations) if locations else j.get("cityStateCountry", ""),
                    "category": j.get("category", ""),
                    "posted_date": j.get("dateCreated", ""),
                    "req_id": req_id,
                })

            offset += len(job_list)
            print(f"  fetched {len(jobs)} unique / {offset} scanned / {total_hits} reported")

            if new_count == 0 or offset >= total_hits:
                break

            time.sleep(0.5)

    return jobs


if __name__ == "__main__":
    print("=== Scraping HPE ===")
    jobs = scrape_all()
    print(f"\nTotal jobs found: {len(jobs)}\n")
    for j in jobs[:10]:
        print(j)
    with open("hpe_jobs.json", "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(jobs)} jobs -> hpe_jobs.json")
