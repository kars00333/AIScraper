# AIScraper
# AIScraper

Two independent scraping/resolution projects.

## Part 1 — Career Page Scrapers

Scrapers for company career sites across several ATS platforms (Avature, Phenom People, and others).

- `career_scrapers_deliverable/` — final scripts and JSON output per company (Coca-Cola HBC, HPE, Hugo Boss, Lenovo, L'Oréal, NBC, Siemens).
- `career_scrapers_workfiles/` — supporting work: an EA scraper variant, a local HTML result viewer (`build_viewer.py` / `crawler_viewer.html`), and a scraper health check script.

Setup:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r career_scrapers_workfiles/requirements.txt
```

## Part 2 — LinkedIn → Career Page Resolver

Given a company name (or LinkedIn URL), resolves the company's real career/job-listing page — via domain guessing, Clearbit autocomplete, and search engine fallback (Yahoo, DuckDuckGo, Bing), with ATS-platform detection and relevance validation.

- `Part2/resolver/` — the resolver package (`config.py`, `main.py`, `verify.py`, `extractors/`, `infra/`).
- `Part2/Part2Deliver/` — a packaged snapshot of an earlier delivered version.

Setup:
```bash
python3 -m venv dvenv
source dvenv/bin/activate
pip install -r Part2/requirements.txt
```

Run:
```bash
cd Part2
python -m resolver.main --dev-set
```
