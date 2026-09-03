"""
Added Avature job scraper — Lenovo, L'Oréal, National Bank of
Canada, Siemens, and Coca-Cola HBC in a single script.

To add new company add an entry to COMPANIES; the shared scraping
code doesn't need to change unless the new company's theme needs a
`layout` mode that extract_location() doesn't already support.

Setup:  pip install httpx beautifulsoup4
Run:    python avature_multi_scraper.py
"""
import concurrent.futures
import json
import re
import time
import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}

COMPANIES = {
    "lenovo": {
        "label": "Lenovo",
        "base": "https://jobs.lenovo.com/en_US/careers/SearchJobs/?jobRecordsPerPage={step}&jobOffset={offset}",
        "step": 10,  # server forces 10/page regardless of jobRecordsPerPage requested
        "layout": "subtitle_first_span",  # no list-item-location class on this theme
        "job_link_pattern": r"/careers/JobDetail/",
        "domain_check": "jobs.lenovo.com",
    },
    "loreal": {
        "label": "L'Oréal",
        "base": "https://careers.loreal.com/en_US/jobs/SearchJobs?jobRecordsPerPage={step}&jobOffset={offset}",
        "step": 20,
        "layout": "subtitle_first_span",  # same theme as Lenovo
        "job_link_pattern": r"/jobs/JobDetail/",
        "domain_check": "careers.loreal.com",
        # Job detail pages embed a dataLayer.push({...}) with a richer
        # location plus country/function/division/employment/position type.
        "detail_enrichment": "loreal_datalayer",
    },
    "nbc": {
        "label": "National Bank of Canada",
        "base": "https://emplois.bnc.ca/en_CA/careers/SearchJobs/?jobRecordsPerPage={step}&jobOffset={offset}",
        "step": 20,
        "layout": "table",  # plain HTML <table>, not an Avature card list
        "job_link_pattern": r"/careers/JobDetail/",
        "domain_check": "emplois.bnc.ca",
    },
    "siemens": {
        "label": "Siemens",
        # /externaljobs/, not /careers/; pagination uses folder*, not job*.
        "base": "https://jobs.siemens.com/en_US/externaljobs/SearchJobs/?folderRecordsPerPage={step}&folderOffset={offset}",
        "step": 6,
        "location_selector": "span.list-item-location",
        "job_link_pattern": r"/externaljobs/JobDetail/",
        "domain_check": "jobs.siemens.com",
    },
    "coca_cola_hbc": {
        "label": "Coca-Cola HBC",
        # ProjectDetail links; pagination uses project*, not job*.
        "base": "https://careers.coca-colahellenic.com/en_US/careers/SearchJobs/?projectRecordsPerPage={step}&projectOffset={offset}",
        "step": 10,
        "location_selector": "span[class^='list-item-location']",
        "job_link_pattern": r"/careers/ProjectDetail/",
        "domain_check": "careers.coca-colahellenic.com",
    },
}

MAX_PAGES = 2000  # safety valve; real stop condition is an empty page
EMPTY_PAGE_RETRIES = 3  # re-fetch a page that comes back with 0 job links this many extra times
EMPTY_PAGE_RETRY_DELAY = 1.5  # seconds between those retries

DATALAYER_JOB_RE = re.compile(
    r'jobTitle:\s*"[^"]*".*?'
    r'jobFunction:\s*"(?P<jobFunction>[^"]*)".*?'
    r'jobDivision:\s*"(?P<jobDivision>[^"]*)".*?'
    r'jobBrand:\s*"[^"]*".*?'
    r'jobEmploymentType:\s*"(?P<jobEmploymentType>[^"]*)".*?'
    r'jobPositionType:\s*"(?P<jobPositionType>[^"]*)".*?'
    r'jobCountry:\s*"(?P<jobCountry>[^"]*)".*?'
    r'jobLocation:\s*"(?P<jobLocation>[^"]*)"',
    re.DOTALL,
)


def get_html(client: httpx.Client, url: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            resp = client.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
            resp.raise_for_status()  # some portals rate-limit with 4xx instead of erroring
            return resp.text
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def extract_location(a_tag, config: dict) -> str:
    layout = config.get("layout", "card")

    if layout == "table":
        # <tr><th><a>title</a></th><td>city, prov</td><td>work mode</td></tr>
        tr = a_tag.find_parent("tr")
        if not tr:
            return ""
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        return "; ".join(c for c in cells if c)

    header_text_div = a_tag.find_parent("div", class_="article__header__text")
    if not header_text_div:
        return ""

    if layout == "subtitle_first_span":
        # Location is the first plain <span> inside subtitle; later
        # siblings are "Req #: ..." / "Posted ..." metadata.
        subtitle = header_text_div.find("div", class_="article__header__text__subtitle")
        if not subtitle:
            return ""
        for span in subtitle.find_all("span", recursive=False):
            text = span.get_text(strip=True)
            if text and not text.lower().startswith(("posted", "req #")):
                return text
        return ""

    spans = header_text_div.select(config["location_selector"])
    return "; ".join(s.get_text(strip=True) for s in spans)


def make_absolute(href: str, domain_check: str) -> str:
    if href.startswith("http"):
        return href
    return f"https://{domain_check}{href}"


def parse_result_count(html: str):
    """Best-effort extraction of a results-count string for a sanity check."""
    m = re.search(r"of\s+([\d,]+\+?)\s+(?:results|jobs)", html, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'aria-label="(\d+)\s+result\(s\)"', html, re.IGNORECASE)
    return m.group(1) if m else None


def enrich_with_loreal_datalayer(jobs: list, max_workers: int = 5) -> list:
    """Fetches each job's detail page and pulls the richer location/metadata
    out of its inline dataLayer.push({...}) block. One extra request per
    job; keep max_workers low, careers.loreal.com rate-limits aggressively."""
    with httpx.Client() as client:
        def worker(job):
            try:
                html_text = get_html(client, job["url"])
            except Exception:
                return job
            m = DATALAYER_JOB_RE.search(html_text)
            if not m:
                return job
            data = m.groupdict()
            job["location"] = data["jobLocation"] or data["jobCountry"] or job["location"]
            job["country"] = data["jobCountry"]
            job["job_function"] = data["jobFunction"]
            job["division"] = data["jobDivision"]
            job["employment_type"] = data["jobEmploymentType"]
            job["position_type"] = data["jobPositionType"]
            return job

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            return list(ex.map(worker, jobs))


def scrape_company(config: dict) -> tuple:
    """Returns (jobs, reported_total); reported_total is the page's own
    result count, or None if it couldn't be parsed for this markup."""
    jobs, seen = [], set()
    page = 0
    reported_total = None

    # Fresh client per company: no cookies/session state carried over,
    # since some portals return stale filtered results otherwise.
    with httpx.Client() as client:
        while page < MAX_PAGES:
            offset = page * config["step"]
            url = config["base"].format(step=config["step"], offset=offset)

            # A page can come back with 0 job links even mid-listing (seen
            # on L'Oréal); retry before treating it as the real end.
            links, html_text = [], ""
            for attempt in range(EMPTY_PAGE_RETRIES + 1):
                html_text = get_html(client, url)
                soup = BeautifulSoup(html_text, "html.parser")
                links = soup.select(f'a[href*="{config["job_link_pattern"]}"]')
                if links or attempt == EMPTY_PAGE_RETRIES:
                    break
                time.sleep(EMPTY_PAGE_RETRY_DELAY)

            if page == 0:
                reported_total = parse_result_count(html_text)

            new_count = 0
            for a in links:
                href = a.get("href", "")
                # "Share via email" links embed the real job URL inside a
                # mailto: body param and also match job_link_pattern.
                if not href or href.startswith("mailto:") or "_linkedinApiv2" in href or href in seen:
                    continue
                seen.add(href)
                new_count += 1
                title = a.get_text(strip=True) or (a.get("title") or "").strip()
                jobs.append({
                    "title": title,
                    "url": make_absolute(href, config["domain_check"]),
                    "location": extract_location(a, config),
                })

            if new_count == 0:
                break
            page += 1

    if config.get("detail_enrichment") == "loreal_datalayer":
        print(f"[{config['label']}] enriching {len(jobs)} jobs from detail pages...")
        jobs = enrich_with_loreal_datalayer(jobs)

    print(f"[{config['label']}] page banner reported: {reported_total!r}")
    return jobs, reported_total


if __name__ == "__main__":
    all_results = {}
    for key, config in COMPANIES.items():
        print(f"\n=== Scraping {config['label']} ===")
        try:
            jobs, _ = scrape_company(config)
        except Exception as e:
            print(f"  FAILED: {e}")
            continue

        all_results[key] = jobs
        print(f"  Total jobs found: {len(jobs)}")
        for j in jobs[:10]:
            print(" ", j)

        out_path = f"{key}_jobs.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
        print(f"  Saved -> {out_path}")

    print("\n=== Summary ===")
    for key, jobs in all_results.items():
        print(f"  {COMPANIES[key]['label']}: {len(jobs)} jobs")
