"""ATS (Applicant Tracking System) detection from URL and HTML."""

import logging

from bs4 import BeautifulSoup

from resolver.config import ATS_SIGNATURES

logger = logging.getLogger(__name__)

# Attributes that show the ATS actually serves the page, rather than the page
# merely naming it somewhere in its text.
_STRUCTURAL_ATTRS = ("src", "href", "action", "data-src")


def _structural_targets(html: str) -> list[str]:
    """URLs the page loads from or submits to."""
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []
    targets = []
    for tag in soup.find_all(["script", "iframe", "form", "link", "a"]):
        for attr in _STRUCTURAL_ATTRS:
            val = tag.get(attr)
            if val:
                targets.append(str(val).lower())
    return targets


def detect_ats(url: str, html: str = "") -> str:
    """
    Identify the ATS serving a career page.
    Returns the ATS name, or "" when unconfirmed — empty is correct, wrong is
    worse. A signature counts only when the ATS hosts the page or the page
    loads from / submits to it; a mention in body text is not evidence.
    """
    url_lower = url.lower()
    targets = _structural_targets(html)
    for ats_name, signatures in ATS_SIGNATURES.items():
        for sig in signatures:
            if sig in url_lower:
                logger.debug("ATS '%s' confirmed by host URL (%s)", ats_name, sig)
                return ats_name
            if any(sig in t for t in targets):
                logger.debug("ATS '%s' confirmed by embedded resource (%s)", ats_name, sig)
                return ats_name
    return ""
