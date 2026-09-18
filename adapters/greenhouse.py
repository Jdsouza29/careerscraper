"""Greenhouse job boards: https://boards-api.greenhouse.io/v1/boards/<token>/jobs"""

from adapters.base import Adapter, AdapterError, make_job
from core.text import html_to_text

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


class GreenhouseAdapter(Adapter):
    slug = "greenhouse"
    description_in_list = True
    required_params = ("board_token",)

    def url(self):
        return API.format(token=self.param("board_token"))

    def raw(self, http):
        return http.get_json(self.url())

    def fetch(self, http):
        payload = self.raw(http)
        if "jobs" not in payload:
            raise AdapterError(f"{self.name}: greenhouse payload has no 'jobs' key")

        jobs = []
        for item in payload["jobs"]:
            offices = item.get("offices") or []
            departments = item.get("departments") or []
            jobs.append(make_job(
                self.name,
                item.get("title"),
                location=(item.get("location") or {}).get("name")
                         or ", ".join(o.get("name", "") for o in offices),
                department=departments[0]["name"] if departments else "",
                source=self.slug,
                external_id=item.get("id"),
                job_url=item.get("absolute_url"),
                apply_url=item.get("absolute_url"),
                posted_date=_date(item.get("first_published") or item.get("updated_at")),
                description=html_to_text(item.get("content")),
            ))
        return jobs


def _date(value):
    return str(value)[:10] if value else ""
