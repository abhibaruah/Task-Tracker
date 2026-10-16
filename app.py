#!/usr/bin/env python3
"""Task-Tracker: a local task list you can host yourself.

One sortable table of every task: add on one line, click a column header to
re-order, click a task to expand it for editing.

Run it and open the browser::

    python3 app.py                  # http://127.0.0.1:5000

The front end is plain HTML rendered by Jinja2 on the server. There is no
JavaScript anywhere in this project and no build step, so it runs on a machine
that has nothing installed but Python and pip.

Configuration (all optional, via environment variables):

===============  ==========================================================
``HOST``         interface to bind, default ``127.0.0.1`` (local only)
``PORT``         port to listen on, default ``5000``
``DEBUG``        set to ``1`` for the Flask debugger/auto-reload
``TASK_DB``      path to the SQLite file, default ``tasks.db`` next to this file
``SECRET_KEY``   key for the flash-message session cookie
===============  ==========================================================
"""

import logging
import os
import secrets
from datetime import date

from flask import Flask, abort, flash, redirect, render_template, request, url_for

import db

logger = logging.getLogger("task-tracker")


def create_app():
    """Build the Flask app. Separate factory so tests can spin up their own."""
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
    db.init_app(app)

    # Make sure the schema exists before the first request is served.
    with app.app_context():
        db.init_db()

    @app.context_processor
    def inject_shared_data():
        """Everything the base template needs, on every page.

        Feature 4: the banner is rendered server-side on each request, so its
        counts can never drift out of sync with the data. It deliberately shows
        counts only -- the individual tasks live in the table, not here.
        """
        return {
            "banner_counts": db.banner_counts(),
            "categories": db.list_categories(),
            "priorities": db.PRIORITIES,
            "statuses": db.STATUSES,
            "status_labels": db.STATUS_LABELS,
            "today": date.today().isoformat(),
            "db_path": db.db_path(),
            # Which banner tab is lit up: any endpoint that mentions a note
            # (including note categories) counts as "notes"; everything else
            # counts as "tasks".
            "active_tab": "notes" if "note" in (request.endpoint or "") else "tasks",
            # Sort + active filters, for every link that should come back to
            # this exact view (form actions, confirm pages, delete links).
            "list_params": _list_params(),
        }

    # -----------------------------------------------------------------------
    # The task table (index)
    # -----------------------------------------------------------------------
    @app.route("/", methods=["GET"])
    def index():
        """One flat table of every task, ordered and filtered by the URL.

        ``?sort=``/``?dir=`` choose the order. ``?status=``/``?category=`` hide
        everything that does not match; each may repeat once per ticked box in
        that column's header dropdown. All of it is plain GET navigation, so
        filtering needs no JavaScript.
        """
        sort, direction = db.resolve_sort(
            request.args.get("sort", ""), request.args.get("dir", "")
        )
        status, category = _list_filters()
        status_menu, category_menu = _filter_menus(
            sort, direction, status, category, deselected=_deselected()
        )
        return render_template(
            "index.html",
            items=db.list_items_sorted(sort, direction, status, category),
            total=db.count_items(),
            columns=_columns(sort, direction),
            sort=sort,
            direction=direction,
            filter_menus={"status": status_menu, "category": category_menu},
            filter_labels=_filter_labels(status, category),
            clear_url=url_for("index", sort=sort, dir=direction),
        )

    # -----------------------------------------------------------------------
    # Items
    # -----------------------------------------------------------------------
    @app.route("/items", methods=["POST"])
    def add_item():
        """Quick add: type a task, and the date it belongs to is created on demand."""
        day_date = request.form.get("day_date", "") or date.today().isoformat()
        day_id = db.ensure_day(day_date)
        title = request.form.get("title", "").strip()
        db.add_item(
            day_id,
            title,
            request.form.get("priority", "Medium"),
            request.form.get("category_id"),
            "",
        )
        flash("Added: %s" % title, "ok")
        return _see_other(_list_url())

    @app.route("/items/<int:item_id>/status", methods=["POST"])
    def set_status(item_id):
        """Feature 3: mark a task To Do / In Progress / Done from the table."""
        status = db.set_status(item_id, request.form.get("status", ""))
        flash("Moved to %s." % db.STATUS_LABELS[status], "ok")
        return _see_other(_list_url())

    @app.route("/items/<int:item_id>/update", methods=["POST"])
    def update_item(item_id):
        """Edit title, priority, category, notes or date from the expanded row."""
        db.update_item(
            item_id,
            request.form.get("title", ""),
            request.form.get("priority", "Medium"),
            request.form.get("category_id"),
            request.form.get("notes", ""),
            request.form.get("day_date") or None,
        )
        flash("Saved.", "ok")
        return _see_other(_list_url())

    @app.route("/items/<int:item_id>/delete", methods=["GET"])
    def confirm_delete_item(item_id):
        item = db.get_item(item_id) or abort(404)
        return render_template(
            "confirm.html",
            heading="Delete this task?",
            question="This cannot be undone.",
            action=url_for("delete_item", item_id=item_id, **_list_params()),
            cancel_url=_list_url(),
            items=[item],
            danger=True,
        )

    @app.route("/items/<int:item_id>/delete", methods=["POST"])
    def delete_item(item_id):
        item = db.get_item(item_id) or abort(404)
        db.delete_item(item_id)
        flash("Deleted: %s" % item["title"], "ok")
        return _see_other(_list_url())

    @app.route("/items/delete", methods=["POST"])
    def delete_items():
        """Bulk delete: the tick boxes down the left of the table.

        The boxes belong to the toolbar form at the top of the table (joined
        with the HTML5 ``form`` attribute, because every row already holds
        its own status and edit forms), so this post carries one ``id=`` per
        ticked row. Sort and filters ride in the action's query string --
        exactly like ``add_item`` -- so the redirect drops you back on the
        view you deleted from. Nothing ticked is a harmless no-op; there is
        deliberately no confirmation page, so the flash reports what went.
        """
        items = _selected_items()
        if not items:
            flash("Nothing selected to delete.", "error")
            return _see_other(_list_url())
        db.delete_items([item["id"] for item in items])
        if len(items) == 1:
            flash("Deleted: %s" % items[0]["title"], "ok")
        else:
            flash("Deleted %d task(s)." % len(items), "ok")
        return _see_other(_list_url())

    # -----------------------------------------------------------------------
    # Categories
    # -----------------------------------------------------------------------
    @app.route("/categories", methods=["POST"])
    def create_category():
        db.create_category(
            request.form.get("name", ""),
            request.form.get("color", db.DEFAULT_COLOR),
        )
        flash("Added category %s." % request.form.get("name", "").strip(), "ok")
        return _see_other(_list_url())

    @app.route("/categories/<int:category_id>/delete", methods=["GET"])
    def confirm_delete_category(category_id):
        category = db.get_category(category_id) or abort(404)
        if category["usage"]:
            question = (
                "%d task(s) currently use this category. They are kept, and "
                "simply become uncategorised." % category["usage"]
            )
        else:
            question = "No tasks use this category."
        return render_template(
            "confirm.html",
            heading="Delete category %s?" % category["name"],
            question=question,
            action=url_for("delete_category", category_id=category_id, **_list_params()),
            cancel_url=_list_url(),
            items=[],
            danger=True,
        )

    @app.route("/categories/<int:category_id>/delete", methods=["POST"])
    def delete_category(category_id):
        category = db.get_category(category_id) or abort(404)
        db.delete_category(category_id)
        flash(
            "Deleted category %s. Its %d task(s) are now uncategorised."
            % (category["name"], category["usage"]),
            "ok",
        )
        return _see_other(_list_url())

    # -----------------------------------------------------------------------
    # The Notes tab
    # -----------------------------------------------------------------------
    @app.route("/notes", methods=["GET"])
    def list_notes():
        """Standalone notes, newest first, with a create form on top."""
        return render_template(
            "notes.html",
            notes=db.list_notes(),
            archived=db.list_notes(archived=True),
            note_categories=db.list_note_categories(),
        )

    @app.route("/notes", methods=["POST"])
    def create_note():
        """Add one note: a required body plus an optional title and category."""
        title = request.form.get("title", "")
        body = request.form.get("body", "")
        db.create_note(
            title,
            body,
            request.form.get("category_id"),
        )
        # The title is optional, so name the note the way the list will.
        flash("Added note %s." % db.note_label(title, body), "ok")
        return _see_other(url_for("list_notes"))

    @app.route("/notes/<int:note_id>/delete", methods=["GET"])
    def confirm_delete_note(note_id):
        note = db.get_note(note_id) or abort(404)
        return render_template(
            "confirm.html",
            heading="Delete note %s?" % note["label"],
            question="This cannot be undone.",
            action=url_for("delete_note", note_id=note_id),
            cancel_url=url_for("list_notes"),
            items=[],
            danger=True,
        )

    @app.route("/notes/<int:note_id>/delete", methods=["POST"])
    def delete_note(note_id):
        note = db.get_note(note_id) or abort(404)
        db.delete_note(note_id)
        flash("Deleted note %s." % note["label"], "ok")
        return _see_other(url_for("list_notes"))

    # Archiving is a flag, not a destructive step, so it needs no confirmation
    # page -- one POST and the note slides into (or out of) the Archive
    # dropdown. Delete keeps its confirm flow and works from either list.
    @app.route("/notes/<int:note_id>/archive", methods=["POST"])
    def archive_note(note_id):
        note = db.get_note(note_id) or abort(404)
        db.set_note_archived(note_id, True)
        flash("Archived note %s." % note["label"], "ok")
        return _see_other(url_for("list_notes"))

    @app.route("/notes/<int:note_id>/unarchive", methods=["POST"])
    def unarchive_note(note_id):
        note = db.get_note(note_id) or abort(404)
        db.set_note_archived(note_id, False)
        flash("Restored note %s from the archive." % note["label"], "ok")
        return _see_other(url_for("list_notes"))

    # Note categories live on the Notes tab and are independent of the task
    # categories: they have their own table, their own routes, and they never
    # appear in the Tasks tab's sidebar.
    @app.route("/notes/categories", methods=["POST"])
    def create_note_category():
        db.create_note_category(
            request.form.get("name", ""),
            request.form.get("color", db.DEFAULT_COLOR),
        )
        flash("Added category %s." % request.form.get("name", "").strip(), "ok")
        return _see_other(url_for("list_notes"))

    @app.route("/notes/categories/<int:category_id>/delete", methods=["GET"])
    def confirm_delete_note_category(category_id):
        category = db.get_note_category(category_id) or abort(404)
        if category["usage"]:
            question = (
                "%d note(s) currently use this category. They are kept, and "
                "simply become uncategorised." % category["usage"]
            )
        else:
            question = "No notes use this category."
        return render_template(
            "confirm.html",
            heading="Delete category %s?" % category["name"],
            question=question,
            action=url_for("delete_note_category", category_id=category_id),
            cancel_url=url_for("list_notes"),
            items=[],
            danger=True,
        )

    @app.route("/notes/categories/<int:category_id>/delete", methods=["POST"])
    def delete_note_category(category_id):
        category = db.get_note_category(category_id) or abort(404)
        db.delete_note_category(category_id)
        flash(
            "Deleted category %s. Its %d note(s) are now uncategorised."
            % (category["name"], category["usage"]),
            "ok",
        )
        return _see_other(url_for("list_notes"))

    @app.errorhandler(db.ValidationError)
    def handle_validation_error(exc):
        """Bad input never reaches SQLite: flash the reason and go back."""
        flash(str(exc), "error")
        # A bad note (empty title, over-long body, ...) belongs to the Notes
        # tab, so send it back there; everything else belongs to the table.
        if request.path.startswith("/notes") and request.method == "POST":
            return _see_other(url_for("list_notes"))
        return _see_other(_list_url())

    return app


# ---------------------------------------------------------------------------
# Redirect and header helpers. Actions post and then redirect, so the browser
# lands back on the table with the sort you were using still applied.
# ---------------------------------------------------------------------------
def _see_other(url):
    """Post/Redirect/Get with the correct status code.

    303 (not the 302 that ``redirect()`` defaults to) tells the client to follow
    up with a GET. A 302 leaves it ambiguous, so some clients repeat the POST
    against the redirect target and get a 405.
    """
    return redirect(url, code=303)


def _list_sort():
    """The sort column to come back to, from the form or the query string."""
    return request.form.get("sort") or request.args.get("sort") or ""


def _list_dir():
    return request.form.get("dir") or request.args.get("dir") or ""


def _list_url():
    """Back to the list, keeping the sort *and* the active filters."""
    return url_for("index", **_list_params())


def _list_sort_dir():
    """The sort state, resolved to concrete values (never empty strings)."""
    return db.resolve_sort(_list_sort(), _list_dir())


def _deselected():
    """Which dropdown the user just pressed "Deselect All" in, if any.

    There is no JavaScript to untick a whole menu in place, so that button
    submits ``?deselect=status`` / ``?deselect=category`` instead. The server
    then ignores that column's ticked values and re-renders its boxes cleared.
    Any other value is ignored, so a hand-edited URL can only widen the view.
    """
    value = request.args.get("deselect", "")
    return value if value in ("status", "category") else None


def _list_filters():
    """The active filters, from the query string only.

    Never from form fields: the edit and status forms post their own
    ``status`` value, and that must never be mistaken for a filter. Each
    parameter may repeat once per ticked box in a dropdown, so read it with
    ``getlist``; ``db.resolve_filters`` drops anything that is not a real
    status or a real category.

    "Deselect All" (``?deselect=<column>``) beats whatever that column still
    sent along with it, so the menu clears while every link built from here --
    sort headers, the other dropdown's hidden fields, confirm pages -- keeps
    the rest of the view exactly as it was.
    """
    deselect = _deselected()
    status = [] if deselect == "status" else request.args.getlist("status")
    category = [] if deselect == "category" else request.args.getlist("category")
    return db.resolve_filters(status, category)


def _list_params():
    """Sort and filters as one dict, ready for ``url_for``.

    Every link and form action that should come back to the view you are
    looking at is built from this, so neither the order nor the filters are
    lost when you add, edit, move or delete something.
    """
    sort, direction = _list_sort_dir()
    status, category = _list_filters()
    params = {"sort": sort, "dir": direction}
    if status:
        params["status"] = status          # a list repeats ?status= per value
    if category:
        params["category"] = category      # (url_for expands list values)
    return params


def _selected_ids():
    """The ticked row boxes from the bulk-delete post, as unique ids.

    Each ticked box repeats ``id=`` in the form body. Anything that is not a
    plain positive number (a hand-edited request) is dropped before it is
    looked up, and duplicates collapse, so a row can only be asked for once.
    They arrive in submission order, which is the order the rows appear in.
    """
    ids = []
    for raw in request.form.getlist("id"):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in ids:
            ids.append(value)
    return ids


def _selected_items():
    """The still-existing tasks behind the ticked boxes, in table order.

    A row deleted from another tab since the page was rendered quietly drops
    out here, so a stale id can never make the delete fail.
    """
    return [item for item in (db.get_item(i) for i in _selected_ids()) if item]


def _filter_menus(sort, direction, status, category, deselected=None):
    """The filter dropdowns rendered under the Status and Category headers.

    Each menu is a ``<details>`` holding a GET form of tick boxes: ticked
    means "show me these". While a column's filter is off every box is ticked,
    so applying the form unchanged leaves the view alone (and
    ``db.resolve_filters`` reads a full selection as "no filter"). Each form
    carries the sort and the *other* column's filter as hidden inputs, so
    ticking boxes in one dropdown never disturbs the rest of the view, and
    each "Show all" link clears its own column only.

    ``deselected`` is the column whose "Deselect All" button was just pressed.
    It clears that menu's filter like "Show all" does, but renders every one of
    its boxes *unticked* instead of the usual all-ticked default -- so you can
    then tick just the one value you actually want rather than unticking all
    the others by hand. That menu also stays open for the next click.
    """
    status_off = status is None
    category_off = category is None
    # A just-deselected column is unfiltered (shows everything) yet renders
    # with its boxes cleared; an untouched column keeps its all-ticked look.
    status_clear = deselected == "status"
    category_clear = deselected == "category"

    status_options = [
        {"key": key, "label": db.STATUS_LABELS[key], "color": None,
         "checked": (not status_clear) and (status_off or key in status)}
        for key in db.STATUSES
    ]
    category_options = [
        {"key": str(entry["id"]), "label": entry["name"],
         "color": entry["color"],
         "checked": (not category_clear) and (category_off or entry["id"] in category)}
        for entry in db.list_categories()
    ]
    category_options.append(
        {"key": "none", "label": "Uncategorised", "color": None,
         "checked": (not category_clear) and (category_off or "none" in category)}
    )

    def menu(name, legend, options, filtered, other_key, other_values):
        hidden = [("sort", sort), ("dir", direction)]
        hidden += [(other_key, value) for value in (other_values or [])]
        keep = {"sort": sort, "dir": direction}
        if other_values:
            keep[other_key] = other_values
        return {
            "name": name,
            "legend": legend,
            "hint": "All ticked (or none ticked) shows every task.",
            "options": options,
            "hidden": hidden,
            "filtered": filtered,
            "count": sum(option["checked"] for option in options) if filtered else 0,
            "deselected": deselected == name,
            "show_all": url_for("index", **keep),
            "show_all_title": "Show every %s again" % name,
        }

    return (
        menu("status", "Show these statuses", status_options, not status_off,
             "category", category),
        menu("category", "Show these categories", category_options,
             not category_off, "status", status),
    )


def _filter_labels(status, category):
    """Human names of the active filters, for the "filtered by ..." summary."""
    labels = [db.STATUS_LABELS[value] for value in (status or [])]
    names = []
    for value in category or []:
        if value == "none":
            names.append("Uncategorised")
        else:
            found = db.get_category(value)
            if found:
                names.append(found["name"])
    if len(names) > 3:
        # A long category selection would drown the summary line; the dropdown
        # itself shows exactly which boxes are ticked.
        labels.append("%d categories" % len(names))
    else:
        labels.extend(names)
    return labels


def _columns(sort, direction):
    """Header cells for the task table: label, sort link and current state.

    The sort links keep the active filters, so re-ordering a filtered list does
    not silently drop the filter.
    """
    columns = []
    for key, label in db.SORT_LABELS:
        active = key == sort
        if active:
            # Clicking the column you are already sorted by flips the direction.
            next_direction = "asc" if direction == "desc" else "desc"
        else:
            next_direction = db.DEFAULT_DIRECTION.get(key, "asc")
        params = _list_params()
        params.update(sort=key, dir=next_direction)
        columns.append(
            {
                "key": key,
                "label": label,
                "active": active,
                # aria-sort only accepts "ascending"/"descending"/"none".
                "aria": ("descending" if direction == "desc" else "ascending") if active else "none",
                "arrow": ("\u25bc" if direction == "desc" else "\u25b2") if active else "",
                "href": url_for("index", **params),
                "hint": "%s order" % ("descending" if next_direction == "desc" else "ascending"),
            }
        )
    return columns


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
    logger.info("Task-Tracker on http://%s:%s (database: %s)", host, port, db.db_path())
    app.run(host=host, port=port, debug=debug, threaded=True)
