"""Ashby job boards: https://api.ashbyhq.com/posting-api/job-board/<token>"""

from adapters.base import Adapter, AdapterError, make_job
from core.text import html_to_text

API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


class AshbyAdapter(Adapter):
    slug = "ashby"
    description_in_list = True
    required_params = ("board_token",)

    def url(self):
        return API.format(token=self.param("board_token"))

    def raw(self, http):
        return http.get_json(self.url())

    def fetch(self, http):
        payload = self.raw(http)
        if "jobs" not in payload:
            raise AdapterError(f"{self.name}: ashby payload has no 'jobs' key")

        jobs = []
        for item in payload["jobs"]:
            if item.get("isListed") is False:
                continue
            locations = [item.get("location") or ""]
            locations += [
                secondary.get("location", "")
                for secondary in item.get("secondaryLocations") or []
            ]
            jobs.append(make_job(
                self.name,
                item.get("title"),
                location=", ".join(part for part in locations if part),
                department=item.get("team") or item.get("department") or "",
                employment_type=item.get("employmentType") or "",
                work_mode=item.get("workplaceType")
                          or ("Remote" if item.get("isRemote") else ""),
                source=self.slug,
                external_id=item.get("id"),
                job_url=item.get("jobUrl"),
                apply_url=item.get("applyUrl") or item.get("jobUrl"),
                posted_date=str(item.get("publishedAt") or "")[:10],
                description=item.get("descriptionPlain")
                            or html_to_text(item.get("descriptionHtml")),
            ))
        return jobs
