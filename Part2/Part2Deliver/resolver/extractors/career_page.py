"""Find a company's career/job-list page via domain guessing and search fallback."""

import re
import base64
import logging
import urllib.parse

import httpx
from bs4 import BeautifulSoup

from resolver.infra import client
from resolver.config import CAREER_PATHS

logger = logging.getLogger(__name__)

# Multi-tenant ATS platforms only — no single-employer hosts, which would
# not generalise to unseen companies.
_ATS_HOST_REGEX = re.compile(
    r"(myworkdayjobs\.com|greenhouse\.io|boards\.greenhouse|jobs\.lever\.co|lever\.co|icims\.com|smartrecruiters\.com|avature\.net|phenompeople|ashbyhq\.com|taleo\.net|successfactors|oraclecloud\.com|applitrack\.com|governmentjobs\.com|usajobs\.gov|workforcenow\.adp\.com|ultipro\.com|paycomonline\.net|paylocity\.com|jobvite\.com|recruitee\.com|workable\.com|bamboohr\.com|dayforcehcm\.com|eightfold\.ai)",
    re.IGNORECASE
)

# ATS-domain paths belonging to the vendor's own site, not a customer board.
_ATS_VENDOR_PATH_RE = re.compile(
    r"^/(resources|security|blog|pricing|customers|about|product|platform|"
    r"demo|login|sign[-_]?up|contact|legal|privacy|terms|docs|help|support|"
    r"partners|integrations|events|webinars|news|press|solutions)(/|$)",
    re.IGNORECASE,
)


# One specific posting rather than the company's list.
_SINGLE_POSTING_RE = re.compile(
    r"(/job/[^/]+/[^/]+|/jobs?/\d{5,}|/job/[A-Za-z0-9-]*-\d{4,}"
    r"|/job/[a-z0-9][a-z0-9-]{6,}/?$|/postings?/[0-9a-f-]{20,}"
    # A trailing UUID is a specific posting on most ATS boards, which
    # address roles as /<tenant>/<uuid> with no "job" segment at all.
    r"|/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def to_listing_url(url: str) -> str:
    """
    Trim a single-posting URL back to its job list; unchanged if not one.
    The target is the company's list of open roles, not one posting.
    """
    parsed = urllib.parse.urlparse(url)
    if not _SINGLE_POSTING_RE.search(parsed.path):
        return url
    path = parsed.path
    uuid_tail = re.search(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}.*$",
        path, re.IGNORECASE)
    if uuid_tail:
        trimmed = path[:uuid_tail.start()]
        return urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, trimmed or "/", "", "", ""))
    for marker in ("/job/", "/jobs/", "/posting/", "/postings/"):
        idx = path.lower().find(marker)
        # idx == 0 is valid: a jobs subdomain trims to its root.
        if idx >= 0:
            path = path[:idx]
            break
    else:
        path = path.rsplit("/", 1)[0]
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, path or "/", "", "", "")
    )


# --- Validation: is this page actually a job listing? ---

def is_job_listing_page(url: str, html: str) -> bool:
    """
    Determine if a fetched page is a genuine job-listing / careers page.
    Matches the logic of the validation script to ensure we only return
    pages that will pass the scorer.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        host = (parsed.hostname or "").lower()
    except Exception:
        return False
    if _ATS_HOST_REGEX.search(host):
        # An ATS vendor's own marketing site shares the domain with its
        # customers' boards, so vendor paths are not job lists.
        if _ATS_VENDOR_PATH_RE.match(path):
            return False
        return True

    # score.mjs truncates to 60000 chars before applying these same checks;
    # matching that cap keeps this check predictive of the real score.
    text_lower = html[:60000].lower()
    path_careers = bool(re.search(r"(careers?|jobs?|openings?|vacancies|positions?|join-us|work-with-us|opportunities|job-search|search-jobs)", path))
    bare_home = path in ("", "/")
    if bare_home and not re.search(r"careers|jobs", host):
        return False
    signals = 0
    if path_careers:
        signals += 1
    if re.search(r"careers|jobs", host):
        signals += 1
    if re.search(r'"@type"\s*:\s*"jobposting"', text_lower):
        signals += 2
    if re.search(r"(open positions?|current openings?|view (all )?(jobs|openings|roles)|search jobs|browse jobs|all jobs)", text_lower):
        signals += 1
    apply_count = len(re.findall(r"apply now|apply for", text_lower))
    if apply_count >= 2:
        signals += 1
    job_link_count = len(re.findall(r"/job[/-]|/careers/|requisition|job_?id=", text_lower))
    if job_link_count >= 3:
        signals += 1
    return signals >= 2


# --- Domain guessing from company name ---

def _normalize_company_name(name: str) -> str:
    """Normalize company name into a likely domain slug."""
    # Remove common suffixes
    name = re.sub(
        r'\b(inc\.?|llc\.?|ltd\.?|corp\.?|corporation|company|co\.?|group|holdings|plc|gmbh|ag|sa|s\.a\.)\s*$',
        '', name, flags=re.IGNORECASE
    ).strip()
    # Remove punctuation, lowercase
    slug = re.sub(r'[^a-z0-9]+', '', name.lower())
    return slug


def _guess_domains(company_name: str) -> list[str]:
    """Generate plausible domains from a company name."""
    slug = _normalize_company_name(company_name)
    if not slug:
        return []

    # Also try with hyphens for multi-word names
    hyphen_slug = re.sub(r'[^a-z0-9]+', '-', company_name.lower()).strip('-')
    hyphen_slug = re.sub(r'-+', '-', hyphen_slug)
    # Remove common suffixes for hyphenated version
    hyphen_slug = re.sub(
        r'-(inc|llc|ltd|corp|corporation|company|co|group|holdings|plc|gmbh|ag|sa)$',
        '', hyphen_slug
    )
    domains = []
    for candidate in set([slug, hyphen_slug]):
        if candidate:
            domains.append(f"{candidate}.com")
            domains.append(f"{candidate}.org")
    return domains[:4]  # Keep it small to avoid wasting requests


def _try_clearbit_autocomplete(company_name: str) -> str | None:
    """
    Attempt Clearbit Autocomplete API (free, no auth) to resolve company domain.
    Returns the domain string or None.
    """
    try:
        url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={urllib.parse.quote(company_name)}"
        resp = client.get(url)
        if resp and resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                domain = data[0].get("domain", "")
                if domain:
                    logger.debug("Clearbit resolved '%s' -> %s", company_name, domain)
                    return domain
    except Exception as exc:
        logger.debug("Clearbit autocomplete failed: %s", exc)
    return None


# --- Search: Yahoo (primary), then DuckDuckGo and Bing as fallback ---

_YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _yahoo_search(query: str) -> list[str]:
    """
    Search Yahoo's server-rendered SERP.
    Organic results are wrapped in an r.search.yahoo.com redirect with the
    real destination in an 'RU=' segment; decode it rather than following.
    """
    try:
        encoded = urllib.parse.quote_plus(query)
        # Shared client, not raw httpx: it rate-limits and retries.
        resp = client.get(
            f"https://search.yahoo.com/search?p={encoded}",
            headers=_YAHOO_HEADERS,
        )
        if not resp or resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        urls = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "r.search.yahoo.com" not in href:
                continue
            m = re.search(r"/RU=([^/]+)/", href)
            if not m:
                continue
            try:
                decoded = urllib.parse.unquote(m.group(1))
                # Yahoo's own navbar uses the same redirect format as
                # organic results and appears first in the DOM.
                host = (urllib.parse.urlparse(decoded).hostname or "").lower()
                if decoded.startswith("http") and not host.endswith("yahoo.com"):
                    urls.append(decoded)
            except Exception:
                pass
        return urls
    except Exception as e:
        logger.error(f"Yahoo Search Error: {e}")
        return []


_DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _ddg_search(query: str) -> list[str]:
    """
    Search DuckDuckGo's no-JS 'lite' endpoint. Returns [] on any failure,
    including its anomaly/CAPTCHA page, so callers can fall back.
    """
    try:
        resp = httpx.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query, "kl": ""},
            headers=_DDG_HEADERS,
            timeout=10,
            follow_redirects=True,
        )
        if resp.status_code != 200 or "anomaly-modal" in resp.text:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        urls = []
        for a in soup.select("a.result-link"):
            href = a.get("href", "")
            if href.startswith("http"):
                urls.append(href)
        return urls
    except Exception as e:
        logger.error(f"DDG Search Error: {e}")
        return []


def _bing_search(query: str) -> list[str]:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.bing.com/search?q={encoded}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    try:
        resp = client.get(url, headers=headers)
        if not resp:
            return []
        urls = []
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.select("h2 a"):
            href = a.get("href", "")
            m = re.search(r'&u=a1([^&]+)', href)
            if m:
                b64_str = m.group(1)
                b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
                try:
                    decoded = base64.urlsafe_b64decode(b64_str).decode("utf-8")
                    urls.append(decoded)
                except Exception:
                    pass
            elif href.startswith("http"):
                urls.append(href)
        return urls
    except Exception as e:
        logger.error(f"Bing Search Error: {e}")
        return []


# Paid-ad tracking params. Bing renders ads in the same h2>a shape as
# organic hits, and ad landing pages are never a company's careers page.
_AD_PARAM_RE = re.compile(r"[?&](msclkid|gclid|utm_source|utm_campaign|adgroupid|adid)=", re.IGNORECASE)


# Aggregators/job boards to skip in search results
_SKIP_DOMAINS = [
    "glassdoor.", "indeed.", "ziprecruiter.", "monster.",
    "salary.com", "comparably.", "yelp.", "bbb.org",
    "wikipedia.org", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "tiktok.com", "youtube.com",
    "linkedin.com", "crunchbase.com", "bloomberg.com",
    "zoominfo.com", "dnb.com", "pitchbook.com",
    # Second-tier aggregators seen returning company-shaped pages that are
    # not the employer's own job list.
    "bebee.com", "lawjobs.com", "projectcasting.com", "jobcase.com",
    "jooble.org", "careerbuilder.com", "snagajob.com", "simplyhired.",
    "lensa.com", "talent.com", "adzuna.", "neuvoo.", "jobs2careers.",
    "myperfectresume.", "resume.com", "trabajo.org", "whatjobs.",
    "jobsearcher.com", "jobote.com", "salarylist.", "joblist.com",
]


_NAME_STOPWORDS = {
    "inc", "llc", "ltd", "corp", "corporation", "company", "co", "group",
    "holdings", "plc", "gmbh", "ag", "sa", "the", "and", "of", "for", "us", "u.s",
    # HTML-entity leftovers; they appear on nearly every page.
    "amp", "quot", "nbsp", "apos",
}


# Host labels that identify nobody: TLDs and careers-domain infrastructure.
_NON_IDENTITY_SLOTS = {
    "com", "org", "net", "io", "co", "gov", "edu", "us", "uk", "ca", "au",
    "info", "biz", "app", "ai", "inc", "www", "careers", "career", "jobs",
    "job", "apply", "recruiting", "recruitment", "boards", "work", "hire",
    "talent", "en", "search",
}


_GENERIC_TOKENS = {
    "capital", "law", "firm", "search", "recruiting", "recruitment", "staffing",
    "solutions", "services", "service", "associates", "partners", "consulting",
    "management", "agency", "systems", "technologies", "technology", "products",
    "global", "national", "american", "health", "healthcare", "medical",
    "center", "centre", "institute", "professional", "industries", "enterprises",
    "resources", "human", "employment", "staff", "people", "talent",
}


def _relevant_tokens(company_name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", company_name.lower())
    return [w for w in words if w not in _NAME_STOPWORDS and len(w) >= 3]


_ACRONYM_SKIP = {"of", "and", "the", "for", "a", "at", "in"}
# Legal-entity suffixes contribute no letters: "Shook, Hardy & Bacon L.L.P."
# is shb, not shblllp.
_ENTITY_SUFFIX = {"llp", "llc", "inc", "ltd", "corp", "co", "plc", "pllc",
                  "lp", "pc", "sa", "ag", "gmbh", "l", "p"}


def _acronyms(company_name: str) -> list[str]:
    """Acronym candidates: initials of the name and of its trailing words.

    Built from the raw name so industry words still contribute an initial,
    and short tokens contribute in full ("US Army Corps" -> usace, not ace).
    A two-letter result counts only for a 4+ word name, where it is a real
    abbreviation rather than generic initials.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", company_name.lower())
             if w not in _ACRONYM_SKIP]
    while words and words[-1] in _ENTITY_SUFFIX:
        words.pop()
    floor = 2 if len(words) >= 4 else 3
    out = []
    for group in (words, words[-4:], words[-3:], words[-2:]):
        if len(group) < 2:
            continue
        a = "".join(w if len(w) <= 2 else w[0] for w in group)
        if floor <= len(a) <= 6 and a not in out:
            out.append(a)
    return out


def _page_corroborates(html: str, company_name: str) -> bool:
    """Does the page name most of the company, not just one shared word?

    "Kaon (prev. FlowGPT)" against a page reading "Kaon Interactive" matches
    one token of three — that is a different firm sharing a word, not a
    match. Requiring nearly the whole name separates them.
    """
    if not html:
        return False
    tokens = _relevant_tokens(company_name)
    if not tokens:
        return False
    text = re.sub(r"[^a-z0-9 ]", " ", html[:40000].lower())
    hits = sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}\b", text))
    return hits >= max(1, len(tokens) - 1)


def _label_decomposes(label: str, tokens: list[str]) -> bool:
    """Does the host label read as the company's own words run together?

    "AFC Industries" -> afcind, "Boot Barn" -> bootbarn. The label must be
    consumed entirely by in-order prefixes of the name's tokens, so a shared
    first word plus someone else's ("Smith" -> smithcollege) is rejected.
    """
    i = 0
    used = 0
    for t in tokens:
        for length in range(len(t), 1, -1):
            if label.startswith(t[:length], i):
                i += length
                used += 1
                break
    return i == len(label) and used >= 2


def _looks_related(url: str, company_name: str, html: str = "") -> bool:
    """
    Check a candidate URL actually belongs to this company.
    score.mjs validates page *shape*, never whose page it is, so a wrong
    company's real job board would otherwise pass. Applies to candidates
    from open search/crawl; domain-guess paths are company-specific already.
    """
    tokens = _relevant_tokens(company_name)
    if not tokens:
        return True  # name too generic/short to check meaningfully

    # Industry filler identifies nobody; keep only if that is all there is.
    strong = [t for t in tokens if t not in _GENERIC_TOKENS] or tokens
    parsed = urllib.parse.urlparse(url.lower())
    host = parsed.hostname or ""
    # Split on hyphens too: ATS subdomains embed the tenant as
    # "careersen-itt-inc.icims.com".
    slots = [s for s in re.split(r"[.\-]", host) if s and s not in _NON_IDENTITY_SLOTS]

    # Path identifies a company only on an ATS host (tenant slug); job
    # boards carry the employer name in the path, so it proves nothing.
    if _ATS_HOST_REGEX.search(host):
        slots += [s for s in re.split(r"[^a-z0-9]+", parsed.path) if s]

    # Leading article in the domain (recruitAbility -> therecruitability).
    # Narrow on purpose: "contains" would match "genergy" in "nrgenergy".
    slots += [s[3:] for s in list(slots) if s.startswith("the") and len(s) > 6]

    # One-word brands lose short pieces to tokenizing (U-Haul -> ["haul"]).
    slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
    # Exact label, or a label covering most of the slug. Matching only the
    # slug's first word is not identity ("Pioneer Equity Partners" would
    # otherwise claim pioneer.bank).
    if len(slug) >= 4 and any(
        s == slug or (len(s) >= 4 and slug.startswith(s) and len(s) >= 0.6 * len(slug))
        for s in slots
    ):
        return True

    # The label spells out the company's own words, abbreviations included.
    if any(_label_decomposes(s, tokens) for s in slots):
        return True

    # Prefix-aligned, so an incidental match inside a longer label fails.
    aligned = any(
        s.startswith(t) or (len(s) >= 3 and t.startswith(s))
        for t in strong for s in slots
    )
    # Acronym domains share no whole token with the name (Veterans Affairs
    # -> va.gov). Matched against a whole label or label prefix only.
    for acr in _acronyms(company_name):
        for slot in slots:
            if slot == acr:
                # A two-letter acronym matches far too many domains on its
                # own (ce.com, ms.com); make the page confirm the name.
                if len(acr) == 2 and not _page_corroborates(html, company_name):
                    continue
                return True
            # Prefix matching needs 3+ chars to mean anything.
            if len(acr) >= 3 and len(slot) > len(acr) and slot.startswith(acr):
                return True

    # An exact host-label match settles it only if the matched tokens account
    # for the whole name; otherwise the page must corroborate the rest.
    exact_hits = {t for t in strong for s in slots if t == s}
    if exact_hits:
        # Against the FULL name: filtering industry words can leave one short
        # token standing in for a long one ("ILM Professional Services").
        accounts_for_name = len(exact_hits) == len(set(tokens))
        distinctive = len(strong) == 1 and len(strong[0]) > 7
        if accounts_for_name or distinctive or _page_corroborates(html, company_name):
            return True

    # One-word names need the exact match above; prefixing a longer label is
    # coincidence ("Smith" -> smithcollege).
    if aligned and len(strong) >= 2:
        matched = sum(1 for t in strong if any(t in s for s in slots))
        if matched >= 2:
            return True

    # No content fallback by design: a job board's page about a company names
    # it just as the company's own page does. Identity rests on the URL.
    return False


_GOV_NAME_RE = re.compile(
    r"\b(department of|dept\.? of|u\.?s\.? army|u\.?s\.? navy|u\.?s\.? air force|"
    r"naval|state of|county of|city of|school district|isd\b|independent school|"
    r"public schools|board of education)\b",
    re.IGNORECASE,
)


_JOB_PATH_RE = re.compile(
    r"/(search-jobs|jobs?|openings?|careers?|vacancies|roles?|opportunities)"
)


def _fetch_page(url: str) -> tuple[str, str] | None:
    """Fetch a URL; return (final_url, html) if usable, else None.
    401 counts as usable — the board exists behind a login — but yields no
    body to inspect.
    """
    resp = client.get(url)
    if not resp or resp.status_code not in (200, 401):
        return None
    final_url = str(resp.url) if hasattr(resp, "url") else url
    return final_url, (resp.text if resp.status_code == 200 else "")


def _candidate_links(html: str, base_url: str, ats_only: bool = False) -> list[str]:
    """Links on a page that could be the real job list.
    An ATS host is always a candidate; a same-host link needs a job keyword
    in its path. Off-host non-ATS links are ignored — they lead away from
    the company.
    """
    out: list[str] = []
    base_host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        href = a["href"]
        if href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        if href.startswith("/"):
            href = urllib.parse.urljoin(base_url, href)
        elif not href.startswith("http"):
            continue
        parsed = urllib.parse.urlparse(href)
        host = (parsed.hostname or "").lower()
        if _ATS_HOST_REGEX.search(host):
            if href not in out:
                out.append(href)
        elif not ats_only and host == base_host and _JOB_PATH_RE.search(parsed.path.lower()):
            if href not in out and href != base_url:
                out.append(href)
    return out


def _accept(url: str, html: str, company: str | None) -> bool:
    """A page is acceptable if it is a job list and, when a company is given,
    demonstrably belongs to it."""
    if not is_job_listing_page(url, html):
        return False
    return company is None or _looks_related(url, company, html)


def _search_for_career_page(company_name: str) -> tuple[str | None, str]:
    """
    Search for a company's career page: DuckDuckGo Lite first, Bing as a
    fallback when DDG is unavailable or rate-limited.
    Returns (url, method) or (None, "none").
    """
    search_queries = [
        f'{company_name} "myworkdayjobs.com" OR "greenhouse.io" OR "jobs.lever.co" OR "icims.com" OR "smartrecruiters.com" OR "taleo.net" OR "ashbyhq.com"',
        f"{company_name} careers",
        f"{company_name} careers jobs",
        f"{company_name} jobs apply",
        f"{company_name} open positions",
    ]
    if _GOV_NAME_RE.search(company_name):
        # Government agencies post openings on the federal/state job portals,
        # not always under their own domain — nudge the search there.
        search_queries.insert(0, f'{company_name} usajobs.gov OR governmentjobs.com')
    for query in search_queries:
        # Record which engine answered, for per-row provenance.
        engine = ""
        result_urls = []
        for name, search in (
            ("yahoo", _yahoo_search),
            ("ddg", _ddg_search),
            ("bing", _bing_search),
        ):
            result_urls = search(query)
            if result_urls:
                engine = name
                break

        # Filter aggregators before taking the top N, or job boards that
        # outrank the company consume the whole budget.
        candidates = [
            u for u in result_urls
            if not any(agg in u.lower() for agg in _SKIP_DOMAINS)
            and not _AD_PARAM_RE.search(u)
        ]
        for result_url in candidates[:8]:
            got = _fetch_page(result_url)
            if not got:
                continue
            final_url, html = got
            # Relevance checked here too: the ATS-biased query can self-match
            # an ATS vendor's own site.
            if _accept(final_url, html, company_name):
                return final_url, f"search_fallback:{engine}"

            # Not a job list itself — it may be a splash page linking to one.
            for cand in _candidate_links(html, final_url)[:5]:
                got = _fetch_page(cand)
                if not got:
                    continue
                cand_url, cand_html = got
                if _accept(cand_url, cand_html, company_name):
                    return cand_url, f"search_fallback_crawler:{engine}"

                # One more hop, ATS links only: careers pages often just
                # forward to the board.
                for ats in _candidate_links(cand_html, cand_url, ats_only=True)[:3]:
                    got = _fetch_page(ats)
                    if not got:
                        continue
                    ats_url, ats_html = got
                    if _accept(ats_url, ats_html, company_name):
                        return ats_url, f"search_fallback_crawler_l2:{engine}"
    return None, "none"


# --- Main entry point ---

# Shorter list for guessed domains — only the most common paths
_GUESS_PATHS = ["/careers", "/jobs", "/careers/", "/about/careers", "/company/careers"]


def find_career_page(company_name: str) -> tuple[str | None, str, str]:
    """
    Find a company's career/job-list page.
    Returns:
        (url_or_None, status, method_used)
        status: "ok" | "no_page_found"
        method_used: "clearbit_paths" | "domain_guess" | "search_fallback" | "none"
    """
    # ── Step 1: Try Clearbit to get the real domain ──
    domain = _try_clearbit_autocomplete(company_name)
    if domain and not _looks_related(domain, company_name):
        # Clearbit returns unrelated domains for short/ambiguous names.
        domain = None
    if domain:
        # Try career paths on the Clearbit-resolved domain
        result = _try_career_paths(domain, "clearbit_paths", paths=CAREER_PATHS)
        if result:
            return result

    # ── Step 2: Quick domain guesses (only .com, only /careers and /jobs) ──
    guessed_domains = _guess_domains(company_name)
    for dom in guessed_domains:
        if dom == domain:
            continue
        # Only try .com guesses, skip .org (rarely useful, often dead)
        if not dom.endswith(".com"):
            continue
        result = _try_career_paths(dom, "domain_guess", paths=_GUESS_PATHS)
        if result:
            return result

    # ── Step 3: Search engine fallback ──
    search_url, method = _search_for_career_page(company_name)
    if search_url:
        return search_url, "ok", method
    return None, "no_page_found", "none"


def _try_career_paths(
    domain: str, method: str, paths: list[str] | None = None,
) -> tuple[str, str, str] | None:
    """
    Try common career paths on a domain. Returns (url, status, method) or None.
    Bails early after 2 consecutive non-200 responses to avoid wasting time
    on domains where no career subpath exists.
    """
    if paths is None:
        paths = CAREER_PATHS.copy()
    else:
        paths = paths.copy()

    # Always fetch the homepage to discover actual career links
    discovered_paths: list[str] = []
    try:
        home_resp = client.get(f"https://{domain}/")
        if home_resp and home_resp.status_code == 200:
            soup = BeautifulSoup(home_resp.text, "lxml")
            base_parsed = urllib.parse.urlparse(str(home_resp.url))
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Exclude javascript/mailto links
                if href.startswith(('javascript:', 'mailto:', 'tel:')):
                    continue
                # Simple keyword check in the link text or href
                text = a.get_text(strip=True).lower()
                href_lower = href.lower()
                if re.search(r'careers?|jobs?|openings?|opportunities|work-with-us', text) or \
                   re.search(r'/(careers?|jobs?|openings?|vacancies|roles?|opportunities)', href_lower):
                    if href.startswith("/"):
                        href = urllib.parse.urljoin(str(home_resp.url), href)
                    elif not href.startswith("http"):
                        continue
                    parsed = urllib.parse.urlparse(href)
                    host = (parsed.hostname or "").lower()
                    if _ATS_HOST_REGEX.search(host):
                        # Verify: an unfetched URL may be dead, and a dead
                        # guess scores worse than nothing.
                        got = _fetch_page(href)
                        if got and _accept(got[0], got[1], None):
                            return got[0], "ok", method + "_home_crawler"
                    elif host == base_parsed.hostname:
                        cpath = parsed.path.lower()
                        if (cpath not in paths and cpath not in discovered_paths
                                and cpath not in ("", "/")):
                            discovered_paths.append(cpath)
    except Exception:
        pass

    # Homepage-linked paths beat generic guesses; probe cap would cut them.
    if discovered_paths:
        paths = discovered_paths + paths
    consecutive_fails = 0
    max_consecutive_fails = 2  # bail after this many consecutive non-200s
    for path in paths[:6]: # Limit to top 6 paths to avoid infinite loops
        url = f"https://{domain}{path}"
        try:
            resp = client.get(url)
        except Exception:
            consecutive_fails += 1
            if consecutive_fails >= max_consecutive_fails:
                logger.debug("Domain %s: %d consecutive failures, bailing", domain, consecutive_fails)
                return None
            continue
        if resp is None:
            # DNS failure or dead host — don't bother with the rest
            logger.debug("Domain %s appears unreachable, skipping remaining paths", domain)
            return None
        if resp.status_code == 200:
            final_url = str(resp.url) if hasattr(resp, 'url') else url

            # Only a bounce to our own homepage is a miss; a different
            # subdomain (jobs.company.com/) is a real career page.
            final_parsed = urllib.parse.urlparse(final_url)
            same_host = (final_parsed.hostname or "").lower() == domain.lower()
            if same_host and final_parsed.path in ("", "/") and path not in ("", "/"):
                # Domain redirects career paths to its own homepage — no career page here
                consecutive_fails += 1
                if consecutive_fails >= max_consecutive_fails:
                    logger.debug("Domain %s redirects to homepage, bailing", domain)
                    return None
                continue
            if is_job_listing_page(final_url, resp.text):
                logger.info("Found career page: %s (via %s)", final_url, method)
                return final_url, "ok", method
            else:
                # Not a list itself; test its job links. No relevance check —
                # the domain is already this company's.
                for cand in _candidate_links(resp.text, final_url)[:3]:
                    got = _fetch_page(cand)
                    if got and _accept(got[0], got[1], None):
                        return got[0], "ok", method + "_crawler"
                consecutive_fails += 1
        else:
            consecutive_fails += 1
        if consecutive_fails >= max_consecutive_fails:
            logger.debug("Domain %s: %d consecutive failures, bailing", domain, consecutive_fails)
            return None
    return None
