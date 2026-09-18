"""SmartRecruiters: https://api.smartrecruiters.com/v1/companies/<token>/postings

The list endpoint has no descriptions, so each surviving job needs one detail call.
`q` and `country` params narrow the list server-side - worth setting for big employers.
"""

from adapters.base import Adapter, AdapterError, make_job
from core.text import html_to_text

LIST_API = "https://api.smartrecruiters.com/v1/companies/{token}/postings"
DETAIL_API = "https://api.smartrecruiters.com/v1/companies/{token}/postings/{job_id}"
PUBLIC_URL = "https://jobs.smartrecruiters.com/{token}/{job_id}"
PAGE_SIZE = 100

SECTION_ORDER = ("jobDescription", "qualifications", "additionalInformation")


class SmartRecruitersAdapter(Adapter):
    slug = "smartrecruiters"
    description_in_list = False
    required_params = ("company_token",)

    def _query(self, offset):
        query = {"limit": PAGE_SIZE, "offset": offset}
        for key, param in (("q", "q"), ("country", "country"), ("region", "region")):
            if self.param(param):
                query[key] = self.param(param)
        return query

    def raw(self, http):
        token = self.param("company_token")
        return http.get_json(LIST_API.format(token=token), params=self._query(0))

    def fetch(self, http):
        token = self.param("company_token")
        max_jobs = int(self.param("max_jobs", 1000))
        jobs, offset, total = [], 0, None
        self.partial = bool(self.param("q") or self.param("country") or self.param("region"))

        while offset < max_jobs:
            payload = http.get_json(LIST_API.format(token=token), params=self._query(offset))
            if "content" not in payload:
                raise AdapterError(f"{self.name}: smartrecruiters payload has no 'content' key")

            if total is None:
                total = int(payload.get("totalFound") or 0)
            page = payload["content"]
            for item in page:
                location = item.get("location") or {}
                where = ", ".join(
                    part for part in (location.get("city"), location.get("region"),
                                      (location.get("country") or "").upper()) if part
                )
                if location.get("remote"):
                    work_mode = "Remote"
                elif location.get("hybrid"):
                    work_mode = "Hybrid"
                else:
                    work_mode = "Onsite"

                job_id = str(item.get("id"))
                jobs.append(make_job(
                    self.name,
                    item.get("name"),
                    location=where,
                    department=(item.get("department") or {}).get("label")
                               or (item.get("function") or {}).get("label", ""),
                    employment_type=(item.get("typeOfEmployment") or {}).get("label", ""),
                    work_mode=work_mode,
                    source=self.slug,
                    external_id=job_id,
                    job_url=PUBLIC_URL.format(token=token, job_id=job_id),
                    apply_url=PUBLIC_URL.format(token=token, job_id=job_id),
                    posted_date=str(item.get("releasedDate") or "")[:10],
                ))

            offset += PAGE_SIZE
            if len(page) < PAGE_SIZE or (total and offset >= total):
                break

        if total and len(jobs) < total:
            self.partial = True
        return jobs

    def fetch_description(self, http, job):
        payload = http.get_json(DETAIL_API.format(
            token=self.param("company_token"), job_id=job["external_id"],
        ))
        sections_payload = ((payload.get("jobAd") or {}).get("sections")) or {}

        sections = []
        for key in SECTION_ORDER:
            block = sections_payload.get(key) or {}
            body = html_to_text(block.get("text"))
            if body:
                sections.append((block.get("title") or key, body))

        text = "\n\n".join(f"{heading}\n{body}" for heading, body in sections)
        return text, sections
