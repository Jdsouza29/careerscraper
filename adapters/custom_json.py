"""Config-driven adapter for careers pages backed by their own JSON API (e.g. IBM).

Everything lives in the company's `params`, so a new company needs no new code:

    "params": {
      "url": "https://careers.example.com/api/jobs",
      "method": "GET",
      "query": {"country": "IN"},
      "list_path": "data.jobs",
      "fields": {"title": "title", "location": "office.name",
                 "external_id": "id", "job_url": "url", "description": "body"},
      "url_template": "https://careers.example.com/job/{external_id}",
      "pagination": {"kind": "offset", "param": "offset", "size": 50,
                     "size_param": "limit", "total_path": "total", "max_pages": 20},
      "detail": {"url_template": "https://careers.example.com/api/jobs/{external_id}",
                 "description_path": "job.description"}
    }
"""

from urllib.parse import urljoin

from adapters.base import Adapter, AdapterError, make_job
from core.text import dig, html_to_text

TEXT_FIELDS = ("title", "location", "department", "employment_type", "work_mode",
               "external_id", "job_url", "apply_url", "posted_date", "description")


class CustomJsonAdapter(Adapter):
    slug = "custom_json"
    required_params = ("url", "fields")

    @property
    def description_in_list(self):
        return bool((self.param("fields") or {}).get("description") or self.param("sections"))

    def _request(self, http, page_index):
        method = str(self.param("method", "GET")).upper()
        query = dict(self.param("query") or {})
        body = self.param("body")
        pagination = self.param("pagination") or {}

        if pagination:
            kind = pagination.get("kind", "offset")
            size = int(pagination.get("size", 20))
            target = query if method == "GET" else (body if isinstance(body, dict) else query)
            # The page size has to go out on every request, including the first.
            if pagination.get("size_param"):
                target[pagination["size_param"]] = size
            if page_index:
                target[pagination.get("param", "offset")] = (
                    page_index * size if kind == "offset" else page_index
                )

        headers = self.param("headers") or {}
        if method == "POST":
            return http.post_json(self.param("url"), body or {},
                                  params=query or None, headers=headers)
        return http.get_json(self.param("url"), params=query or None, headers=headers)

    def raw(self, http):
        return self._request(http, 0)

    def fetch(self, http):
        pagination = self.param("pagination") or {}
        max_pages = int(pagination.get("max_pages", 1)) if pagination else 1
        fields = self.param("fields")
        jobs = []

        for page_index in range(max_pages):
            payload = self._request(http, page_index)
            list_path = self.param("list_path")
            items = dig(payload, list_path, None) if list_path else payload
            if not isinstance(items, list):
                raise AdapterError(
                    f"{self.name}: list_path {list_path!r} did not resolve to a list"
                )

            for item in items:
                values = {}
                for field, path in fields.items():
                    if field not in TEXT_FIELDS:
                        raise AdapterError(f"{self.name}: unknown mapped field {field!r}")
                    values[field] = _flatten(dig(item, path, ""))
                if values.get("description"):
                    values["description"] = html_to_text(values["description"])

                sections = self._sections(item)
                if sections:
                    values["description"] = "\n\n".join(
                        f"{heading}\n{body}" for heading, body in sections
                    )

                title = values.pop("title", "")
                template = self.param("url_template")
                if template and not values.get("job_url"):
                    values["job_url"] = template.format(**{**values, "title": title})
                base_url = self.param("base_url")
                if base_url and values.get("job_url", "").startswith("/"):
                    values["job_url"] = urljoin(base_url, values["job_url"])
                values.setdefault("source", self.slug)
                jobs.append(make_job(self.name, title, sections=sections, **values))

            total_path = pagination.get("total_path")
            size = int(pagination.get("size", 20))
            if len(items) < size:
                break
            if total_path and len(jobs) >= int(dig(payload, total_path, 0) or 0):
                break
        else:
            if max_pages > 1:
                self.partial = True     # ran out of pages, not out of jobs

        return jobs

    def _sections(self, item):
        """Boards that split a description across fields ("qualifications", ...) map them
        here as heading -> path, which also feeds required/preferred bucketing."""
        mapping = self.param("sections") or {}
        sections = []
        for heading, path in mapping.items():
            body = html_to_text(_flatten(dig(item, path, "")))
            if body:
                sections.append((heading, body))
        return sections

    def fetch_description(self, http, job):
        detail = self.param("detail") or {}
        if not detail.get("url_template"):
            return job.get("description", ""), []

        url = detail["url_template"].format(**job)
        if str(detail.get("method", "GET")).upper() == "POST":
            payload = http.post_json(url, detail.get("body") or {},
                                     headers=detail.get("headers") or {})
        else:
            payload = http.get_json(url, headers=detail.get("headers") or {})

        raw = dig(payload, detail.get("description_path", "description"), "")
        return html_to_text(_flatten(raw)), []


def _flatten(value):
    """Collapse whatever the payload gave us into a string."""
    if isinstance(value, list):
        return ", ".join(_flatten(part) for part in value if part)
    if isinstance(value, dict):
        for key in ("name", "label", "text", "value", "title"):
            if key in value:
                return _flatten(value[key])
        return ""
    return "" if value is None else str(value)
