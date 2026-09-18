"""Adapter contract plus the normalized job shape every adapter must return."""


class AdapterError(Exception):
    """Raised when an adapter is misconfigured or a payload looks nothing like expected."""


JOB_FIELDS = (
    "company",
    "title",
    "location",
    "department",
    "employment_type",
    "work_mode",
    "source",
    "external_id",
    "job_url",
    "apply_url",
    "posted_date",
    "description",
)


def make_job(company, title, **fields):
    """Build a normalized job record. `sections` is parser-only and never stored."""
    job = {name: "" for name in JOB_FIELDS}
    job["company"] = company
    job["title"] = " ".join(str(title or "").split())
    for key, value in fields.items():
        if key == "sections":
            continue
        if key not in JOB_FIELDS:
            raise AdapterError(f"unknown job field {key!r}")
        job[key] = "" if value is None else str(value).strip()
    job["sections"] = fields.get("sections") or []
    return job


class Adapter:
    """One backend behind a careers page.

    Subclasses implement `fetch`, and `fetch_description` when the list endpoint
    does not already carry descriptions.
    """

    slug = ""
    description_in_list = True
    required_params = ()

    def __init__(self, company):
        # Set by adapters that only saw part of a board (server-side query, page cap).
        # A partial view must never be used to decide a job has closed.
        self.partial = False
        self.company = company
        self.name = company.get("name", "")
        self.params = company.get("params") or {}
        missing = [p for p in self.required_params if not self.params.get(p)]
        if missing:
            raise AdapterError(
                f"{self.name}: adapter '{self.slug}' needs params {', '.join(missing)}"
            )

    def param(self, key, default=None):
        return self.params.get(key, default)

    def fetch(self, http):
        raise NotImplementedError

    def fetch_description(self, http, job):
        """Return (text, sections) for one job. Only called when needed."""
        return job.get("description", ""), job.get("sections", [])

    def raw(self, http):
        """Raw upstream payload, for `--raw` debugging."""
        return {"note": f"adapter '{self.slug}' does not expose a raw payload"}
