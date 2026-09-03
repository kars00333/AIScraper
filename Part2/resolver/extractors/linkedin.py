"""Extract company name from a LinkedIn job posting page."""

import json
import re
import logging

from bs4 import BeautifulSoup

from resolver.infra import client

logger = logging.getLogger(__name__)


# LinkedIn search/listing pages yield titles that are not companies
# ("Law jobs in United States").
_JUNK_NAME_RE = re.compile(
    r"(\bjobs?\s+in\b|\bjob\s+search\b|^jobs?\b|\bhiring\s+now\b|^linkedin$"
    r"|\bcareers?\s+in\b|\bemployment\b.*\bin\b)",
    re.IGNORECASE,
)


_CLOSED_INDICATORS = [
    "no longer accepting applications",
    "this job is no longer available",
    "job has expired",
    "this posting has been closed",
]


# LinkedIn exposes the same posting under several URL shapes: the canonical
# /jobs/view/<id>, and the search-results view that carries the id in a query
# parameter (?currentJobId=/?trk_job_id=). Accept any of them so a link copied
# while browsing resolves like a canonical one.
_JOB_ID_RE = re.compile(
    r"/jobs/view/(\d{6,})"
    r"|[?&](?:currentJobId|trk_job_id|jobId)=(\d{6,})"
    r"|/jobs/[^/?#]*-(\d{9,})(?:[/?#]|$)",
    re.IGNORECASE,
)


def _job_id(url: str) -> str | None:
    """The numeric posting id, from whichever URL shape LinkedIn used."""
    m = _JOB_ID_RE.search(url)
    return next((g for g in m.groups() if g), None) if m else None
_GUEST_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"


def _guest_company(linkedin_url: str) -> str | None:
    """Company name from LinkedIn's guest posting card, or None."""
    jid = _job_id(linkedin_url)
    if not jid:
        return None
    resp = client.get(_GUEST_ENDPOINT.format(jid))
    if resp is None or resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "lxml")
    el = soup.select_one(
        "a.topcard__org-name-link, .topcard__org-name-link, "
        "span.topcard__flavor, .top-card-layout__card .topcard__flavor"
    )
    if el:
        name = el.get_text(strip=True)
        if name and len(name) < 150:
            return name
    return None


def get_company(linkedin_url: str) -> tuple[str | None, str]:
    """
    Fetch a LinkedIn job posting and extract the company name.
    Returns:
        (company_name, status_hint)
        status_hint is one of: "ok", "no_company", "lookup_unavailable", "posting_closed"
    Closed-ness is detected independently of extraction, so a closed posting
    still returns its company name rather than being treated as a dead end.
    """
    # Guest endpoint first: an expired posting's normal page redirects to a
    # keyword search naming no employer; this still serves the posting card.
    guest_name = _guest_company(linkedin_url)
    if guest_name and not _JUNK_NAME_RE.search(guest_name):
        return guest_name, "ok"
    resp = client.get(linkedin_url)
    if resp is None:
        return None, "lookup_unavailable"
    if resp.status_code == 404:
        return None, "no_company"
    if resp.status_code >= 400:
        logger.warning("HTTP %d for %s", resp.status_code, linkedin_url)
        return None, "lookup_unavailable"
    html = resp.text
    soup = BeautifulSoup(html, "lxml")
    is_closed = any(ind in html.lower() for ind in _CLOSED_INDICATORS)
    found_status = "posting_closed" if is_closed else "ok"
    if is_closed:
        logger.debug("Posting closed (still resolving company): %s", linkedin_url)
    name = _extract_company_name(soup, html)
    if name and not _JUNK_NAME_RE.search(name):
        return name, found_status

    # No name anywhere. Report the closure if we saw one, else no_company.
    return None, "posting_closed" if is_closed else "no_company"


def _extract_company_name(soup: BeautifulSoup, html: str) -> str | None:
    """Try each extraction strategy in order; return the first name found."""
    # ── Strategy 1: JSON-LD schema.org block ──
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            # Handle both single object and array of objects
            items = data if isinstance(data, list) else [data]
            for item in items:
                org = item.get("hiringOrganization")
                if isinstance(org, dict):
                    name = org.get("name", "").strip()
                    if name:
                        logger.debug("JSON-LD company: %s", name)
                        return name
                elif isinstance(org, str) and org.strip():
                    return org.strip()
        except (json.JSONDecodeError, AttributeError):
            continue

    # ── Strategy 2: Title tag ──
    # Before the company-link scan: the title names this posting's employer,
    # while the first /company/ link is often a sidebar recommendation.
    # Capture stops at a dash or pipe to exclude the location.
    title_tag = soup.find("title")
    if title_tag:
        title_text = title_tag.get_text(strip=True)
        # LinkedIn serves either shape for the same posting:
        #   "{Company} hiring {Job} in {Location} | LinkedIn"
        #   "{Job} at {Company} — {Location} | LinkedIn Jobs"
        m = re.match(r"\s*(.+?)\s+hiring\s+", title_text, re.IGNORECASE)
        if not m:
            m = re.search(r"\bat\s+(.+?)\s*(?:[—–|]|$)", title_text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if candidate and candidate.lower() != "linkedin" and len(candidate) < 150:
                return candidate

    # ── Strategy 3: Meta tags ──
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "") or meta.get("name", "")
        content = (meta.get("content") or "").strip()
        if prop.lower() in ("og:site_name",) and content and content.lower() != "linkedin":
            return content

    # ── Strategy 4: Company link (last resort) ──
    # Unanchored, so prefer a link that isn't flagged as a recommendation.
    for link in soup.select("a[href*='/company/']"):
        if "trk=public_jobs_" in link.get("href", ""):
            continue
        text = link.get_text(strip=True)
        if text and len(text) < 200:
            return text
    return None
