# Task Tracker

A small, self-hosted task list. Python on the server, a plain browser page for
the client. Built for a work machine where you cannot install third-party tools:
**no JavaScript, no Node, no npm, no build step, no external services.** The only
dependency is Flask (plus SQLite, which ships with Python).

The whole UI is one line where you type a task, and one expandable table of
everything you have added.

## Features

| # | Requirement | How it works |
|---|---|---|
| 1 | Tasks linked to a day | Each task carries a date ("Date added", defaulting to today). The date row is created automatically behind the scenes — there is no separate step. |
| 2 | Priority and category | Every task has Low/Medium/High priority (shown as a badge on the task) and a category, both editable when you expand the row. You can add your own categories, each with a colour dot. |
| 3 | To Do / In Progress / Done with colours | Three small buttons in the Status column move a task between states, so you can mark things complete straight from the table. Rows are coloured by status **and** labelled in words, so colour is never the only signal. |
| 4 | Banner of open work | A sticky dark banner at the top shows the status counts (To Do / In Progress / Done) and nothing else — individual tasks are never listed there, only in the table below. |
| 5 | Client-server via the browser | One Flask process serves the page and handles the form posts; you use it in any browser at `http://127.0.0.1:5000`. |
| 6 | Notes tab | A separate tab (`Tasks | Notes`) for quick notes: the **body is required** and gets a big writing box; the title is an optional heading. Notes have their **own categories** — independent of the task categories — managed through a collapsed "Note categories" dropdown on the same page. The list is newest first; each note can be **archived** into its own dropdown (restore or delete from there) or deleted via a confirmation page. |

## The table

| Column | Sortable | Notes |
|---|---|---|
| **Tick box** | no | The leftmost column: tick the rows you want gone, then press `Delete selected` above the table. |
| **Task** | yes | Click the title to expand the row: notes, edit form and delete. |
| **Notes** | yes | Open the **Notes** tab (`Tasks | Notes`): a big body box (required, below the optional title) and a category dropdown fed by the Notes tab's *own* categories — add or remove them via the collapsed "Note categories" dropdown. The list is newest first; each note row offers `archive` (moves it into the "Archived notes" dropdown, where you can `restore` or `delete` it) and `delete` (opens a confirmation page first).
| **Status** | yes | `To Do` / `In Progress` / `Done` buttons; the current one is pressed in. Sorting puts To Do first, Done last. |
| **Category** | yes | Colour dot and name, or `—` when uncategorised. |
| **Date added** | yes | The date the task belongs to. Defaults to today. |

Click any column header to sort by it. Click the same header again to reverse
the direction; the active column shows an arrow and is highlighted. Your sort is
kept as you add, edit, complete and delete tasks — every action returns you to
the table exactly as you left it. Uncategorised tasks sort to the end of the
Category column in ascending order, and to the front when reversed.

### Filtering

Under the **Status** and **Category** headers there is a `Filter ▾` dropdown
instead of a wall of links. Open it and tick as many values as you want to see
— `To Do` and `Done`, or `Work`, `Personal` and `Uncategorised` — then press
`Apply`; the table reloads showing only the matching tasks:
`?status=todo&status=done`, `?category=3&category=none`, or both columns at
once (`?status=todo&status=done&category=3`).

The two dropdowns combine, and both survive sorting, adding, editing and
moving tasks, because every link and form carries the current view with it. A
dropdown that is filtering flags itself (`Filter (2)`, filled in blue) and
ticks only the values it is showing; `Show all` inside it clears just that
column while keeping the other one and the sort. `Clear filters` — in the
summary line, or in the "No tasks match these filters" row — drops every
filter at once while keeping your sort order. Ticking *everything* counts as
"no filter", which is what an untouched dropdown submits. Because an untouched
menu arrives with every box ticked, filtering by a single value would
otherwise mean unticking the rest one by one — so each dropdown also carries a
`Deselect All` button: one click clears all of its boxes. It needs no
JavaScript either (it submits `?deselect=category` and the server re-renders
them unticked), keeps that menu open for your next tick, and leaves the other
column's filter and the sort untouched. When a filter
matches nothing the table still renders its headers, so the dropdowns are
always reachable and you can never get stuck. The table area keeps a minimum
height for the same reason: a fully-open menu always fits on screen, so you
never have to scroll to reach the last option.

The table itself is a `<details>` element, so the whole panel folds away with
the arrow next to "Tasks". Nothing behind that is JavaScript: sorting is an
ordinary link (`?sort=status&dir=desc`), filtering is a plain GET form
(`?status=todo&status=done`), expanding a row uses the browser's
built-in disclosure element, and bulk delete is one form post of the ticked
boxes (they join the `Delete selected` button's form through the plain HTML
`form` attribute).

## Quick start

```bash
git clone <your-repo-url>
cd Task-Tracker

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python3 app.py                     # then open http://127.0.0.1:5000
```

The `tasks.db` SQLite file is created automatically on first run, seeded with
four categories (Work, Personal, Errands, Admin).

## Using it

- **Add a task** — type in the box at the top and press `Add task`. The date
  defaults to today, so you can add and go. Change the priority, category or
  date before adding if you want.
- **Mark progress** — click `To Do`, `In Progress` or `Done` in the Status
  column. The banner counts and the row colours update immediately.
- **Filter the table** — open `Filter ▾` under the `Status` or `Category`
  header, tick the values you want to see (`To Do`, `Work`, `Uncategorised`,
  …) and press `Apply`. Tick several at once, combine both columns, and keep
  your sort while you do. `Deselect All` unticks a whole menu in one click
  (handy when it opens fully ticked), `Show all` in one dropdown clears just
  that column, and `Clear filters` clears everything.
- **See or change the details** — click the task title to expand the row and
  edit the title, priority, category, date or notes.
- **Delete a task** — expand the row and use `Delete`; it asks for confirmation
  on its own page first.
- **Delete several at once** — tick the boxes down the left of the rows you
  want gone and press `Delete selected` at the top of the table. Unlike a
  single delete there is no confirmation page: they go straight away, so tick
  carefully. With nothing ticked the button does nothing.
- **Categories** — open the `Categories` tab on the left of the table to see
  them, each with a count of how many tasks use it. Add one by typing a name
  and picking a colour. To remove one, click `delete` next to it: you get a
  confirmation page telling you how many tasks are affected, and those tasks
  are **kept** — they just become uncategorised.

### Status colours

| Status | Colour | Notes |
|---|---|---|
| To Do | blue-grey | default for new tasks |
| In Progress | amber | counted in the banner |
| Done | green | struck through, and counted as Done in the banner |

## Data model

Five tables in one SQLite file (`tasks.db`):

```
categories      (id, name UNIQUE, color, created_at)
note_categories (id, name UNIQUE, color, created_at)
days            (id, day_date UNIQUE, label, created_at)
items           (id, day_id -> days ON DELETE CASCADE, title, notes, priority,
                 category_id -> categories ON DELETE SET NULL, status,
                 created_at, updated_at)
notes           (id, title (optional), body (required),
                 category_id -> note_categories ON DELETE SET NULL,
                 archived (0 or 1), created_at, updated_at)
```

`categories` belongs to the Tasks tab and `note_categories` to the Notes tab;
they are separate tables on purpose, so a tag on the Notes tab never has to
mean anything on the task table (and vice versa). Databases written before
note categories existed are migrated automatically on startup: each note
keeps its category by name.

`days` still exists as the "Date added" value for a task — it is just no longer
shown as its own section. You never have to create one by hand: adding a task
creates its date row on demand, and every task on the same date shares it.

Enforced by the schema itself, so bad data cannot be stored even by mistake:

- `priority` may only be `Low`, `Medium` or `High`
- `status` may only be `todo`, `in_progress` or `done`
- `day_date` must look like `YYYY-MM-DD`, and `color` like `#rrggbb`
- titles must be 1–200 characters; notes up to 2000
- a note's body is required (up to 5000 characters); its title may be blank

Dates are stored as ISO text, which sorts chronologically with a plain string
comparison — which is why sorting the Date column is a database `ORDER BY` and
not a Python sort.

### Sorting and filtering safely

`?sort=` and `?dir=` from the URL are matched against a small whitelist of keys
(`task`, `status`, `category`, `date`) and never pasted into SQL, so a
hand-edited URL like `?sort=; DROP TABLE items;--` simply falls back to the
default ordering. There is a test for exactly that.

`?status=` and `?category=` are validated the same way before they reach a
`WHERE` clause (and bound as parameters): each repeated status must be one of
the three real values and each category must exist. Anything else — a typo, a
category you deleted yesterday, an injection attempt — is dropped, and ticking
*every* value counts as "no filter", so a hand-edited URL can only ever show
you *more* tasks, never break the page.

## Backups and exports

The live file is SQLite because that is what keeps writes transactional. When
you want a readable or diffable copy, use `export.py`:

```bash
python3 export.py --format json                    # nested days -> tasks
python3 export.py --format csv  --out items.csv    # one row per task (spreadsheet)
python3 export.py --format sql  --out backup.sql   # restorable dump

sqlite3 tasks.db < backup.sql                      # restore a dump
sqlite3 tasks.db .dump > backup.sql                # the same thing, no Python
```

Copying `tasks.db` while the app is running can miss recent writes, so prefer a
dump/export if the app is live. `tasks.db*` is gitignored — your tasks are not
code, so keep them out of the repo and move them with an export.

To inspect the database by hand: `sqlite3 tasks.db` (CLI) or
[DB Browser for SQLite](https://sqlitebrowser.org) (free GUI).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `127.0.0.1` | Interface to bind. Local-only by default — this app has no login, so keep it off the network unless you add auth. |
| `PORT` | `5000` | Port to listen on. |
| `DEBUG` | off | Set to `1` for the Flask debugger and auto-reload while developing. |
| `TASK_DB` | `tasks.db` next to `app.py` | Path to the SQLite file. |
| `SECRET_KEY` | random per start | Key for the flash-message session cookie. |

```bash
PORT=8080 TASK_DB=~/my-tasks.db python3 app.py
```

## Project layout

```
app.py                    Flask app: routes, sort headers, banner context
db.py                     all the SQL: schema, queries, sorting, validation
export.py                 JSON / CSV / SQL export
test_app.py               66 end-to-end tests (Flask test client)
requirements.txt          flask>=2.0  (that's the whole list)
templates/base.html       layout + the status banner (feature 4)
templates/index.html      quick-add bar + Categories tab + sortable table
templates/_filter_menu.html  one column's <details> filter dropdown (tick boxes)
templates/_task_row.html  one row: expandable details, status buttons, categories
templates/confirm.html    read-only "are you sure?" page for deletes
static/style.css          every colour and layout rule
```

## Why these choices

**No JavaScript at all.** Sorting is a link, expanding is `<details>`, and every
action is an ordinary form post followed by a redirect — even the bulk tick
boxes join their button's form through the plain HTML `form` attribute instead
of a line of script. There is nothing to build, nothing to transpile, and
nothing that can break on a machine without Node/npm. `test_app.py` asserts
that no served page contains `<script`, `onclick`, `onchange` or a
`javascript:` URL.

**Flask + Jinja2.** Jinja2 is a hard dependency of Flask (not an extra install),
it runs on the server and emits plain HTML, and its autoescaping means a title
like `Fish & Chips <run>` renders correctly instead of breaking the markup.

**SQLite instead of JSON/CSV/YAML.** Measured with 5,000 items:

| | JSON file | SQLite |
|---|---|---|
| Change one task's status | 24.8 ms, rewrites all 633 KB | <1 ms, updates one row |
| Interrupted write | file unparseable — **all tasks lost** | last committed change only |
| Two tabs / double submit | lost updates | writer locking |
| Invalid status or priority | silently stored | rejected by `CHECK` constraints |
| Sorting a column | Python sort in memory | `ORDER BY`, indexed |

Raw speed is not the point at this scale (25 ms is imperceptible) — durability
and constraints are. A truncated JSON rewrite loses your entire history, and
nothing stops a typo like `In Progress` becoming a phantom fourth status that
never gets coloured. SQLite is in the standard library, so it costs no
dependency to get those guarantees.

## Tests

```bash
python3 test_app.py            # or: python3 -m unittest -v test_app
```

66 tests covering quick-adding a task and its date, the four sortable columns
and their sort links, ascending/descending order for every column, clicking the
active header to flip direction, the sort surviving edits and redirects, a
malicious `?sort=` value being ignored, filtering by status and by category
(any number of ticked values per dropdown, both columns at once, a full
selection meaning "no filter", submitting the dropdown's form the way a
browser would, keeping the sort, staying on across edits and
redirects, junk values being ignored, clearing, and Deselect All emptying a
whole menu in one click while the other column's filter and its links stay
clean), bulk delete via the tick-box column (one box per row wired to the
`Delete selected` button with no JavaScript, removing only the ticked rows in
one click, keeping the sort and filters, ignoring junk ids, and doing nothing
when nothing is ticked), all three status
transitions from the
table, the banner showing status counts but never a list of tasks, editing
(including moving a task to a different date), validation of every field,
confirmation before deleting a task from its row *or a category*, deleting a
category without losing its tasks (they become
uncategorised), reuse of a deleted category's name, category duplicates and
colours, HTML escaping, the no-JavaScript guarantee, and all three export
formats. Each test runs against its own throwaway database, so your real tasks
are never touched.

## Troubleshooting

- **`Address already in use`** — something else is on port 5000
  (`ReviewHelper` uses it too). Run with `PORT=5001 python3 app.py`.
- **`ModuleNotFoundError: No module named 'flask'`** — the virtualenv is not
  active, or dependencies are not installed. Re-run `source .venv/bin/activate`
  and `pip install -r requirements.txt`.
- **`tasks.db-wal` / `tasks.db-shm` files appear** — normal for write-ahead
  logging; they are part of the database. Keep all three together, or use
  `export.py --format sql`.
- **The date field shows a different format** — that's your browser's locale;
  the value sent to the server is always `YYYY-MM-DD`.
- **A task seems to be missing** — check the sort. Completed tasks are still in
  the table, just coloured green; sorting by Date added descending keeps the
  newest at the top.

## Possible next steps

- A Priority column with its own sortable header. The priority is currently a
  badge on the task; adding a sort key to `db.SORT_KEYS` and an entry to
  `SORT_LABELS` is all it takes.
- A "carry unfinished tasks over to today" button.
- Simple authentication, if you ever want to reach it from another machine.

---

Licensed under the MIT License.
