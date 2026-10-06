#!/usr/bin/env python3
"""SQLite persistence layer for Task-Tracker.

Every line of SQL in this project lives in this module. ``app.py`` and the
Jinja2 templates only ever call the helpers below, so the web layer never
touches SQLite directly and the storage engine could be swapped in one place.

Why SQLite (and not JSON/CSV/YAML):

* ``sqlite3`` ships with Python, so there is no database server to install and
  no extra dependency beyond Flask itself.
* Every change is a transaction. An interrupted write can lose the change in
  flight, but it cannot corrupt the file the way a truncated JSON rewrite can.
* ``CHECK`` constraints make invalid values (a mistyped status, a malformed
  date, a bad colour) impossible to store, so the colours on screen can never
  disagree with the data.
* Dates are stored as ISO ``YYYY-MM-DD`` text, which sorts chronologically
  with a plain string comparison.

Run ``python3 db.py --init`` to create the database file and seed categories.
"""

import argparse
import os
import sqlite3
from datetime import date

from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Feature 2 + 3 vocabulary. Keeping these as module constants means the forms,
# the templates and the CHECK constraints all agree on the same strings.
PRIORITIES = ("Low", "Medium", "High")
STATUSES = ("todo", "in_progress", "done")
STATUS_LABELS = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}
DEFAULT_COLOR = "#6b7280"

DEFAULT_CATEGORIES = (
    ("Work", "#2563eb"),
    ("Personal", "#7c3aed"),
    ("Errands", "#0891b2"),
    ("Admin", "#b45309"),
)

# Sort helpers: open work floats to the top of a day, finished work sinks.
STATUS_RANK_SQL = "CASE i.status WHEN 'todo' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END"
PRIORITY_RANK_SQL = "CASE i.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END"

# ---------------------------------------------------------------------------
# Table sorting. The ?sort= and ?dir= query parameters are matched against the
# keys below and never interpolated directly, so a hand-edited URL cannot put
# arbitrary SQL into an ORDER BY.
# ---------------------------------------------------------------------------
# Each sort key is a *list* of ORDER BY expressions. Keeping them separate lets
# the ascending/descending direction apply to every part, so sorting by category
# descending really does reverse the whole column (uncategorised included).
SORT_KEYS = {
    "task": ("i.title COLLATE NOCASE",),
    "status": (STATUS_RANK_SQL,),
    "category": ("CASE WHEN c.name IS NULL THEN 1 ELSE 0 END", "c.name COLLATE NOCASE"),
    "date": ("d.day_date",),
}
SORT_LABELS = (
    ("task", "Task"),
    ("status", "Status"),
    ("category", "Category"),
    ("date", "Date added"),
)
DEFAULT_SORT = "date"
# What the first click on each column should mean: dates read best newest-first,
# everything else alphabetically/from the top.
DEFAULT_DIRECTION = {"date": "desc", "status": "asc", "task": "asc", "category": "asc"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE
               CHECK (length(trim(name)) BETWEEN 1 AND 40),
    color      TEXT    NOT NULL DEFAULT '#6b7280'
               CHECK (color GLOB '#[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]'),
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS days (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day_date   TEXT    NOT NULL UNIQUE
               CHECK (day_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    label      TEXT    NOT NULL DEFAULT '' CHECK (length(label) <= 80),
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day_id      INTEGER NOT NULL REFERENCES days(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
    notes       TEXT    NOT NULL DEFAULT '' CHECK (length(notes) <= 2000),
    priority    TEXT    NOT NULL DEFAULT 'Medium'
                CHECK (priority IN ('Low', 'Medium', 'High')),
    category_id INTEGER          REFERENCES categories(id) ON DELETE SET NULL,
    status      TEXT    NOT NULL DEFAULT 'todo'
                CHECK (status IN ('todo', 'in_progress', 'done')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_days_date    ON days (day_date);
CREATE INDEX IF NOT EXISTS idx_items_day    ON items (day_id);
CREATE INDEX IF NOT EXISTS idx_items_status ON items (status);
"""


class ValidationError(ValueError):
    """User input was rejected. Routes flash the message and redirect back."""


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
def db_path():
    """Path of the SQLite file. Override with the ``TASK_DB`` env var."""
    return os.environ.get("TASK_DB") or os.path.join(BASE_DIR, "tasks.db")


def connect(path=None):
    """Return a configured connection. The caller is responsible for closing it."""
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    # Enforce the REFERENCES above instead of silently ignoring them.
    conn.execute("PRAGMA foreign_keys = ON")
    # Write-ahead logging: readers never block the writer, and a crash mid-way
    # leaves the database in the last committed state.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db():
    """One connection per request, cached on ``flask.g``."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    """Register the per-request teardown handler."""
    app.teardown_appcontext(close_db)


def init_db(path=None):
    """Create the tables if needed, seed categories, return the path used."""
    path = path or db_path()
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO categories (name, color) VALUES (?, ?)",
                DEFAULT_CATEGORIES,
            )
        conn.commit()
    finally:
        conn.close()
    return path
# ---------------------------------------------------------------------------
# Validation. Anything that fails here raises ValidationError, which app.py
# turns into a flashed message rather than a 500.
# ---------------------------------------------------------------------------
def check_date(value, field="date"):
    text = (value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValidationError("Please pick a valid %s (YYYY-MM-DD)." % field)
    if parsed.isoformat() != text:
        raise ValidationError("Please pick a valid %s (YYYY-MM-DD)." % field)
    return text


def valid_date(value):
    """True/False version of :func:`check_date`, for redirect decisions."""
    try:
        check_date(value)
    except ValidationError:
        return False
    return True


def check_priority(value):
    text = (value or "").strip()
    if text not in PRIORITIES:
        raise ValidationError("Priority must be one of: %s." % ", ".join(PRIORITIES))
    return text


def check_status(value):
    text = (value or "").strip()
    if text not in STATUSES:
        raise ValidationError(
            "Status must be one of: %s." % ", ".join(STATUS_LABELS[s] for s in STATUSES)
        )
    return text


def check_color(value):
    text = (value or "").strip().lower()
    if len(text) == 7 and text.startswith("#") and all(c in "0123456789abcdef" for c in text[1:]):
        return text
    raise ValidationError("Colour must be a hex value like #1a2b3c.")


def check_title(value, limit=200):
    text = (value or "").strip()
    if not text:
        raise ValidationError("An item needs a title.")
    if len(text) > limit:
        raise ValidationError("That title is too long (max %d characters)." % limit)
    return text


def _category_id(value):
    """An empty select value means "uncategorised", stored as NULL."""
    if value in (None, "", "None"):
        return None
    try:
        category_id = int(value)
    except (TypeError, ValueError):
        raise ValidationError("Unknown category.")
    row = get_db().execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
    if row is None:
        raise ValidationError("That category no longer exists.")
    return category_id


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_days():
    """Days newest-first, with item totals for the day header."""
    rows = get_db().execute(
        """
        SELECT d.id, d.day_date, d.label,
               COUNT(i.id) AS item_count,
               COALESCE(SUM(CASE WHEN i.status <> 'done' THEN 1 ELSE 0 END), 0) AS open_count
        FROM days d
        LEFT JOIN items i ON i.day_id = d.id
        GROUP BY d.id
        ORDER BY d.day_date DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_day(day_id):
    row = get_db().execute(
        "SELECT id, day_date, label FROM days WHERE id = ?", (day_id,)
    ).fetchone()
    return dict(row) if row else None


def find_day(day_date):
    row = get_db().execute(
        "SELECT id, day_date, label FROM days WHERE day_date = ?", (day_date,)
    ).fetchone()
    return dict(row) if row else None


ITEM_SELECT = """
SELECT i.id, i.day_id, i.title, i.notes, i.priority, i.status, i.category_id,
       i.created_at, i.updated_at, d.day_date,
       c.name AS category, c.color AS category_color
FROM items i
JOIN days d        ON d.id = i.day_id
LEFT JOIN categories c ON c.id = i.category_id
"""

ITEM_ORDER = "ORDER BY %s, %s, i.id" % (STATUS_RANK_SQL, PRIORITY_RANK_SQL)


def resolve_sort(sort, direction):
    """Validate ?sort=/?dir= against the whitelist, falling back to defaults."""
    key = sort if sort in SORT_KEYS else DEFAULT_SORT
    direction = (direction or "").strip().lower()
    if direction not in ("asc", "desc"):
        direction = DEFAULT_DIRECTION.get(key, "asc")
    return key, direction


def _as_list(value):
    """Normalise "one value or many" to a list of the non-empty ones."""
    if value is None or value == "":
        return []
    if isinstance(value, (str, int)):
        return [value]
    return [item for item in value if item not in (None, "")]


def resolve_filters(status=None, category=None):
    """Validate ?status=/?category= from the query string.

    Either argument may be a single value (``?status=done``) or a list of
    values (``?status=todo&status=done``) -- the header dropdowns submit one
    entry per ticked box. Returns ``(statuses, categories)`` where each element
    is either ``None`` (that filter is off) or a non-empty list of values safe
    to bind into a ``WHERE`` clause: statuses from :data:`STATUSES`, and
    category ids or the string ``"none"`` for uncategorised tasks.

    Anything else -- a typo, a hand-edited URL, an injection attempt, a
    category that has since been deleted -- is dropped rather than raised, so a
    bad filter can only ever widen the view, never break it or reach SQLite.
    Selecting *every* value counts as no filter, which is what the dropdowns
    submit when they are left fully ticked.
    """
    statuses = []
    for value in _as_list(status):
        value = str(value).strip().lower()
        if value in STATUSES and value not in statuses:
            statuses.append(value)
    if len(statuses) == len(STATUSES):
        statuses = []                      # every status ticked = no filter

    known = {row["id"] for row in get_db().execute("SELECT id FROM categories")}
    categories = []
    for value in _as_list(category):
        value = str(value).strip().lower()
        if value == "none":
            key = "none"
        elif value.isdigit() and len(value) <= 9:        # ids are small ints
            key = int(value)
            if key not in known:
                continue                   # deleted or invented category
        else:
            continue
        if key not in categories:
            categories.append(key)
    if set(categories) == (known | {"none"}):
        categories = []                    # every box ticked = no filter
    return statuses or None, categories or None


def list_items_sorted(sort=DEFAULT_SORT, direction="", status=None, category=None):
    """Every task in one flat list, ordered by one whitelisted column.

    This is what the task table renders. Ties fall back to ``i.id`` so the order
    is stable and re-clicking a header cannot shuffle equal rows.

    ``status`` and ``category`` come from :func:`resolve_filters`, so they are
    either ``None`` or a non-empty list of bound values -- never interpolated.
    Within a column the values match *any* (an ``IN`` clause, because the
    dropdowns tick several boxes at once); across columns every filter that is
    on must match.
    """
    key, direction = resolve_sort(sort, direction)
    where, params = [], []
    statuses = _as_list(status)
    if statuses:
        where.append("i.status IN (%s)" % ",".join("?" * len(statuses)))
        params.extend(statuses)
    categories = _as_list(category)
    if categories:
        parts = []
        ids = [value for value in categories if value != "none"]
        if ids:
            parts.append("i.category_id IN (%s)" % ",".join("?" * len(ids)))
            params.extend(ids)
        if "none" in categories:
            parts.append("i.category_id IS NULL")
        where.append("(" + " OR ".join(parts) + ")")

    order = direction.upper()
    # Direction is applied to every expression of the key, not just the last one.
    terms = ["%s %s" % (expression, order) for expression in SORT_KEYS[key]]
    terms.append("i.id %s" % order)
    sql = ITEM_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + ", ".join(terms)
    rows = get_db().execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def count_items():
    """All tasks, ignoring filters: the denominator for "showing X of Y"."""
    row = get_db().execute("SELECT COUNT(*) FROM items").fetchone()
    return row[0]


def list_items(day_id):
    """Items for one day: To Do first, then In Progress, then Done (feature 3)."""
    rows = get_db().execute(
        ITEM_SELECT + " WHERE i.day_id = ? " + ITEM_ORDER, (day_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_item(item_id):
    row = get_db().execute(ITEM_SELECT + " WHERE i.id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def items_by_day():
    """Every item, grouped by day id, using a single query."""
    grouped = {}
    for row in get_db().execute(ITEM_SELECT + " " + ITEM_ORDER).fetchall():
        grouped.setdefault(row["day_id"], []).append(dict(row))
    return grouped


def day_views():
    """Every day with its items grouped underneath (kept for the data layer's
    sake; the web UI now renders one flat, sortable table instead)."""
    grouped = items_by_day()
    return [{"day": day, "tasks": grouped.get(day["id"], [])} for day in list_days()]


def list_categories():
    """Categories with the number of tasks using each one, alphabetically."""
    rows = get_db().execute(
        """
        SELECT c.id, c.name, c.color, COUNT(i.id) AS usage
        FROM categories c
        LEFT JOIN items i ON i.category_id = c.id
        GROUP BY c.id
        ORDER BY c.name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_category(category_id):
    """One category plus how many tasks reference it (None if it is gone)."""
    row = get_db().execute(
        """
        SELECT c.id, c.name, c.color, COUNT(i.id) AS usage
        FROM categories c
        LEFT JOIN items i ON i.category_id = c.id
        WHERE c.id = ?
        GROUP BY c.id
        """,
        (category_id,),
    ).fetchone()
    return dict(row) if row else None


def banner_counts():
    """Totals for the banner chips, including how much work is still open."""
    counts = dict.fromkeys(STATUSES, 0)
    rows = get_db().execute(
        "SELECT status, COUNT(*) AS n FROM items GROUP BY status"
    ).fetchall()
    for row in rows:
        counts[row["status"]] = row["n"]
    counts["open"] = counts["todo"] + counts["in_progress"]
    return counts
# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def ensure_day(day_date):
    """Return the day id for a date, creating the day if it does not exist.

    This is what makes the quick-add form work: you type a task, and the date it
    belongs to is created on demand instead of needing its own step.
    """
    day_date = check_date(day_date)
    existing = find_day(day_date)
    if existing is not None:
        return existing["id"]
    db = get_db()
    # Two quick-adds for the same brand-new date (two tabs, or a form submitted
    # twice) race between the check above and this insert. Losing that race is
    # not an error: the row the winner created is exactly the row we want.
    db.execute(
        "INSERT INTO days (day_date) VALUES (?) ON CONFLICT(day_date) DO NOTHING",
        (day_date,),
    )
    db.commit()
    return find_day(day_date)["id"]


def create_day(day_date, label=""):
    """Feature 1: create the date first, then hang items off it."""
    day_date = check_date(day_date)
    label = (label or "").strip()
    if len(label) > 80:
        raise ValidationError("That label is too long (max 80 characters).")
    if find_day(day_date) is not None:
        raise ValidationError("%s is already on the list." % day_date)
    db = get_db()
    cursor = db.execute(
        "INSERT INTO days (day_date, label) VALUES (?, ?)", (day_date, label)
    )
    db.commit()
    return cursor.lastrowid, day_date


def delete_day(day_id):
    """Deleting a day takes its items with it (ON DELETE CASCADE)."""
    db = get_db()
    cursor = db.execute("DELETE FROM days WHERE id = ?", (day_id,))
    db.commit()
    return cursor.rowcount > 0


def add_item(day_id, title, priority="Medium", category_id=None, notes=""):
    try:
        day_id = int(day_id)
    except (TypeError, ValueError):
        raise ValidationError("Pick a day for that item.")
    if get_day(day_id) is None:
        raise ValidationError("That day no longer exists.")

    title = check_title(title)
    priority = check_priority(priority)
    category_id = _category_id(category_id)
    notes = (notes or "").strip()
    if len(notes) > 2000:
        raise ValidationError("Those notes are too long (max 2000 characters).")

    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO items (day_id, title, notes, priority, category_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (day_id, title, notes, priority, category_id),
    )
    db.commit()
    return cursor.lastrowid


def update_item(item_id, title, priority, category_id=None, notes="", day_date=None):
    """Edit a task. ``day_date`` moves the task to a different date if given."""
    title = check_title(title)
    priority = check_priority(priority)
    category_id = _category_id(category_id)
    notes = (notes or "").strip()
    if len(notes) > 2000:
        raise ValidationError("Those notes are too long (max 2000 characters).")

    db = get_db()
    current = db.execute("SELECT day_id FROM items WHERE id = ?", (item_id,)).fetchone()
    if current is None:
        raise ValidationError("That item no longer exists.")

    day_id = current["day_id"]
    if day_date:
        # Moving to another date just means pointing at that day's row, creating
        # it on demand if this is the first task for that date.
        new_day_id = ensure_day(day_date)
        if new_day_id != day_id:
            day_id = new_day_id

    cursor = db.execute(
        """
        UPDATE items
           SET title = ?, notes = ?, priority = ?, category_id = ?, day_id = ?,
               updated_at = datetime('now')
         WHERE id = ?
        """,
        (title, notes, priority, category_id, day_id, item_id),
    )
    db.commit()
    if cursor.rowcount == 0:
        raise ValidationError("That item no longer exists.")
    return True


def set_status(item_id, status):
    """Feature 3: move an item between To Do / In Progress / Done."""
    status = check_status(status)
    db = get_db()
    cursor = db.execute(
        "UPDATE items SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, item_id),
    )
    db.commit()
    if cursor.rowcount == 0:
        raise ValidationError("That item no longer exists.")
    return status


def delete_item(item_id):
    db = get_db()
    cursor = db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    return cursor.rowcount > 0


def delete_items(item_ids):
    """Delete several tasks at once (the bulk tick-box flow).

    ``item_ids`` are already-validated ints; the placeholders keep the SQL
    text fixed no matter how many rows are ticked, and a single commit makes
    the whole deletion atomic. Returns the number of rows removed.
    """
    if not item_ids:
        return 0
    placeholders = ", ".join("?" * len(item_ids))
    db = get_db()
    cursor = db.execute(
        "DELETE FROM items WHERE id IN (%s)" % placeholders, tuple(item_ids)
    )
    db.commit()
    return cursor.rowcount


def create_category(name, color=DEFAULT_COLOR):
    name = (name or "").strip()
    if not name:
        raise ValidationError("A category needs a name.")
    if len(name) > 40:
        raise ValidationError("That category name is too long (max 40 characters).")
    color = check_color(color)

    db = get_db()
    # SQLite's UNIQUE is case-sensitive, so compare case-insensitively here to
    # stop "Work" and "work" becoming two categories.
    existing = db.execute(
        "SELECT 1 FROM categories WHERE lower(name) = lower(?)", (name,)
    ).fetchone()
    if existing is not None:
        raise ValidationError("%s already exists." % name)

    cursor = db.execute(
        "INSERT INTO categories (name, color) VALUES (?, ?)", (name, color)
    )
    db.commit()
    return cursor.lastrowid


def delete_category(category_id):
    """Delete a category.

    Tasks that used it are kept: the foreign key is ``ON DELETE SET NULL``, so
    they simply become uncategorised. Nothing you have written is lost.
    """
    db = get_db()
    cursor = db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    db.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="Task-Tracker database utility.")
    parser.add_argument(
        "--init",
        action="store_true",
        help="create the database file if missing and seed the default categories",
    )
    args = parser.parse_args(argv)
    if args.init:
        print("Initialised %s" % init_db())
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


