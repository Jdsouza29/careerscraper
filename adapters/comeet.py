"""Comeet career sites: https://www.comeet.co/careers-api/2.0/company/<uid>/positions

Two params, both of which are published by the company's own careers page:

  * `company_uid` is the dotted id in every Comeet link - comeet.com/jobs/cyera/17.008/...
  * `token` is the read-only board token the page passes to the same API. Without it
    the API answers 400 "Token is missing"; with a mismatched pair it answers 400
    "Account uid or token are not valid".

`details=true` makes the list endpoint return the description already split into its
authored sections ("Description", "Requirements", ...), so the requirement parser gets
the same section structure it gets from Lever, and no detail call is needed.
"""

from adapters.base import Adapter, AdapterError, make_job
from core.text import html_to_text

API = "https://www.comeet.co/careers-api/2.0/company/{uid}/positions"


class ComeetAdapter(Adapter):
    slug = "comeet"
    description_in_list = True
    required_params = ("company_uid", "token")

    def url(self):
        return API.format(uid=self.param("company_uid"))

    def _query(self):
        return {"token": self.param("token"), "details": "true"}

    def raw(self, http):
        return http.get_json(self.url(), params=self._query())

    def fetch(self, http):
        payload = self.raw(http)
        if not isinstance(payload, list):
            raise AdapterError(f"{self.name}: comeet payload was not a list of positions")

        jobs = []
        for item in payload:
            if item.get("is_internal"):
                continue

            sections = []
            for block in item.get("details") or []:
                body = html_to_text(block.get("value"))
                if body:
                    sections.append((block.get("name", ""), body))

            location = item.get("location") or {}
            url = item.get("url_comeet_hosted_page") or item.get("url_active_page") or ""
            jobs.append(make_job(
                self.name,
                item.get("name"),
                location=location.get("name")
                         or ", ".join(part for part in (location.get("city"),
                                                        location.get("country")) if part),
                department=item.get("department") or "",
                employment_type=item.get("employment_type") or "",
                work_mode=item.get("workplace_type") or "",
                source=self.slug,
                external_id=item.get("uid"),
                job_url=url,
                apply_url=item.get("url_active_page") or url,
                posted_date=str(item.get("time_updated") or "")[:10],
                description="\n\n".join(
                    f"{heading}\n{body}".strip() for heading, body in sections
                ),
                sections=sections,
            ))
        return jobs
