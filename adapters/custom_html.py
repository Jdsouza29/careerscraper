"""Config-driven adapter for careers pages whose job list is in the served HTML.

No browser: if the page needs JavaScript to render its list, this yields nothing and
the run summary flags the company as returning 0 jobs.

    "params": {
      "url": "https://example.com/careers",
      "item_selector": "div.job-card",
      "fields": {"title": {"selector": "h3"},
                 "location": {"selector": ".loc"},
                 "job_url": {"selector": "a", "attr": "href"}},
      "base_url": "https://example.com",
      "pages": {"url_template": "https://example.com/careers?page={page}",
                "start": 1, "count": 5},
      "detail": {"description_selector": "div.job-description"}
    }
"""

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from adapters.base import Adapter, AdapterError, make_job
from core.text import clean_whitespace, html_to_text

TEXT_FIELDS = ("title", "location", "department", "employment_type", "work_mode",
               "external_id", "job_url", "apply_url", "posted_date", "description")


class CustomHtmlAdapter(Adapter):
    slug = "custom_html"
    required_params = ("url", "item_selector", "fields")

    @property
    def description_in_list(self):
        return bool((self.param("fields") or {}).get("description"))

    def _page_urls(self):
        pages = self.param("pages")
        if not pages:
            return [self.param("url")]
        start = int(pages.get("start", 1))
        count = int(pages.get("count", 1))
        return [pages["url_template"].format(page=n) for n in range(start, start + count)]

    def raw(self, http):
        return {"url": self._page_urls()[0], "html": http.get_text(self._page_urls()[0])[:4000]}

    def fetch(self, http):
        fields = self.param("fields")
        base_url = self.param("base_url") or self.param("url")
        jobs = []

        for page_url in self._page_urls():
            soup = BeautifulSoup(http.get_text(page_url), "html.parser")
            cards = soup.select(self.param("item_selector"))
            if not cards:
                break

            for card in cards:
                values = {}
                for field, rule in fields.items():
                    if field not in TEXT_FIELDS:
                        raise AdapterError(f"{self.name}: unknown mapped field {field!r}")
                    values[field] = _extract(card, rule, base_url)
                if values.get("description"):
                    values["description"] = clean_whitespace(values["description"])

                title = values.pop("title", "")
                if not title:
                    continue
                if not values.get("external_id"):
                    values["external_id"] = values.get("job_url", "")
                values["source"] = self.slug
                jobs.append(make_job(self.name, title, **values))

        return jobs

    def fetch_description(self, http, job):
        detail = self.param("detail") or {}
        selector = detail.get("description_selector")
        if not selector or not job.get("job_url"):
            return job.get("description", ""), []

        soup = BeautifulSoup(http.get_text(job["job_url"]), "html.parser")
        node = soup.select_one(selector)
        return (html_to_text(str(node)) if node else ""), []


def _extract(card, rule, base_url):
    if isinstance(rule, str):
        rule = {"selector": rule}
    node = card if rule.get("selector") in (None, "", ".") else card.select_one(rule["selector"])
    if node is None:
        return ""

    attr = rule.get("attr")
    if not attr:
        return " ".join(node.get_text(" ").split())

    value = node.get(attr, "")
    if isinstance(value, list):
        value = " ".join(value)
    return urljoin(base_url, value) if attr in ("href", "src") and value else value
