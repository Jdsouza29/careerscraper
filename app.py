#!/usr/bin/env python3
"""Web UI for browsing scraped jobs.

Read-only over the same SQLite database main.py writes, so it never scrapes and never
mutates. Start it with:

    ./venv/bin/python app.py            # http://127.0.0.1:5000
"""

import json
import os
import sqlite3
from datetime import datetime

from flask import Flask, abort, g, jsonify, redirect, render_template, request

from core import favorites, store

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 60

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


def config_path():
    """CS_CONFIG picks the config, and therefore the database, this UI reads.

        CS_CONFIG=config.sde.json ./venv/bin/python app.py

    Without it the UI reads config.json - which is why a scrape run with --config
    config.sde.json (a different db_path) appears to have changed nothing here.
    """
    path = os.environ.get("CS_CONFIG", "config.json")
    return path if os.path.isabs(path) else os.path.join(HERE, path)


def config():
    with open(config_path(), encoding="utf-8") as handle:
        return json.load(handle)


def db_path():
    path = config().get("db_path", "data/jobs.db")
    return path if os.path.isabs(path) else os.path.join(HERE, path)


def favorites_path():
    path = config().get("favorites_path", favorites.DEFAULT_PATH)
    return path if os.path.isabs(path) else os.path.join(HERE, path)


def get_favorites():
    """The one database the UI writes. Kept apart from the read-only jobs connection."""
    if "favorites" not in g:
        g.favorites = favorites.connect(favorites_path())
    return g.favorites


def get_conn():
    if "conn" not in g:
        path = db_path()
        if not os.path.exists(path):
            abort(503, f"No database yet at {path}. Run main.py first.")
        # Read-only: the UI must never interfere with a scrape in progress.
        g.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        g.conn.row_factory = sqlite3.Row
    return g.conn


@app.teardown_appcontext
def close_conn(_exception):
    for name in ("conn", "favorites"):
        conn = g.pop(name, None)
        if conn is not None:
            conn.close()


@app.template_filter("exp")
def exp_label(job):
    low, high = job.get("min_years_exp"), job.get("max_years_exp")
    if low is None:
        return "—"
    return f"{low}–{high} yrs" if high and high != low else f"{low}+ yrs"


@app.template_filter("skills")
def skill_list(value):
    return [skill.strip() for skill in (value or "").split(",") if skill.strip()]


@app.route("/")
def index():
    conn = get_conn()
    args = request.args

    filters = {
        "text": args.get("q", "").strip() or None,
        # The COMPANY select posts an empty value for "All", and getlist turns that into
        # [""] - truthy, so it used to become a real filter and match nothing at all.
        "companies": [name for name in args.getlist("company") if name.strip()] or None,
        "skill": args.get("skill") or None,
        "location": args.get("location", "").strip() or None,
        "work_mode": args.get("work_mode") or None,
        "max_exp": args.get("max_exp", type=int),
        "since": args.get("since") or None,
        "include_closed": args.get("include_closed") == "1",
        "sort": args.get("sort", "new"),
    }
    page = max(args.get("page", 1, type=int), 1)
    favorites_only = args.get("favorites") == "1"

    starred = favorites.keys(get_favorites())
    matches = store.query_jobs(conn, **filters)
    if favorites_only:
        matches = [row for row in matches if row["key"] in starred]
    total = len(matches)
    jobs = matches[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]

    return render_template(
        "jobs.html",
        jobs=jobs,
        total=total,
        page=page,
        pages=max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1),
        stored=store.count_jobs(conn),
        companies=store.distinct_values(conn, "company"),
        modes=store.distinct_values(conn, "work_mode"),
        skills=store.skill_counts(conn)[:40],
        selected=filters,
        starred=starred,
        favorites_only=favorites_only,
        favorites_total=len(starred),
        query=args,
    )


@app.route("/job/<int:job_id>")
def job(job_id):
    row = store.get_job(get_conn(), job_id)
    if row is None:
        abort(404)
    starred = favorites.is_favorite(get_favorites(), row["key"])
    return render_template("job.html", job=row, starred=starred)


@app.route("/favorite/<int:job_id>", methods=["POST"])
def favorite(job_id):
    """Star or unstar one job. POST so a link prefetch can never flip it."""
    row = store.get_job(get_conn(), job_id)
    if row is None:
        abort(404)
    conn = get_favorites()
    state = favorites.toggle(conn, row["key"], row.get("company") or "",
                             row.get("title") or "", datetime.now().isoformat(timespec="seconds"))
    return jsonify({"favorite": state, "total": favorites.count(conn)})


@app.route("/apply/<int:job_id>")
def apply(job_id):
    """Send the browser to the company's own posting."""
    row = store.get_job(get_conn(), job_id)
    if row is None:
        abort(404)
    target = row.get("apply_url") or row.get("job_url")
    if not target:
        abort(404, "This job has no stored URL.")
    return redirect(target, code=302)


if __name__ == "__main__":
    print(f"Reading {db_path()}  (config: {os.path.basename(config_path())})")
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
