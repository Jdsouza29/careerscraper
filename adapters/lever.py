"""Lever job boards: https://api.lever.co/v0/postings/<token>?mode=json

Lever returns titled sub-lists ("Responsibilities", "What We're Looking For"), which
map straight onto the required/preferred buckets the requirement parser wants.
"""

from datetime import datetime, timezone

from adapters.base import Adapter, AdapterError, make_job
from core.text import html_to_text

API = "https://api.lever.co/v0/postings/{token}?mode=json"


class LeverAdapter(Adapter):
    slug = "lever"
    description_in_list = True
    required_params = ("board_token",)

    def url(self):
        return API.format(token=self.param("board_token"))

    def raw(self, http):
        return http.get_json(self.url())

    def fetch(self, http):
        payload = self.raw(http)
        if not isinstance(payload, list):
            raise AdapterError(f"{self.name}: lever payload was not a list of postings")

        jobs = []
        for item in payload:
            categories = item.get("categories") or {}
            sections = [("Description", html_to_text(item.get("descriptionBodyPlain")
                                                     or item.get("descriptionPlain")))]
            for block in item.get("lists") or []:
                sections.append((block.get("text", ""), html_to_text(block.get("content"))))
            if item.get("additionalPlain"):
                sections.append(("Additional", html_to_text(item["additionalPlain"])))

            locations = categories.get("allLocations") or []
            jobs.append(make_job(
                self.name,
                item.get("text"),
                location=categories.get("location") or ", ".join(locations),
                department=categories.get("team") or categories.get("department") or "",
                employment_type=categories.get("commitment") or "",
                work_mode=item.get("workplaceType") or "",
                source=self.slug,
                external_id=item.get("id"),
                job_url=item.get("hostedUrl"),
                apply_url=item.get("applyUrl"),
                posted_date=_epoch_ms_to_date(item.get("createdAt")),
                description="\n\n".join(
                    f"{heading}\n{body}".strip() for heading, body in sections if body
                ),
                sections=sections,
            ))
        return jobs


def _epoch_ms_to_date(value):
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""
