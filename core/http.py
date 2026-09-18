"""HTTP client with retries, exponential backoff and polite rate limiting."""

import time

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
                 backoff=1.0, min_interval=0.4):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        if headers:
            self.session.headers.update(headers)
        self.proxies = proxies or None
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.min_interval = min_interval
        self._last_request_at = 0.0

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        if self.proxies:
            kwargs.setdefault("proxies", self.proxies)

        last_error = None
        for attempt in range(self.retries):
            self._throttle()
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
