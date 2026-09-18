"""Workday career sites (the /wday/cxs JSON API behind every myworkdayjobs.com page).

Enterprise tenants list thousands of jobs 20 at a time, so `search_text` (a string or a
list of strings) and `applied_facets` narrow things server-side. `max_jobs` caps a run.
"""

import re

from adapters.base import Adapter, AdapterError, make_job
from core.text import html_to_text

PAGE_SIZE = 20


class WorkdayAdapter(Adapter):
    slug = "workday"
    description_in_list = False
    required_params = ("host", "site")

    @property
    def tenant(self):
        return self.param("tenant") or self.param("host").split(".")[0]

    def _list_url(self):
        return f"https://{self.param('host')}/wday/cxs/{self.tenant}/{self.param('site')}/jobs"

    def _detail_url(self, external_path):
        return (f"https://{self.param('host')}/wday/cxs/{self.tenant}"
                f"/{self.param('site')}{external_path}")

    def _public_url(self, external_path):
        locale = self.param("locale", "en-US")
        return f"https://{self.param('host')}/{locale}/{self.param('site')}{external_path}"

    def _body(self, search_text, offset):
        return {
            "appliedFacets": self.param("applied_facets") or {},
            "limit": PAGE_SIZE,
            "offset": offset,
            "searchText": search_text,
        }

    def _queries(self):
        search_text = self.param("search_text", "")
        return search_text if isinstance(search_text, list) else [search_text]

    def raw(self, http):
        return http.post_json(self._list_url(), self._body(self._queries()[0], 0))

    def fetch(self, http):
        max_jobs = int(self.param("max_jobs", 400))
        jobs, seen_paths = [], set()
        self.partial = bool(self.param("search_text") or self.param("applied_facets"))

        for search_text in self._queries():
            offset, total = 0, None
            while len(jobs) < max_jobs:
                payload = http.post_json(self._list_url(), self._body(search_text, offset))
                if "jobPostings" not in payload:
                    raise AdapterError(
                        f"{self.name}: workday payload has no 'jobPostings' key"
                    )

                # Workday reports `total` on the first page only and 0 thereafter.
                if total is None:
                    total = int(payload.get("total") or 0)

                page = payload["jobPostings"]
                for item in page:
                    external_path = item.get("externalPath") or ""
                    if not external_path or external_path in seen_paths:
                        continue
                    seen_paths.add(external_path)
                    jobs.append(make_job(
                        self.name,
                        item.get("title"),
                        location=item.get("locationsText") or "",
                        source=self.slug,
                        external_id=_requisition_id(item, external_path),
                        job_url=self._public_url(external_path),
                        apply_url=self._public_url(external_path),
                        posted_date="",  # Workday only gives "Posted Today"-style text here
                        description="",
                    ))

                offset += PAGE_SIZE
                if len(page) < PAGE_SIZE or (total and offset >= total):
                    break

        if len(jobs) >= max_jobs:
            self.partial = True
        return jobs[:max_jobs]

    def fetch_description(self, http, job):
        external_path = _path_from_url(job["job_url"], self.param("site"))
        info = http.get_json(self._detail_url(external_path)).get("jobPostingInfo") or {}

        text = html_to_text(info.get("jobDescription"))
        if info.get("location"):
            job["location"] = info["location"]
        if info.get("startDate"):
            job["posted_date"] = str(info["startDate"])[:10]
        if info.get("timeType"):
            job["employment_type"] = info["timeType"]
        if info.get("externalUrl"):
            job["job_url"] = info["externalUrl"]
        return text, []


REQ_ID = re.compile(r"^\S*\d{3,}\S*$")


def _requisition_id(item, external_path):
    """Pick the requisition id out of bulletFields.

    Tenants do not agree on what goes in bulletFields[0]: most put the req id there,
    but F5 leads with a "0"/"1" flag and Intel with the literal "Spotlight Job". Taking
    position 0 blindly gave every job of those tenants the same id, and `dedupe` then
    collapsed the whole board into one or two rows. So look for the bullet that actually
    looks like a req id (no spaces, at least three digits) and fall back to the
    externalPath, which Workday guarantees to be unique.
    """
    for bullet in item.get("bulletFields") or []:
        value = str(bullet).strip()
        if REQ_ID.match(value):
            return value
    return external_path


def _path_from_url(url, site):
    marker = f"/{site}"
    index = url.find(marker)
    return url[index + len(marker):] if index >= 0 else url
