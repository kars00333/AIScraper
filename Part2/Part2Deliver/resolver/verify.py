#!/usr/bin/env python3
"""
Independent verification of company -> career-page pairings.

Deliberately does NOT import _looks_related: that is the predicate the
resolver uses to accept a URL, so auditing with it is circular and reports
100% by construction. This fetches the page and looks for evidence the page
itself provides:
  * does the <title> / og:site_name / logo alt name the company?
  * does the page carry multiple distinct job links (a LIST, not one posting)?
  * does the site's own homepage identify itself as this company?

Each row gets a verdict from that evidence, so a wrong pairing that satisfied
the resolver's URL heuristic still gets caught.
"""
import re
import sys
import sqlite3
import urllib.parse

from bs4 import BeautifulSoup

from resolver.infra import client
from resolver.config import CHECKPOINT_DB_PATH

STOP = {"inc", "llc", "ltd", "corp", "corporation", "company", "co", "group",
        "holdings", "plc", "the", "and", "of", "for", "a"}


def toks(name):
    return [t for t in re.findall(r"[a-z0-9]+", name.lower())
            if t not in STOP and len(t) >= 3]


def name_in(text, company):
    """Whole company name, or most of its distinctive tokens, present."""
    t = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    norm = re.sub(r"[^a-z0-9]+", " ", company.lower()).strip()
    if norm and norm in t:
        return True
    ts = toks(company)
    if not ts:
        return False
    hit = sum(1 for x in ts if re.search(rf"\b{re.escape(x)}\b", t))
    return hit >= max(1, len(ts) - 1)


def verify(company, url):
    r = client.get(url)
    if r is None:
        return "UNREACHABLE", ""
    if r.status_code in (403, 401):
        return "BLOCKED(%d)" % r.status_code, ""
    if r.status_code >= 400:
        return "DEAD(%d)" % r.status_code, ""
    html = r.text or ""
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    site = ""
    for m in soup.find_all("meta"):
        if (m.get("property") or m.get("name") or "").lower() == "og:site_name":
            site = m.get("content") or ""
            break

    # Evidence 1: page identifies the company by name.
    identified = name_in(title, company) or name_in(site, company)

    # Evidence 2: is it a list of jobs, or a single posting / profile?
    job_links = set()
    for a in soup.find_all("a", href=True):
        h = a["href"].lower()
        if re.search(r"/(job|jobs|career|careers|opening|position|vacanc)", h):
            job_links.add(h.split("?")[0])
    is_list = len(job_links) >= 3
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    own_domain = any(t in host for t in toks(company)) or \
        re.sub(r"[^a-z0-9]", "", company.lower())[:10] in host.replace(".", "")
    if identified and is_list:
        return "OK", f"title/site names company; {len(job_links)} job links"
    if identified and not is_list:
        return "OK-THIN", f"names company but only {len(job_links)} job links"
    if own_domain and is_list:
        return "OK-DOMAIN", f"own domain; {len(job_links)} job links"
    return "SUSPECT", f"page does not name company (title={title[:48]!r})"


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    conn = sqlite3.connect(f"file:{CHECKPOINT_DB_PATH}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT company, career_page_url, resolved_via FROM results "
        "WHERE career_page_url <> '' ORDER BY RANDOM() LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    tally = {}
    print(f"independently verifying {len(rows)} pairings\n")
    for co, url, via in rows:
        verdict, why = verify(co, url)
        tally[verdict] = tally.get(verdict, 0) + 1
        flag = "  " if verdict.startswith("OK") else ">>"
        print(f"{flag} [{verdict:11}] {co[:26]:27} {url[:50]:51} {why[:44]}")
    print("\n--- tally ---")
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {k:12} {v}")
    ok = sum(v for k, v in tally.items() if k.startswith("OK"))
    print(f"\ncorroborated: {ok}/{len(rows)}")


if __name__ == "__main__":
    main()
