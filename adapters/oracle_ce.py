"""Oracle Recruiting Candidate Experience sites (careers.<company>.com/en/sites/CX_1).

The public careers host is a skin over an Oracle Fusion pod, and the JSON lives on the
pod: /hcmRestApi/resources/latest/recruitingCEJobRequisition(s|Details). Two quirks
drive this adapter:

  * `expand=requisitionList.secondaryLocations` is mandatory - without it the response
    still reports TotalJobsCount but carries no jobs at all.
  * paging goes inside the `finder` string (`limit=`, `offset=`), not as query params,
    and Oracle caps a page at 200.

The detail resource returns description, responsibilities and qualifications separately,
mapped as sections so "Qualifications" feeds the required-skills bucket.
"""

from adapters.base import Adapter, AdapterError, make_job
from core.text import html_to_text

LIST_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
DETAIL_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
LIST_EXPAND = "requisitionList.secondaryLocations"
PAGE_SIZE = 100
MAX_PAGE_SIZE = 200
WORK_MODES = {"ORA_ONSITE": "On-site", "ORA_REMOTE": "Remote", "ORA_HYBRID": "Hybrid"}
DETAIL_SECTIONS = (
    ("Description", "ExternalDescriptionStr"),
    ("Responsibilities", "ExternalResponsibilitiesStr"),
    ("Qualifications", "ExternalQualificationsStr"),
)


class OracleCeAdapter(Adapter):
    slug = "oracle_ce"
    description_in_list = False
    required_params = ("host", "site_number", "base_url")

    @property
    def site(self):
        return self.param("site_number")

    def _page_size(self):
        return min(int(self.param("page_size", PAGE_SIZE)), MAX_PAGE_SIZE)

    def _list_finder(self, keyword, offset):
        parts = [f"siteNumber={self.site}", f"limit={self._page_size()}", f"offset={offset}",
                 "sortBy=POSTING_DATES_DESC"]
        if keyword:
            parts.append(f"keyword={keyword}")
        return "findReqs;" + ",".join(parts)

    def _list_query(self, keyword, offset):
        return {"onlyData": "true", "expand": LIST_EXPAND,
                "finder": self._list_finder(keyword, offset)}

    def _keywords(self):
        keyword = self.param("keyword", "")
        return keyword if isinstance(keyword, list) else [keyword]

    def _public_url(self, requisition_id):
        return (f"{self.param('base_url').rstrip('/')}/{self.param('locale', 'en')}"
                f"/sites/{self.site}/job/{requisition_id}")

    def raw(self, http):
        return http.get_json(f"https://{self.param('host')}{LIST_PATH}",
                             params=self._list_query(self._keywords()[0], 0))

    def fetch(self, http):
        max_jobs = int(self.param("max_jobs", 400))
        page_size = self._page_size()
        jobs, seen_ids = [], set()
        self.partial = bool(self.param("keyword"))

        for keyword in self._keywords():
            offset, total = 0, None
            while len(jobs) < max_jobs:
                payload = http.get_json(f"https://{self.param('host')}{LIST_PATH}",
                                        params=self._list_query(keyword, offset))
                items = payload.get("items") or []
                if not items or "requisitionList" not in items[0]:
                    raise AdapterError(
                        f"{self.name}: oracle payload has no 'requisitionList' "
                        f"(expand={LIST_EXPAND} missing?)"
                    )

                if total is None:
                    total = int(items[0].get("TotalJobsCount") or 0)

                page = items[0]["requisitionList"]
                for item in page:
                    requisition_id = str(item.get("Id") or "")
                    if not requisition_id or requisition_id in seen_ids:
                        continue
                    seen_ids.add(requisition_id)
                    url = self._public_url(requisition_id)
                    jobs.append(make_job(
                        self.name,
                        item.get("Title"),
                        location=_locations(item),
                        department=item.get("Department") or item.get("JobFunction") or "",
                        employment_type=item.get("JobSchedule") or item.get("JobType") or "",
                        work_mode=WORK_MODES.get(item.get("WorkplaceTypeCode") or "", ""),
                        source=self.slug,
                        external_id=requisition_id,
                        job_url=url,
                        apply_url=url,
                        posted_date=str(item.get("PostedDate") or "")[:10],
                        description="",
                    ))

                offset += page_size
                if len(page) < page_size or (total and offset >= total):
                    break

        if len(jobs) >= max_jobs:
            self.partial = True
        return jobs[:max_jobs]

    def fetch_description(self, http, job):
        payload = http.get_json(
            f"https://{self.param('host')}{DETAIL_PATH}",
            params={"onlyData": "true", "expand": "all",
                    "finder": f'ById;Id="{job["external_id"]}",siteNumber={self.site}'},
        )
        items = payload.get("items") or []
        if not items:
            return job.get("description", ""), []

        detail = items[0]
        sections = []
        for heading, key in DETAIL_SECTIONS:
            body = html_to_text(detail.get(key))
            if body:
                sections.append((heading, body))
        text = "\n\n".join(f"{heading}\n{body}" for heading, body in sections)
        return text, sections


def _locations(item):
    """Primary location plus whatever secondaryLocations the expand pulled in."""
    names = [item.get("PrimaryLocation") or ""]
    names += [secondary.get("LocationName") or secondary.get("Name") or ""
              for secondary in item.get("secondaryLocations") or []]
    seen, unique = set(), []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            unique.append(name)
    return ", ".join(unique)
