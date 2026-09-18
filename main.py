#!/usr/bin/env python3
"""Company career-page job scraper.

Reads a curated registry of companies (companies.json), pulls their open roles straight
from whatever backend serves their careers page, parses requirements out of each
description, filters against config.json, and stores what survives in SQLite + CSV.

    python main.py                          # every enabled company
    python main.py --company ibm            # one company
    python main.py --company ibm --dry-run  # fetch + parse + print, no DB writes
    python main.py --company ibm --raw      # dump the raw upstream payload
    python main.py --list-companies         # registry + stored stats
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adapters import build_adapter                      # noqa: E402
from adapters.base import AdapterError                  # noqa: E402
from core import filters, requirements, store           # noqa: E402
from core.http import HttpClient, HttpError             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
DEFAULT_REGISTRY = os.path.join(HERE, "companies.json")


def resolve(path):
    """Config paths are relative to this script, not to wherever it was invoked from."""
    return path if os.path.isabs(path) else os.path.join(HERE, path)


def load_json(path, label):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        sys.exit(f"error: {label} not found at {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"error: {label} at {path} is not valid JSON ({exc})")


def select_companies(registry, wanted, include_disabled=False):
    companies = registry.get("companies") or []
    if not wanted:
        return [c for c in companies if include_disabled or c.get("enabled", True)]

    names = [name.strip().lower() for name in wanted if name.strip()]
    chosen, missing = [], []
    for name in names:
        matches = [c for c in companies
                   if c.get("name", "").lower() == name
                   or name in c.get("name", "").lower()]
        if matches:
            chosen.extend(matches)
        else:
            missing.append(name)
    if missing:
        known = ", ".join(sorted(c.get("name", "?") for c in companies))
        sys.exit(f"error: no company matching {', '.join(missing)}. Known: {known}")

    unique, seen = [], set()
    for company in chosen:
        if company.get("name") not in seen:
            seen.add(company.get("name"))
            unique.append(company)
    return unique


def scrape_company(company, http, config, conn, args):
    """Fetch, filter and (unless dry-run) store one company. Returns a stats dict."""
    name = company.get("name", "?")
    stats = Counter()
    adapter = build_adapter(company)

    listings = filters.dedupe(adapter.fetch(http))
    stats["fetched"] = len(listings)
    for job in listings:
        job["key"] = filters.job_key(job)
    live_keys = {job["key"] for job in listings}

    known = {} if (args.dry_run or conn is None) else store.known_keys(conn, [name])
    if conn is not None and not args.dry_run:
        stats["reopened"] = store.touch_last_seen(conn, live_keys & set(known), _now())

    fresh = [job for job in listings if job["key"] not in known]
    stats["already_seen"] = stats["fetched"] - len(fresh)

    if args.no_filter:
        candidates, cheap_rejects = fresh, []
    else:
        candidates, cheap_rejects = filters.apply_cheap_filters(fresh, config)
    stats["passed_title_location"] = len(candidates)

    if args.limit:
        candidates = candidates[:args.limit]

    for job in candidates:
        if not adapter.description_in_list and not job.get("description"):
            try:
                text, sections = adapter.fetch_description(http, job)
                job["description"], job["sections"] = text, sections
                stats["descriptions_fetched"] += 1
            except (HttpError, AdapterError) as exc:
                stats["description_errors"] += 1
                if args.verbose:
                    print(f"    ! description failed for {job['title']}: {exc}")
        job.update(requirements.parse(job, config))

    if args.no_filter:
        keepers, deep_rejects = candidates, []
    else:
        keepers, deep_rejects = filters.apply_deep_filters(candidates, config)

    rejects = cheap_rejects + deep_rejects
    stats["filtered_out"] = len(rejects)
    stats["new"] = len(keepers)

    if conn is not None and not args.dry_run:
        store.insert_jobs(conn, store.JOBS_TABLE, keepers, _now())
        reject_jobs = [job for job, _ in rejects]
        reasons = {job["key"]: reason for job, reason in rejects}
        store.insert_jobs(conn, store.FILTERED_TABLE, reject_jobs, _now(), reasons)
        if adapter.partial:
            stats["partial"] = 1     # narrowed fetch - absence proves nothing
        else:
            stats["closed"] = store.mark_closed(conn, name, live_keys, _now())

    return {"stats": stats, "new_jobs": keepers, "rejects": rejects}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def print_jobs_table(jobs):
    if not jobs:
        print("  (no new jobs)")
        return

    widths = {"company": 16, "title": 44, "location": 24, "exp": 7, "skills": 30}
    header = (f"  {'COMPANY':{widths['company']}} {'TITLE':{widths['title']}} "
              f"{'LOCATION':{widths['location']}} {'EXP':{widths['exp']}} SKILLS")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for job in jobs:
        low, high = job.get("min_years_exp"), job.get("max_years_exp")
        if low is None:
            exp = "?"
        elif high and high != low:
            exp = f"{low}-{high}y"
        else:
            exp = f"{low}+y"
        print(f"  {_cut(job.get('company'), widths['company'])} "
              f"{_cut(job.get('title'), widths['title'])} "
              f"{_cut(job.get('location'), widths['location'])} "
              f"{_cut(exp, widths['exp'])} "
              f"{_cut(job.get('skills_required'), widths['skills'])}")
        print(f"      {job.get('job_url', '')}")


def _cut(value, width):
    text = " ".join(str(value or "").split())
    if len(text) <= width:
        return f"{text:{width}}"
    return text[:width - 1] + "…"


def print_summary(results, failures):
    print("\nRun summary")
    header = f"  {'COMPANY':22} {'FETCHED':>7} {'SEEN':>6} {'NEW':>5} {'FILTERED':>9} {'CLOSED':>7}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    totals = Counter()
    for name, result in results.items():
        stats = result["stats"]
        totals.update(stats)
        print(f"  {_cut(name, 22)} {stats['fetched']:>7} {stats['already_seen']:>6} "
              f"{stats['new']:>5} {stats['filtered_out']:>9} {stats['closed']:>7}")
    print("  " + "-" * (len(header) - 2))
    print(f"  {'TOTAL':22} {totals['fetched']:>7} {totals['already_seen']:>6} "
          f"{totals['new']:>5} {totals['filtered_out']:>9} {totals['closed']:>7}")

    for name, result in results.items():
        if result["stats"]["fetched"] == 0:
            print(f"  ! {name}: 0 jobs returned - adapter or params may be stale")

    for name, error in failures.items():
        print(f"  x {name}: {error}")


def print_reasons(results):
    reasons = Counter()
    for result in results.values():
        for _, reason in result["rejects"]:
            reasons[reason.split(":")[0] if reason else "unknown"] += 1
    if reasons:
        print("\nFiltered-out reasons")
        for reason, count in reasons.most_common():
            print(f"  {reason:28} {count}")


def cmd_close_stale(conn, args):
    """Close jobs nothing has confirmed for a while.

    A scrape can only close what it saw the whole of: companies fetched with a
    server-side query, capped by `max_jobs`, or whose board failed outright are skipped
    by `mark_closed`, so their rows stay open indefinitely. This closes them by age.
    """
    days = args.close_stale
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    stale = store.stale_jobs(conn, cutoff)
    if not stale:
        print(f"Nothing open is older than {days} day(s). Cutoff {cutoff[:10]}.")
        return

    by_company = Counter(job["company"] for job in stale)
    print(f"{len(stale)} open job(s) not seen since {cutoff[:10]} "
          f"({days} day(s)), across {len(by_company)} company(ies):")
    for company, count in by_company.most_common():
        print(f"  {company:26} {count:4}")

    if args.dry_run:
        print("\n--dry-run: nothing was closed. Drop --dry-run to close these.")
        return

    closed = store.close_stale(conn, cutoff, _now())
    print(f"\nClosed {closed} job(s). They reopen automatically if a board lists them again.")


def cmd_show(conn, args):
    """Browse what is already stored - no network, no scraping."""
    wanted = [name for group in args.company for name in group.split(",") if name.strip()]
    jobs = store.query_jobs(
        conn,
        companies=_match_stored_companies(conn, wanted) if wanted else None,
        skill=args.skill,
        location=args.location,
        max_exp=args.max_exp,
        since=args.since,
        include_closed=args.include_closed,
        limit=args.limit,
    )
    if not jobs:
        print("No stored jobs match. Run a scrape first, or loosen the filters.")
        return

    header = (f"  {'ID':>5} {'COMPANY':18} {'TITLE':44} {'LOCATION':22} "
              f"{'EXP':>6} {'MODE':7} SKILLS")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for job in jobs:
        print(f"  {job['id']:>5} {_cut(job['company'], 18)} {_cut(job['title'], 44)} "
              f"{_cut(job['location'], 22)} {_exp_label(job):>6} "
              f"{_cut(job.get('work_mode') or '-', 7)} "
              f"{_cut(job.get('skills_required'), 34)}")
    print(f"\n  {len(jobs)} job(s). `--job <ID>` for the full posting.")

    if args.export:
        print("  Wrote " + store.export_csv(jobs, resolve(args.export)))


def cmd_job(conn, job_id):
    job = store.get_job(conn, job_id)
    if job is None:
        print(f"No job with id {job_id}.")
        return

    print(f"\n{job['title']}  @  {job['company']}")
    print("=" * 78)
    for label, key in (("Location", "location"), ("Work mode", "work_mode"),
                       ("Department", "department"), ("Type", "employment_type"),
                       ("Experience", None), ("Evidence", "exp_snippet"),
                       ("Skills required", "skills_required"),
                       ("Skills preferred", "skills_preferred"),
                       ("Education", "education"), ("Posted", "posted_date"),
                       ("First seen", "first_seen"), ("Last seen", "last_seen"),
                       ("Open", "is_open"), ("Filtered because", "filter_reason"),
                       ("Source", "source"), ("URL", "job_url")):
        value = _exp_label(job) if key is None else job.get(key)
        if value not in (None, "", []):
            print(f"{label + ':':18}{value}")

    print("-" * 78)
    print(job.get("description") or "(no description stored)")


def _exp_label(job):
    low, high = job.get("min_years_exp"), job.get("max_years_exp")
    if low is None:
        return "?"
    return f"{low}-{high}y" if high and high != low else f"{low}+y"


def _match_stored_companies(conn, wanted):
    """Map user-typed fragments onto the company names actually in the database."""
    stored = [row["company"] for row in
              conn.execute(f'SELECT DISTINCT company FROM "{store.JOBS_TABLE}"')]
    matched = [name for name in stored
               if any(fragment.strip().lower() in name.lower() for fragment in wanted)]
    return matched or wanted


def cmd_list_companies(registry, conn):
    summary = store.company_summary(conn) if conn is not None else {}
    header = f"  {'COMPANY':24} {'ADAPTER':16} {'OPEN':>5} {'LAST RUN':20} CAREERS URL"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for company in registry.get("companies") or []:
        name = company.get("name", "?")
        row = summary.get(name, {})
        flag = "" if company.get("enabled", True) else " (disabled)"
        print(f"  {_cut(name + flag, 24)} {_cut(company.get('adapter'), 16)} "
              f"{str(row.get('open_jobs') or 0):>5} "
              f"{_cut((row.get('last_seen') or '-')[:19], 20)} "
              f"{company.get('careers_url', '')}")


def cmd_raw(companies, http):
    for company in companies:
        print(f"=== {company.get('name')} raw payload ===")
        payload = build_adapter(company).raw(http)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        print(text[:20000] + ("\n... (truncated)" if len(text) > 20000 else ""))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scrape company career pages for jobs.")
    parser.add_argument("--company", "-c", action="append", default=[],
                        help="company name(s) to run, comma-separated; repeatable")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--companies", default=DEFAULT_REGISTRY, dest="registry")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch, parse and print without touching the database")
    parser.add_argument("--raw", action="store_true",
                        help="print the raw upstream payload and exit (adapter debugging)")
    parser.add_argument("--list-companies", action="store_true")
    parser.add_argument("--show", action="store_true",
                        help="browse stored jobs instead of scraping")
    parser.add_argument("--job", type=int, metavar="ID",
                        help="print one stored posting in full, with parsed fields")
    parser.add_argument("--skill", help="--show filter: skill must appear (e.g. Go)")
    parser.add_argument("--location", help="--show filter: location contains")
    parser.add_argument("--max-exp", type=int, dest="max_exp",
                        help="--show filter: parsed minimum years at most N")
    parser.add_argument("--since", help="--show filter: first seen on/after YYYY-MM-DD")
    parser.add_argument("--include-closed", action="store_true",
                        help="--show: include roles that have since closed")
    parser.add_argument("--export", metavar="PATH", help="--show: also write the view to CSV")
    parser.add_argument("--close-stale", type=int, metavar="DAYS", dest="close_stale",
                        help="close open jobs not seen for DAYS days (no scraping). "
                             "The backstop for boards a run can only see part of. "
                             "Add --dry-run to preview.")
    parser.add_argument("--no-filter", action="store_true", help="skip all filtering")
    parser.add_argument("--limit", type=int, default=0,
                        help="cap jobs processed per company (quick checks)")
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    config = load_json(args.config, "config")
    registry = load_json(args.registry, "companies registry")

    wanted = [name for group in args.company for name in group.split(",")]
    http = HttpClient(
        headers=config.get("headers"),
        proxies=config.get("proxies"),
        timeout=config.get("timeout", 20),
        retries=config.get("retries", 3),
        min_interval=config.get("min_request_interval", 0.4),
    )

    if args.raw:
        cmd_raw(select_companies(registry, wanted, include_disabled=True), http)
        return 0

    conn = None
    if not args.dry_run or args.list_companies or args.show or args.job or args.close_stale:
        conn = store.connect(resolve(config.get("db_path", "data/jobs.db")))

    if args.list_companies:
        cmd_list_companies(registry, conn)
        return 0

    if args.job:
        cmd_job(conn, args.job)
        return 0

    if args.show:
        cmd_show(conn, args)
        return 0

    if args.close_stale:
        cmd_close_stale(conn, args)
        return 0

    companies = select_companies(registry, wanted, args.include_disabled)
    if not companies:
        print("No enabled companies in the registry. Add one to companies.json.")
        return 1

    started = time.perf_counter()
    results, failures = {}, {}
    for company in companies:
        name = company.get("name", "?")
        print(f"\n>> {name} ({company.get('adapter')})")
        try:
            results[name] = scrape_company(company, http, config, conn, args)
        except (AdapterError, HttpError) as exc:
            failures[name] = str(exc)
            print(f"   failed: {exc}")
            continue
        except Exception as exc:                       # keep one bad board from ending the run
            failures[name] = f"{type(exc).__name__}: {exc}"
            print(f"   failed: {type(exc).__name__}: {exc}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            continue
        print_jobs_table(results[name]["new_jobs"])

    print_summary(results, failures)
    if args.verbose:
        print_reasons(results)

    new_jobs = [job for result in results.values() for job in result["new_jobs"]]
    if new_jobs and not args.dry_run:
        out_dir = resolve(config.get("out_dir", "out"))
        stamp = datetime.now().strftime("%Y-%m-%d")
        print("\nWrote " + store.export_csv(new_jobs, os.path.join(out_dir, f"new_jobs_{stamp}.csv")))
        print("Wrote " + store.export_csv(store.open_jobs(conn), os.path.join(out_dir, "open_jobs.csv")))

    print(f"\nFinished in {time.perf_counter() - started:.1f}s - "
          f"{len(new_jobs)} new job(s) across {len(results)} company(ies)")

    if conn is not None:
        conn.close()
    return 1 if failures and not results else 0


if __name__ == "__main__":
    sys.exit(main())
