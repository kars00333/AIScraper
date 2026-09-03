"""
EA (Electronic Arts) job scraper — FIXED version (standalone, no production repo needed)

Based on an earlier EA scraper, with three fixes so the same pattern is safe to
reuse on other Avature companies:
  1. Removed the range(30) hard cap of 600 jobs -> now pages until an empty page
     (companies with 999+ jobs no longer lose ~400 rows silently).
  2. Added request retries in get_html (a single network hiccup / rate-limit no
     longer drops a whole page).
  3. Saves the result to JSON for easy handoff.

Setup (once):  pip install httpx beautifulsoup4
Run:           python B_EA_scraper_FIXED_EN.py
"""
import json
import time

import httpx
from bs4 import BeautifulSoup

BASE = ("https://jobs.ea.com/en_US/careers/SearchJobs/?listFilterMode=1&jobSort=id"
        "&jobSortDirection=DESC&jobRecordsPerPage=20&jobOffset={offset}")
STEP = 20
MAX_PAGES = 1000  # Safety valve only; normally stops via the "empty page" break.
                  # Set high so companies with 999+ jobs are fully captured.
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}


def get_html(url, retries=3):
    """GET with retries: a transient network error / rate-limit won't drop a whole page."""
    for attempt in range(retries):
        try:
            return httpx.get(url, headers=HEADERS, timeout=30).text
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))  # backoff: 2s, 4s


def extract_location(a_tag):
    """
    The title <a> sits inside <h3 class="article__header__text__title">. Its parent
    <div class="article__header__text"> holds one or more <span class="list-item-location">
    (jobs with multiple locations have several spans).
    """
    header_text_div = a_tag.find_parent("div", class_="article__header__text")
    if not header_text_div:
        return ""
    spans = header_text_div.select("span.list-item-location")
    return "; ".join(s.get_text(strip=True) for s in spans)


def scrape_all():
    seen, jobs = set(), []
    page = 0
    while page < MAX_PAGES:                       # not a hard-coded range(30) anymore
        html = get_html(BASE.format(offset=page * STEP))
        soup = BeautifulSoup(html, "html.parser")

        new_count = 0
        for a in soup.select('a[href*="/careers/JobDetail/"]'):
            href = a.get("href", "")
            if not href or "_linkedinApiv2" in href or href in seen:
                continue
            seen.add(href)
            new_count += 1
            jobs.append({
                "title": a.get_text(strip=True),
                "url": href,
                "location": extract_location(a),
            })

        if new_count == 0:                        # real stop condition: an empty page
            break
        page += 1
    return jobs


if __name__ == "__main__":
    jobs = scrape_all()
    print(f"Total jobs found: {len(jobs)}\n")
    for j in jobs[:10]:
        print(j)
    with open("ea_jobs.json", "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(jobs)} jobs -> ea_jobs.json")
