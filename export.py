#!/usr/bin/env python3
"""Export the Task-Tracker database for reading, diffing or backup.

The live store stays SQLite, because that is what keeps it transactional and
constraint-checked. This script gives you the human-readable view on demand:

    python3 export.py --format json                    # nested days + items
    python3 export.py --format csv  --out items.csv    # one flat row per item
    python3 export.py --format sql  --out backup.sql   # restorable dump

Restore a SQL dump with::

    sqlite3 tasks.db < backup.sql

Point it at another database with the ``TASK_DB`` environment variable.
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone

import db

CSV_FIELDS = (
    "day_date",
    "day_label",
    "title",
    "notes",
    "priority",
    "category",
    "status",
    "created_at",
    "updated_at",
)

ITEM_ORDER_SQL = """
ORDER BY CASE i.status WHEN 'todo' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
         CASE i.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END,
         i.id
"""


def build_document():
    """The whole database as plain dicts, ready for json.dumps()."""
    conn = db.connect()
    try:
        categories = [
            dict(row)
            for row in conn.execute("SELECT id, name, color FROM categories ORDER BY name")
        ]
        days = []
        for row in conn.execute(
            "SELECT id, day_date, label, created_at FROM days ORDER BY day_date"
        ):
            items = [
                dict(item)
                for item in conn.execute(
                    """
                    SELECT i.id, i.title, i.notes, i.priority, i.status,
                           c.name AS category, c.color AS category_color,
                           i.created_at, i.updated_at
                    FROM items i
                    LEFT JOIN categories c ON c.id = i.category_id
                    WHERE i.day_id = ?
                    """ + ITEM_ORDER_SQL,
                    (row["id"],),
                )
            ]
            days.append(
                {
                    "day_date": row["day_date"],
                    "label": row["label"],
                    "created_at": row["created_at"],
                    "items": items,
                }
            )
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "database": os.path.abspath(db.db_path()),
            "categories": categories,
            "days": days,
        }
    finally:
        conn.close()


def to_json():
    return json.dumps(build_document(), indent=2, ensure_ascii=False) + "\n"


def to_csv():
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for day in build_document()["days"]:
        for item in day["items"]:
            writer.writerow(
                dict(item, day_date=day["day_date"], day_label=day["label"])
            )
    return buffer.getvalue()


def to_sql():
    """A restorable SQL dump, using the stdlib's own iterdump()."""
    conn = db.connect()
    try:
        return "\n".join(conn.iterdump()) + "\n"
    finally:
        conn.close()


FORMATS = {"json": to_json, "csv": to_csv, "sql": to_sql}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Export Task-Tracker data to JSON, CSV or SQL."
    )
    parser.add_argument(
        "--format", choices=sorted(FORMATS), default="json", help="output format"
    )
    parser.add_argument(
        "--out", help="write to this file instead of standard output"
    )
    args = parser.parse_args(argv)

    text = FORMATS[args.format]()
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        print("Wrote %s (%d bytes)" % (args.out, len(text)), file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
