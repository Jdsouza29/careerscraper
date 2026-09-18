"""Eightfold AI career sites (the /api/apply/v2 JSON API behind <tenant>.eightfold.ai).

`domain` is the tenant's own domain and is what the API keys off - netapp.eightfold.ai
answers for domain=netapp.com and nothing else. The list endpoint returns an empty
`job_description`, so descriptions come one call per job from the detail endpoint.
`location` (a string or a list of strings) narrows server-side; `max_jobs` caps a run.
"""

from datetime import datetime, timezone

from adapters.base import Adapter, AdapterError, make_job
from core.text import html_to_text

# Eightfold ignores `num` above 10 and silently serves 10, so paging steps by what
# the page actually returned rather than by what was asked for.
PAGE_SIZE = 10
WORK_MODES = {"onsite": "On-site", "remote": "Remote", "hybrid": "Hybrid"}


class EightfoldAdapter(Adapter):
    slug = "eightfold"
    description_in_list = False
    required_params = ("host", "domain")

    def _list_url(self):
        return f"https://{self.param('host')}/api/apply/v2/jobs"

    def _detail_url(self, external_id):
        return f"https://{self.param('host')}/api/apply/v2/jobs/{external_id}"

    def _query(self, location, start):
        query = {"domain": self.param("domain"), "start": start, "num": PAGE_SIZE}
        if location:
            query["location"] = location
        if self.param("query"):
            query["query"] = self.param("query")
        return query

    def _locations(self):
        location = self.param("location", "")
        return location if isinstance(location, list) else [location]

    def raw(self, http):
        return http.get_json(self._list_url(), params=self._query(self._locations()[0], 0))

    def fetch(self, http):
        max_jobs = int(self.param("max_jobs", 400))
        jobs, seen_ids = [], set()
        self.partial = bool(self.param("location") or self.param("query"))

        for location in self._locations():
            start, total = 0, None
            while len(jobs) < max_jobs:
                payload = http.get_json(self._list_url(), params=self._query(location, start))
                if "positions" not in payload:
                    raise AdapterError(
                        f"{self.name}: eightfold payload has no 'positions' key"
                    )

                if total is None:
                    total = int(payload.get("count") or 0)

                page = payload["positions"]
                for item in page:
                    external_id = str(item.get("id") or "")
                    if not external_id or external_id in seen_ids:
                        continue
                    seen_ids.add(external_id)
                    locations = item.get("locations") or [item.get("location") or ""]
                    url = item.get("canonicalPositionUrl") or ""
                    jobs.append(make_job(
                        self.name,
                        item.get("name") or item.get("posting_name"),
                        location=", ".join(part for part in locations if part),
                        department=item.get("business_unit") or item.get("department") or "",
                        employment_type=item.get("type") if item.get("type") != "ATS" else "",
                        work_mode=WORK_MODES.get(
                            str(item.get("work_location_option") or "").lower(), ""
                        ),
                        source=self.slug,
                        external_id=item.get("display_job_id") or external_id,
                        job_url=url,
                        apply_url=url,
                        posted_date=_epoch_to_date(item.get("t_create")),
                        description=html_to_text(item.get("job_description")),
                    ))

                start += len(page)
                if not page or (total and start >= total):
                    break

        if len(jobs) >= max_jobs:
            self.partial = True
        return jobs[:max_jobs]

    def fetch_description(self, http, job):
        # The list gives display_job_id, the detail endpoint wants the numeric position id.
        position_id = _position_id(job)
        if not position_id:
            return job.get("description", ""), []
        payload = http.get_json(self._detail_url(position_id),
                                params={"domain": self.param("domain")})
        return html_to_text(payload.get("job_description")), []


def _position_id(job):
    """The numeric id is the last path segment of canonicalPositionUrl."""
    url = (job.get("job_url") or "").rstrip("/")
    tail = url.rsplit("/", 1)[-1]
    return tail if tail.isdigit() else job.get("external_id", "")


def _epoch_to_date(value):
    """Eightfold timestamps are unix seconds; anything else is left alone."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()
