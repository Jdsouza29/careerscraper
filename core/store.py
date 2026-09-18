"""SQLite persistence and CSV export.

The DB owns the notion of "new": career pages rarely publish posting dates, so a job
is new when we have never seen its key before. `last_seen` is refreshed every run, and
jobs that vanish from a successful fetch get closed rather than deleted.
"""

import csv
import os
import sqlite3
import unicodedata

from core.filters import job_key

COLUMNS = (
    ("key", "TEXT UNIQUE"),
    ("company", "TEXT"),
    ("title", "TEXT"),
    ("location", "TEXT"),
    ("department", "TEXT"),
    ("employment_type", "TEXT"),
    ("work_mode", "TEXT"),
    ("min_years_exp", "INTEGER"),
    ("max_years_exp", "INTEGER"),
    ("exp_snippet", "TEXT"),
    ("skills_required", "TEXT"),
    ("skills_preferred", "TEXT"),
    ("education", "TEXT"),
    ("source", "TEXT"),
    ("external_id", "TEXT"),
    ("job_url", "TEXT"),
    ("apply_url", "TEXT"),
    ("posted_date", "TEXT"),
    ("description", "TEXT"),
    ("first_seen", "TEXT"),
    ("last_seen", "TEXT"),
    ("is_open", "INTEGER DEFAULT 1"),
    ("closed_at", "TEXT"),
)

JOBS_TABLE = "jobs"
FILTERED_TABLE = "filtered_jobs"

CSV_COLUMNS = ("company", "title", "location", "work_mode", "min_years_exp",
               "max_years_exp", "skills_required", "skills_preferred", "education",
               "posted_date", "first_seen", "job_url", "exp_snippet")


def connect(path):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    return conn


def _create_schema(conn):
    for table in (JOBS_TABLE, FILTERED_TABLE):
        extra = ', "filter_reason" TEXT' if table == FILTERED_TABLE else ""
        columns = ", ".join(f'"{name}" {sql_type}' for name, sql_type in COLUMNS)
        conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" '
            f"(id INTEGER PRIMARY KEY AUTOINCREMENT, {columns}{extra})"
        )
        conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_company" '
                     f'ON "{table}"(company)')
    conn.commit()


def known_keys(conn, companies=None):
    """Every key we have already decided on, mapped to its table."""
    known = {}
    for table in (JOBS_TABLE, FILTERED_TABLE):
        sql = f'SELECT key FROM "{table}"'
        params = ()
        if companies:
            placeholders = ", ".join("?" for _ in companies)
            sql += f" WHERE lower(company) IN ({placeholders})"
            params = tuple(name.lower() for name in companies)
        for row in conn.execute(sql, params):
            known.setdefault(row["key"], table)
    return known


def touch_last_seen(conn, keys, now):
    """Refresh last_seen (and reopen anything that came back) for keys we saw again."""
    if not keys:
        return 0
    reopened = 0
    for chunk in _chunks(list(keys), 400):
        placeholders = ", ".join("?" for _ in chunk)
        cursor = conn.execute(
            f'UPDATE "{JOBS_TABLE}" SET last_seen = ?, is_open = 1, closed_at = NULL '
            f"WHERE key IN ({placeholders}) AND is_open = 0",
            (now, *chunk),
        )
        reopened += cursor.rowcount
        for table in (JOBS_TABLE, FILTERED_TABLE):
            conn.execute(
                f'UPDATE "{table}" SET last_seen = ? WHERE key IN ({placeholders})',
                (now, *chunk),
            )
    conn.commit()
    return reopened


def insert_jobs(conn, table, jobs, now, reasons=None):
    """Insert new rows. `reasons` maps key -> filter_reason for the filtered table."""
    if not jobs:
        return 0

    names = [name for name, _ in COLUMNS]
    if table == FILTERED_TABLE:
        names.append("filter_reason")
    sql = (f'INSERT OR IGNORE INTO "{table}" ({", ".join(chr(34) + n + chr(34) for n in names)}) '
           f'VALUES ({", ".join("?" for _ in names)})')

    inserted = 0
    for job in jobs:
        key = job.get("key") or job_key(job)
        row = {
            **{name: job.get(name, "") for name, _ in COLUMNS},
            "key": key,
            "first_seen": now,
            "last_seen": now,
            "is_open": 1,
            "closed_at": None,
        }
        row["min_years_exp"] = job.get("min_years_exp")
        row["max_years_exp"] = job.get("max_years_exp")
        if table == FILTERED_TABLE:
            row["filter_reason"] = (reasons or {}).get(key, job.get("filter_reason", ""))
        inserted += conn.execute(sql, [row[name] for name in names]).rowcount
        # Reflect what was stored back onto the job so CSV export sees first_seen etc.
        job.update({"key": key, "first_seen": now, "last_seen": now, "is_open": 1})

    conn.commit()
    return inserted


def mark_closed(conn, company, live_keys, now):
    """Close open jobs for a company that were absent from a successful fetch."""
    rows = conn.execute(
        f'SELECT key FROM "{JOBS_TABLE}" WHERE lower(company) = ? AND is_open = 1',
        (company.lower(),),
    ).fetchall()
    stale = [row["key"] for row in rows if row["key"] not in live_keys]
    for chunk in _chunks(stale, 400):
        placeholders = ", ".join("?" for _ in chunk)
        conn.execute(
            f'UPDATE "{JOBS_TABLE}" SET is_open = 0, closed_at = ? '
            f"WHERE key IN ({placeholders})",
            (now, *chunk),
        )
    conn.commit()
    return len(stale)


def stale_jobs(conn, cutoff):
    """Open jobs whose last_seen predates `cutoff` - candidates for closing."""
    return [dict(row) for row in conn.execute(
        f'SELECT * FROM "{JOBS_TABLE}" WHERE is_open = 1 AND last_seen < ? '
        f"ORDER BY last_seen, company, title",
        (cutoff,),
    )]


def close_stale(conn, cutoff, now):
    """Close every open job not seen since `cutoff`.

    `mark_closed` can only speak for a company a run actually fetched in full: a narrowed
    fetch (search_text, a max_jobs cap) or a failed one proves nothing about absence, so
    those companies' jobs would otherwise stay open forever. This is the age-based
    backstop for exactly those rows. Closing stays reversible - if a job shows up again,
    `touch_last_seen` reopens it.
    """
    cursor = conn.execute(
        f'UPDATE "{JOBS_TABLE}" SET is_open = 0, closed_at = ? '
        f"WHERE is_open = 1 AND last_seen < ?",
        (now, cutoff),
    )
    conn.commit()
    return cursor.rowcount

def open_jobs(conn, companies=None):
    sql = f'SELECT * FROM "{JOBS_TABLE}" WHERE is_open = 1'
    params = ()
    if companies:
        placeholders = ", ".join("?" for _ in companies)
        sql += f" AND lower(company) IN ({placeholders})"
        params = tuple(name.lower() for name in companies)
    sql += " ORDER BY first_seen DESC, company, title"
    return [dict(row) for row in conn.execute(sql, params)]


SORTS = {
    "new": "first_seen DESC, company, title",
    "company": "company, title",
    "title": "title, company",
    "exp": "min_years_exp IS NULL, min_years_exp, company",
    "posted": "posted_date DESC, company",
}


# Boards spell the same city several ways, so a typed "bengaluru" has to find the
# "Bangalore" postings too - they are different strings for one place.
CITY_ALIASES = (
    ("bengaluru", "bangalore", "blr"),
    ("gurugram", "gurgaon"),
    ("mumbai", "bombay"),
    ("chennai", "madras"),
    ("kolkata", "calcutta"),
    ("pune", "poona"),
    ("hyderabad", "secunderabad"),
    ("delhi", "new delhi", "ncr"),
    ("thiruvananthapuram", "trivandrum"),
    ("kochi", "cochin"),
)


def fold_text(value):
    """Lowercase and drop accents, so a typed "karnataka" matches "Karnātaka"."""
    decomposed = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def location_terms(location):
    """Expand one typed location into every spelling worth matching."""
    term = fold_text(location).strip()
    for group in CITY_ALIASES:
        if term in group:
            return list(group)
    return [term]


def query_jobs(conn, companies=None, skill=None, location=None, max_exp=None,
               since=None, include_closed=False, limit=0, text=None, work_mode=None,
               sort="new", offset=0):
    """Browse stored jobs. Filters compose; all are optional."""
    sql = f'SELECT * FROM "{JOBS_TABLE}" WHERE 1 = 1'
    params = []

    if text:
        sql += (" AND (lower(title) LIKE ? OR lower(company) LIKE ?"
                " OR lower(skills_required) LIKE ? OR lower(description) LIKE ?)")
        params += [f"%{text.lower()}%"] * 4
    if work_mode:
        sql += " AND lower(work_mode) = ?"
        params.append(work_mode.lower())

    if not include_closed:
        sql += " AND is_open = 1"
    if companies:
        sql += f" AND lower(company) IN ({', '.join('?' for _ in companies)})"
        params += [name.lower() for name in companies]
    if skill:
        sql += " AND (lower(skills_required) LIKE ? OR lower(skills_preferred) LIKE ?)"
        params += [f"%{skill.lower()}%"] * 2
    if location:
        # fold_text is registered on the connection so the comparison ignores accents;
        # the OR list covers the alternate names for the same city.
        conn.create_function("fold_text", 1, fold_text)
        terms = location_terms(location)
        sql += " AND (" + " OR ".join("fold_text(location) LIKE ?" for _ in terms) + ")"
        params += [f"%{term}%" for term in terms]
    if max_exp is not None:
        sql += " AND (min_years_exp IS NULL OR min_years_exp <= ?)"
        params.append(max_exp)
    if since:
        sql += " AND first_seen >= ?"
        params.append(since)

    sql += " ORDER BY " + SORTS.get(sort, SORTS["new"])
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
        if offset:
            sql += " OFFSET ?"
            params.append(offset)
    return [dict(row) for row in conn.execute(sql, params)]


def count_jobs(conn, include_closed=False):
    sql = f'SELECT COUNT(*) FROM "{JOBS_TABLE}"'
    if not include_closed:
        sql += " WHERE is_open = 1"
    return conn.execute(sql).fetchone()[0]


def distinct_values(conn, column):
    """Distinct non-empty values of a column, for populating filter dropdowns."""
    rows = conn.execute(
        f'SELECT DISTINCT "{column}" AS value FROM "{JOBS_TABLE}" '
        f'WHERE is_open = 1 AND "{column}" IS NOT NULL AND "{column}" != "" '
        f'ORDER BY "{column}"'
    )
    return [row["value"] for row in rows]


def skill_counts(conn):
    """How often each parsed skill appears across open jobs, most common first."""
    tally = {}
    for row in conn.execute(
        f'SELECT skills_required, skills_preferred FROM "{JOBS_TABLE}" WHERE is_open = 1'
    ):
        for blob in (row["skills_required"], row["skills_preferred"]):
            for skill in (blob or "").split(","):
                skill = skill.strip()
                if skill:
                    tally[skill] = tally.get(skill, 0) + 1
    return sorted(tally.items(), key=lambda item: (-item[1], item[0]))


def get_job(conn, job_id):
    row = conn.execute(f'SELECT * FROM "{JOBS_TABLE}" WHERE id = ?', (job_id,)).fetchone()
    if row is None:
        row = conn.execute(
            f'SELECT * FROM "{FILTERED_TABLE}" WHERE id = ?', (job_id,)
        ).fetchone()
    return dict(row) if row else None


def company_summary(conn):
    return {
        row["company"]: dict(row)
        for row in conn.execute(
            f'SELECT company, COUNT(*) AS total, '
            f'SUM(is_open) AS open_jobs, MAX(last_seen) AS last_seen '
            f'FROM "{JOBS_TABLE}" GROUP BY company'
        )
    }


def export_csv(jobs, path):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for job in jobs:
            writer.writerow({name: job.get(name, "") for name in CSV_COLUMNS})
    return path


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]
