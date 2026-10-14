"""HTTP client with retries, exponential backoff and polite rate limiting.

The rate limit is **per host**, not per client, because companies are scraped
concurrently but share backends: 112 of the registry's boards live on
boards-api.greenhouse.io, while every Workday tenant is a host of its own. A single
global interval would serialize the whole run; no interval at all would point the
combined concurrency of the run at whichever host is busiest. Keyed by host, the
Workday tenants proceed in parallel and Greenhouse still sees one polite stream.

One `HttpClient` is shared by every worker thread. The throttle state is guarded by a
lock, and each thread gets its own `requests.Session` (sessions are not documented as
thread-safe) while sharing connection-pool-free configuration.
"""

import threading
import time
from urllib.parse import urlparse

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(Exception):
    """Raised when a request keeps failing after all retries."""


class HttpClient:
    def __init__(self, headers=None, proxies=None, timeout=20, retries=3,
                 backoff=1.0, min_interval=0.4, host_intervals=None):
        self.headers = dict(DEFAULT_HEADERS)
        if headers:
            self.headers.update(headers)
        self.proxies = proxies or None
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.min_interval = min_interval
        # Per-host overrides, e.g. {"boards-api.greenhouse.io": 0.1}. A host with no
        # entry uses min_interval.
        self.host_intervals = dict(host_intervals or {})
        self._next_free_at = {}
        self._lock = threading.Lock()
        self._local = threading.local()

    @property
    def session(self):
        """One session per thread; `requests.Session` is not documented thread-safe."""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self.headers)
            self._local.session = session
        return session

    def _throttle(self, url):
        """Claim the next slot for this host, then wait for it outside the lock.

        Reserving the slot while holding the lock is what makes this correct under
        concurrency: two threads targeting the same host get consecutive slots rather
        than both reading the same "last request" timestamp and firing together.
        """
        host = urlparse(url).netloc.lower()
        interval = self.host_intervals.get(host, self.min_interval)
        if interval <= 0:
            return

        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_free_at.get(host, 0.0))
            self._next_free_at[host] = slot + interval

        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        if self.proxies:
            kwargs.setdefault("proxies", self.proxies)

        last_error = None
        for attempt in range(self.retries):
            self._throttle(url)
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code in RETRY_STATUS:
                    last_error = f"HTTP {response.status_code}"
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        time.sleep(min(int(retry_after), 30))
                        continue
                elif response.ok:
                    return response
                else:
                    raise HttpError(f"{method} {url} -> HTTP {response.status_code}")

            if attempt < self.retries - 1:
                time.sleep(self.backoff * (2 ** attempt))

        raise HttpError(f"{method} {url} failed after {self.retries} attempts ({last_error})")

    def get_json(self, url, **kwargs):
        return self._json(self.request("GET", url, **kwargs), url)

    def post_json(self, url, body, **kwargs):
        headers = {"Content-Type": "application/json"}
        headers.update(kwargs.pop("headers", {}))
        response = self.request("POST", url, json=body, headers=headers, **kwargs)
        return self._json(response, url)

    def get_text(self, url, **kwargs):
        return self.request("GET", url, **kwargs).text

    @staticmethod
    def _json(response, url):
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError(f"{url} did not return JSON ({exc})") from exc
