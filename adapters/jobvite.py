"""Jobvite job boards: https://jobs.jobvite.com/<token>/search

Jobvite publishes no JSON for these boards - the search page is server-rendered, so the
list is parsed out of the markup. Tenants are served one of two templates, and both are
in use among the companies in the registry, so this handles either:

  card (Exabeam)        <a class="flex-row" href="/<token>/job/<id>">
                          <div class="jv-job-list-name">Title</div>
                          <div class="jv-job-list-location">Department</div>
                          <div class="ml-auto jv-job-list-location">City, Country</div>
                        </a>

  table (Gigamon)       <tr>
                          <td class="jv-job-list-name"><a href="/<token>/job/<id>">Title</a></td>
                          <td class="jv-job-list-location">City, Country</td>
                        </tr>

So the row is found from the title element rather than by a fixed container selector,
and `.jv-job-list-location` is read by position: the card template carries department
then location, the table template carries location alone. Neither template exposes a
posting date. The page has no pagination controls and serves the whole board at once;
`max_jobs` is only a guard against a board that grows unexpectedly.
"""

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from adapters.base import Adapter, AdapterError, make_job
from core.text import clean_whitespace, html_to_text

BASE = "https://jobs.jobvite.com"
LIST_URL = BASE + "/{token}/search"
TITLE_SELECTOR = ".jv-job-list-name"
META_SELECTOR = ".jv-job-list-location"
DESCRIPTION_SELECTOR = ".jv-job-detail-description"
ROW_TAGS = ("tr", "a", "li")


class JobviteAdapter(Adapter):
    slug = "jobvite"
    description_in_list = False
    required_params = ("company_token",)

    def url(self):
        return LIST_URL.format(token=self.param("company_token"))

    def raw(self, http):
        return {"url": self.url(), "html": http.get_text(self.url())[:4000]}

    def fetch(self, http):
        max_jobs = int(self.param("max_jobs", 500))
        soup = BeautifulSoup(http.get_text(self.url()), "html.parser")
        titles = soup.select(TITLE_SELECTOR)
        if not titles:
            raise AdapterError(
                f"{self.name}: no job rows at {self.url()} - jobvite markup may have changed"
            )

        jobs = []
        for title in titles[:max_jobs]:
            row = _row_of(title)
            href = _href_of(title, row)
            if not href:
                continue

            # Card template: department then location. Table template: location only.
            meta = [_one_line(block) for block in row.select(META_SELECTOR)]
            job_url = urljoin(BASE, href)
            jobs.append(make_job(
                self.name,
                title.get_text(" ", strip=True),
                location=meta[-1] if meta else "",
                department=meta[0] if len(meta) > 1 else "",
                source=self.slug,
                external_id=href.rstrip("/").rsplit("/", 1)[-1],
                job_url=job_url,
                apply_url=job_url,
                posted_date="",  # neither template exposes one, on the list or the detail
                description="",
            ))

        self.partial = len(titles) > max_jobs
        return jobs

    def fetch_description(self, http, job):
        soup = BeautifulSoup(http.get_text(job["job_url"]), "html.parser")
        block = soup.select_one(DESCRIPTION_SELECTOR)
        return (html_to_text(str(block)) if block else ""), []


def _row_of(title):
    """The element holding one listing: the <tr>, <a> or <li> the title sits in."""
    row = title.find_parent(ROW_TAGS)
    return row if row is not None else title.parent


def _href_of(title, row):
    """The job link, which is inside the title in the table template and *is* the row
    in the card template - and BeautifulSoup's `find` only looks at descendants."""
    for candidate in (title, row):
        if candidate.name == "a" and candidate.get("href"):
            return candidate["href"]
        link = candidate.find("a", href=True)
        if link:
            return link["href"]
    return ""


def _one_line(block):
    """Jobvite pretty-prints cells across several lines; collapse them into one."""
    return clean_whitespace(block.get_text(" ")).replace("\n", " ")
