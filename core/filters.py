"""Filtering and dedupe.

Cheap filters (title, location, company) run before descriptions are fetched; deep
filters run after, once requirements are parsed. Every rejection carries a reason
string, which gets stored so you can see why a job never showed up.
"""


def job_key(job):
    """Identity across runs: external id where the board gives one, else title+location."""
    company = (job.get("company") or "").strip().lower()
    external_id = (job.get("external_id") or "").strip().lower()
    if external_id:
        return f"{company}|id:{external_id}"
    title = (job.get("title") or "").strip().lower()
    location = (job.get("location") or "").strip().lower()
    return f"{company}|tl:{title}|{location}"


def dedupe(jobs):
    seen, unique = set(), []
    for job in jobs:
        key = job_key(job)
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique


def apply_cheap_filters(jobs, config):
    """Title / company / location only - no description needed."""
    kept, rejected = [], []
    for job in jobs:
        reason = (_company_reason(job, config)
                  or _title_reason(job, config)
                  or _location_reason(job, config))
        (rejected if reason else kept).append((job, reason) if reason else job)
    return kept, rejected


def apply_deep_filters(jobs, config):
    """Description keywords, parsed experience and required skills.

    Location is re-checked here because some boards only give a placeholder in the list
    ("3 Locations" on Workday) and the real location arrives with the detail call.
    """
    kept, rejected = [], []
    for job in jobs:
        reason = (_location_reason(job, config)
                  or _desc_reason(job, config)
                  or _experience_reason(job, config)
                  or _skill_reason(job, config)
                  or _language_reason(job, config))
        (rejected if reason else kept).append((job, reason) if reason else job)
    return kept, rejected


def _company_reason(job, config):
    company = (job.get("company") or "").lower()
    for word in config.get("company_exclude") or []:
        if word.lower() in company:
            return f"company_exclude:{word}"
    return None


def _title_reason(job, config):
    title = (job.get("title") or "").lower()
    for word in config.get("title_exclude") or []:
        if word.lower() in title:
            return f"title_exclude:{word}"

    include = config.get("title_include") or []
    if include and not any(word.lower() in title for word in include):
        return "title_include:no_match"
    return None


def _location_reason(job, config):
    location = (job.get("location") or "").lower()
    for word in config.get("location_exclude") or []:
        if word.lower() in location:
            return f"location_exclude:{word}"

    wanted = config.get("locations") or []
    if not wanted:
        return None
    if not location:
        return None if config.get("keep_unknown_location", True) else "location:unknown"
    if any(word.lower() in location for word in wanted):
        return None
    return "location:no_match"


def _desc_reason(job, config):
    description = (job.get("description") or "").lower()
    if not description:
        return None if config.get("keep_missing_description", True) else "description:missing"
    for word in config.get("desc_words") or []:
        if word.lower() in description:
            return f"desc_words:{word}"
    return None


def _experience_reason(job, config):
    ceiling = config.get("max_min_experience")
    minimum_wanted = config.get("min_min_experience")
    years = job.get("min_years_exp")

    if years is None:
        if config.get("drop_if_experience_unknown", False):
            return "experience:unknown"
        return None
    if ceiling is not None and years > ceiling:
        return f"experience:min_{years}y>{ceiling}y"
    if minimum_wanted is not None and years < minimum_wanted:
        return f"experience:min_{years}y<{minimum_wanted}y"
    return None


def _skill_reason(job, config):
    wanted = config.get("require_any_skill") or []
    if not wanted:
        return None
    have = {
        skill.strip().lower()
        for skill in (job.get("skills_required", "") + "," + job.get("skills_preferred", "")).split(",")
        if skill.strip()
    }
    if not have:
        if config.get("drop_if_no_skills_found", False):
            return "skills:none_detected"
        return None
    if any(skill.lower() in have for skill in wanted):
        return None
    return f"skills:none_of_{'/'.join(wanted)}"


def _language_reason(job, config):
    languages = config.get("languages") or []
    description = job.get("description") or ""
    if not languages or len(description) < 40:
        return None
    try:
        from langdetect import detect
        from langdetect.lang_detect_exception import LangDetectException
    except ImportError:
        return None
    try:
        detected = detect(description)
    except LangDetectException:
        return None
    return None if detected in languages else f"language:{detected}"
