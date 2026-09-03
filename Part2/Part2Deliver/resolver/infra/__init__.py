"""Rate-limited, retrying HTTP client shared across all extractors."""

import time
import logging
import threading
import urllib.parse

import httpx
import tldextract

from resolver.config import (
    DEFAULT_HEADERS,
    PER_DOMAIN_DELAY,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    DOMAIN_DELAY_OVERRIDES,
    MAX_DOMAIN_DELAY,
)

logger = logging.getLogger(__name__)

# Unrecoverable connection error substrings — never retry these
_DEAD_HOST_HINTS = [
    "nodename nor servname",
    "name or service not known",
    "no address associated",
    "name resolution",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "certificate_verify_failed",
    "certificate verify failed",
    "wrong_version_number",
    "wrong version number",
    "connection refused",
    "connection reset by peer",
]


def _is_dead_host_error(exc: Exception) -> bool:
    """Check if an exception indicates an unrecoverable host issue."""
    msg = str(exc).lower()
    return any(hint in msg for hint in _DEAD_HOST_HINTS)


class RateLimitedClient:
    """HTTP client with per-domain rate limiting and automatic retries."""
    def __init__(
        self,
        per_domain_delay: float = PER_DOMAIN_DELAY,
        max_retries: int = MAX_RETRIES,
        timeout: float = REQUEST_TIMEOUT,
    ):
        self.per_domain_delay = per_domain_delay
        self.max_retries = max_retries
        self.last_hit: dict[str, float] = {}
        self._ratelimit_lock = threading.Lock()
        # Seeded with known-sensitive hosts; raised adaptively on 429.
        self.domain_delays: dict[str, float] = dict(DOMAIN_DELAY_OVERRIDES)
        self._dead_hosts: set[str] = set()  # hostnames that failed DNS
        self.session = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
        )
    def _wait_for_domain(self, domain: str) -> None:
        """
        Enforce the per-domain delay across threads.
        Reserve the next slot under the lock, then sleep outside it, so
        concurrent workers stagger instead of firing together.
        """
        with self._ratelimit_lock:
            now = time.time()
            delay = self.domain_delays.get(domain, self.per_domain_delay)
            send_at = max(now, self.last_hit.get(domain, 0.0) + delay)
            self.last_hit[domain] = send_at
        wait = send_at - time.time()
        if wait > 0:
            time.sleep(wait)
    def _baseline_delay(self, domain: str) -> float:
        return DOMAIN_DELAY_OVERRIDES.get(domain, self.per_domain_delay)
    def _relax_domain(self, domain: str) -> None:
        """Ease a domain's delay back toward its baseline after a success."""
        baseline = self._baseline_delay(domain)
        with self._ratelimit_lock:
            current = self.domain_delays.get(domain, baseline)
            if current > baseline:
                self.domain_delays[domain] = max(baseline, current * 0.8)
    def _get_hostname(self, url: str) -> str:
        """Extract hostname from URL."""
        try:
            return urllib.parse.urlparse(url).hostname or ""
        except Exception:
            return ""
    def get(self, url: str, headers: dict | None = None) -> httpx.Response | None:
        """
        Fetch a URL with rate limiting and retries.
        Returns the Response on success, or None if all retries are exhausted.
        DNS failures are never retried and the host is cached as dead.
        """
        hostname = self._get_hostname(url)
        if hostname in self._dead_hosts:
            return None
        try:
            domain = tldextract.extract(url).registered_domain
        except Exception:
            domain = url
        self._wait_for_domain(domain)
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, headers=headers)
                if resp.status_code == 429:
                    # Raise the ongoing delay too, or other workers keep
                    # hitting at the old rate.
                    with self._ratelimit_lock:
                        current = self.domain_delays.get(domain, self.per_domain_delay)
                        self.domain_delays[domain] = min(current * 1.5, MAX_DOMAIN_DELAY)
                        new_delay = self.domain_delays[domain]
                    backoff = 2 ** attempt * 5
                    logger.warning("429 on %s — domain delay now %.1fs, backing off %ds",
                                   url, new_delay, backoff)
                    time.sleep(backoff)
                    continue
                # Retry transient 5xx; a one-off 500 otherwise discards
                # the whole result.
                if resp.status_code in (500, 502, 503, 504) and attempt < self.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("%d on %s, retrying in %ds", resp.status_code, url, wait)
                    time.sleep(wait)
                    continue
                # Decay on success, or the back-off is a one-way ratchet.
                if resp.status_code < 400:
                    self._relax_domain(domain)
                return resp
            except (httpx.ConnectError,) as exc:
                if _is_dead_host_error(exc):
                    logger.debug("Unrecoverable error for %s — marking host dead", hostname)
                    self._dead_hosts.add(hostname)
                    return None
                # Transient connect errors — retry with backoff
                wait = 2 ** attempt
                logger.warning("Connect error attempt %d for %s: %s, retrying in %ds",
                               attempt + 1, url, exc, wait)
                time.sleep(wait)
            except (httpx.TimeoutException, httpx.ReadError) as exc:
                wait = 2 ** attempt
                logger.warning("Attempt %d failed for %s: %s, retrying in %ds",
                               attempt + 1, url, exc, wait)
                time.sleep(wait)
            except Exception as exc:
                logger.error("Unexpected error fetching %s: %s", url, exc)
                return None
        logger.error("All retries exhausted for %s", url)
        return None
    def close(self):
        self.session.close()


# Module-level singleton — importable everywhere
client = RateLimitedClient()
