"""Starred jobs, kept in their own SQLite file.

The web UI opens `jobs.db` read-only on purpose, so a scrape running in another
terminal can never be disturbed by it. Favourites are the one thing the UI does write,
so they get a separate database instead of a column on `jobs`.

Rows are keyed by the scrape key (`company|id:<external_id>`), not by the `jobs.id`
rowid: the key is what identifies a posting across runs, and it stays valid even if the
job database is rebuilt from scratch or pointed at a different file. `company` and
`title` are copied in purely so the file is readable on its own.
"""

import os
import sqlite3

DEFAULT_PATH = "./data/favorites.db"


def connect(path=DEFAULT_PATH):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        'CREATE TABLE IF NOT EXISTS "favorites" ('
        "key TEXT PRIMARY KEY, company TEXT, title TEXT, marked_at TEXT)"
    )
    conn.commit()
    return conn


def keys(conn):
    """Every starred key, as a set - the UI needs it once per page render."""
    return {row["key"] for row in conn.execute('SELECT key FROM "favorites"')}


def is_favorite(conn, key):
    row = conn.execute('SELECT 1 FROM "favorites" WHERE key = ?', (key,)).fetchone()
    return row is not None


def toggle(conn, key, company="", title="", now=None):
    """Star an unstarred job or unstar a starred one. Returns the new state."""
    if is_favorite(conn, key):
        conn.execute('DELETE FROM "favorites" WHERE key = ?', (key,))
        conn.commit()
        return False
    conn.execute(
        'INSERT INTO "favorites" (key, company, title, marked_at) VALUES (?, ?, ?, ?)',
        (key, company, title, now),
    )
    conn.commit()
    return True


def count(conn):
    return conn.execute('SELECT COUNT(*) FROM "favorites"').fetchone()[0]
