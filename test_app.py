#!/usr/bin/env python3
"""End-to-end tests for Task-Tracker, using Flask's test client.

Run with::

    python3 test_app.py          # or: python3 -m unittest -v test_app

Every test gets its own throwaway SQLite file (via the ``TASK_DB`` env var), so
running the suite never touches your real task list.
"""

import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from urllib.parse import urlencode

# Point the app at a scratch database *before* importing it, so the module-level
# app instance created on import does not touch the real tasks.db.
_TMP_ROOT = tempfile.mkdtemp(prefix="task-tracker-tests-")
os.environ["TASK_DB"] = os.path.join(_TMP_ROOT, "bootstrap.db")
os.environ["SECRET_KEY"] = "test-key"

import app as app_module  # noqa: E402
import db  # noqa: E402
import export  # noqa: E402

DAY = "2026-09-29"


class TaskTrackerTestCase(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(_TMP_ROOT, "test-%s.db" % self._testMethodName)
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.db_path + suffix):
                os.remove(self.db_path + suffix)
        os.environ["TASK_DB"] = self.db_path

        self.app = app_module.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ["TASK_DB"] = os.path.join(_TMP_ROOT, "bootstrap.db")

    # -- helpers ------------------------------------------------------------
    def add_task(self, title="Write the report", day_date=DAY, priority="High",
                 category_id="", follow=True):
        """Quick add. No day has to exist beforehand -- that is the point."""
        return self.client.post(
            "/items",
            data={
                "title": title,
                "day_date": day_date,
                "priority": priority,
                "category_id": category_id,
            },
            follow_redirects=follow,
        )

    def page(self, **params):
        return self.client.get("/", query_string=params).get_data(as_text=True)

    def table(self, **params):
        """Just the <tbody> of the task table, so assertions ignore the banner."""
        full = self.page(**params)
        self.assertIn("<tbody>", full)
        return full.split("<tbody>")[1].split("</tbody>")[0]

    def tasks(self, sort="date", direction="asc"):
        with self.app.app_context():
            return db.list_items_sorted(sort, direction)

    def task_id(self, title):
        with self.app.app_context():
            for item in db.list_items_sorted("task", "asc"):
                if item["title"] == title:
                    return item["id"]
        raise AssertionError("no such task: %s" % title)

    def category_id(self, name):
        with self.app.app_context():
            for category in db.list_categories():
                if category["name"] == name:
                    return category["id"]
        return None

    def note_category_id(self, name):
        """The id of a *Notes-tab* category (their own table, not the task one)."""
        with self.app.app_context():
            for category in db.list_note_categories():
                if category["name"] == name:
                    return category["id"]
        return None

    def assert_order(self, *titles, **params):
        """Assert these titles appear in this order inside the table body."""
        body = self.table(**params)
        positions = [body.index(title) for title in titles]
        self.assertEqual(positions, sorted(positions),
                         "%r are not in order in the table" % (titles,))

    # -- quick add (features 1 + 2) -----------------------------------------
    def test_empty_state_on_first_run(self):
        body = self.page()
        self.assertIn("No tasks yet", body)
        self.assertIn("Nothing to do and nothing in progress", body)

    def test_default_categories_are_seeded(self):
        with self.app.app_context():
            names = [c["name"] for c in db.list_categories()]
        self.assertEqual(names, ["Admin", "Errands", "Personal", "Work"])

    def test_quick_add_creates_the_task_and_its_date_on_demand(self):
        with self.app.app_context():
            self.assertEqual(db.list_days(), [])          # nothing exists yet

        response = self.add_task("Write the report", priority="High",
                                 category_id=self.category_id("Work"))
        self.assertEqual(response.status_code, 200)

        stored = self.tasks()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["title"], "Write the report")
        self.assertEqual(stored[0]["priority"], "High")
        self.assertEqual(stored[0]["category"], "Work")
        self.assertEqual(stored[0]["day_date"], DAY)
        self.assertEqual(stored[0]["status"], "todo")
        # The date row was created behind the scenes, with no separate step.
        with self.app.app_context():
            self.assertEqual([d["day_date"] for d in db.list_days()], [DAY])

    def test_two_tasks_on_the_same_date_share_one_day_row(self):
        self.add_task("First")
        self.add_task("Second")
        with self.app.app_context():
            self.assertEqual(len(db.list_days()), 1)
            self.assertEqual(len(db.list_items_sorted()), 2)

    def test_date_defaults_to_today_when_omitted(self):
        from datetime import date
        self.client.post("/items", data={"title": "No date given", "priority": "Low"},
                         follow_redirects=True)
        self.assertEqual(self.tasks()[0]["day_date"], date.today().isoformat())

    def test_table_has_the_four_columns_each_with_a_sort_link(self):
        self.add_task()
        body = self.page()
        for label in ("Task", "Status", "Category", "Date added"):
            self.assertIn(label, body)
        for column in ("task", "status", "category", "date"):
            self.assertIn("sort=%s&amp;dir=" % column, body)

    def test_priority_and_category_are_shown_in_the_row(self):
        self.add_task("Pay the invoice", priority="Low",
                      category_id=self.category_id("Admin"))
        body = self.table()
        self.assertIn("Pay the invoice", body)
        self.assertIn("priority-low", body)
        self.assertIn("Admin", body)

    def test_uncategorised_task_is_marked_as_such(self):
        self.add_task("Something vague", category_id="")
        self.assertIsNone(self.tasks()[0]["category_id"])
        self.assertIn("&mdash;", self.table())

    # -- sorting by clicking the column headers ------------------------------
    def test_default_order_is_newest_date_first(self):
        self.add_task("Middle", day_date="2026-09-29")
        self.add_task("Newest", day_date="2026-10-01")
        self.add_task("Oldest", day_date="2026-09-01")
        self.assert_order("Newest", "Middle", "Oldest")           # no query string
        body = self.page()
        self.assertIn('aria-sort="descending"', body)             # Date added is active

    def test_sort_by_task_ascending_and_descending(self):
        for title in ("Bravo", "Alpha", "Charlie"):
            self.add_task(title)
        self.assert_order("Alpha", "Bravo", "Charlie", sort="task", dir="asc")
        self.assert_order("Charlie", "Bravo", "Alpha", sort="task", dir="desc")

    def test_sort_by_status_puts_open_work_first(self):
        for title in ("Alpha", "Bravo", "Charlie"):
            self.add_task(title)
        self.client.post("/items/%d/status" % self.task_id("Bravo"),
                         data={"status": "in_progress"}, follow_redirects=True)
        self.client.post("/items/%d/status" % self.task_id("Charlie"),
                         data={"status": "done"}, follow_redirects=True)
        self.assert_order("Alpha", "Bravo", "Charlie", sort="status", dir="asc")
        self.assert_order("Charlie", "Bravo", "Alpha", sort="status", dir="desc")

    def test_sort_by_category_alphabetically_with_none_last(self):
        self.add_task("Zeta", category_id=self.category_id("Work"))
        self.add_task("Beta", category_id=self.category_id("Personal"))
        self.add_task("Gamma", category_id="")
        self.assert_order("Beta", "Zeta", "Gamma", sort="category", dir="asc")
        self.assert_order("Gamma", "Zeta", "Beta", sort="category", dir="desc")

    def test_sort_by_date_ascending(self):
        self.add_task("Middle", day_date="2026-09-29")
        self.add_task("Newest", day_date="2026-10-01")
        self.add_task("Oldest", day_date="2026-09-01")
        self.assert_order("Oldest", "Middle", "Newest", sort="date", dir="asc")

    def test_clicking_the_active_header_offers_the_opposite_direction(self):
        self.add_task()
        # Default state: sorted by date, descending -> its link should offer asc.
        body = self.page()
        self.assertIn('aria-sort="descending"', body)
        self.assertIn("sort=date&amp;dir=asc", body)
        self.assertIn('aria-sort="none"', body)
        # Sorted by task ascending -> that link should now offer desc.
        body = self.page(sort="task", dir="asc")
        self.assertIn('aria-sort="ascending"', body)
        self.assertIn("sort=task&amp;dir=desc", body)

    def test_rows_stay_clickable_for_each_status(self):
        self.add_task("Only task")
        body = self.page()
        self.assertIn('class="task-row status-todo"', body)
        self.assertIn('id="task-%d"' % self.task_id("Only task"), body)

    def test_a_junk_sort_parameter_is_ignored_not_executed(self):
        self.add_task("Still here")
        body = self.page(sort="; DROP TABLE items;--", dir="sideways")
        self.assertIn("Still here", body)                 # table still rendered
        self.assertEqual(len(self.tasks()), 1)            # and the data is intact
        self.assertIn('aria-sort="descending"', body)     # fell back to the default

    def test_sort_is_remembered_when_a_task_is_updated(self):
        self.add_task("Alpha")
        self.add_task("Bravo")
        page = self.client.post(
            "/items/%d/status" % self.task_id("Alpha"),
            data={"status": "in_progress", "sort": "task", "dir": "desc"},
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn('aria-sort="descending"', page)
        body = page.split("<tbody>")[1]
        self.assertLess(body.index("Bravo"), body.index("Alpha"))   # desc still applied

    # -- filtering by the Status and Category headers -------------------------
    @staticmethod
    def find_href(body, needle):
        """The first link on the page whose href contains ``needle``."""
        for href in re.findall(r'href="([^"]+)"', body):
            if needle in href:
                return href.replace("&amp;", "&")
        raise AssertionError("no link whose href contains %r" % needle)

    @staticmethod
    def clear_href(body):
        """The href of the first "Clear filters" link on the page."""
        match = re.search(r'class="clear-filters" href="([^"]+)"', body)
        if not match:
            raise AssertionError("no Clear filters link")
        return match.group(1).replace("&amp;", "&")

    def three_statuses(self):
        """One task in each status, so a status filter has something to hide."""
        self.add_task("Todo task")
        self.add_task("Doing task")
        self.add_task("Finished task")
        self.client.post("/items/%d/status" % self.task_id("Doing task"),
                         data={"status": "in_progress"}, follow_redirects=True)
        self.client.post("/items/%d/status" % self.task_id("Finished task"),
                         data={"status": "done"}, follow_redirects=True)

    def test_the_headers_offer_a_filter_per_status_and_per_category(self):
        self.add_task("One", category_id=self.category_id("Work"))
        body = self.page()
        # One <details> dropdown per filterable column (Status and Category)...
        self.assertEqual(body.count('class="filter-menu'), 2)
        # ...and every value is a tick box inside it. With no filter set every
        # box is ticked, because ticked means "show me these".
        for label in ("To Do", "In Progress", "Done"):
            self.assertIn("%s</label>" % label, body)
        for label in ("Admin", "Errands", "Personal", "Work", "Uncategorised"):
            self.assertIn("%s</label>" % label, body)
        for value in ("todo", "in_progress", "done", "none"):
            self.assertIn('value="%s" checked' % value, body)
        self.assertIn('value="%d" checked' % self.category_id("Work"), body)

    def test_filtering_by_status_shows_only_matching_tasks(self):
        self.three_statuses()
        todo = self.table(status="todo")
        self.assertIn("Todo task", todo)
        self.assertNotIn("Doing task", todo)
        self.assertNotIn("Finished task", todo)

        doing = self.table(status="in_progress")
        self.assertIn("Doing task", doing)
        self.assertNotIn("Todo task", doing)
        self.assertNotIn("Finished task", doing)

        done = self.table(status="done")
        self.assertIn("Finished task", done)
        self.assertNotIn("Todo task", done)
        self.assertNotIn("Doing task", done)

        everything = self.table()                      # no filter = nothing hidden
        for title in ("Todo task", "Doing task", "Finished task"):
            self.assertIn(title, everything)

    def test_filtering_by_category_shows_only_matching_tasks(self):
        self.add_task("Work task", category_id=self.category_id("Work"))
        self.add_task("Personal task", category_id=self.category_id("Personal"))
        self.add_task("Loose task")

        work = self.table(category=self.category_id("Work"))
        self.assertIn("Work task", work)
        self.assertNotIn("Personal task", work)
        self.assertNotIn("Loose task", work)

        loose = self.table(category="none")            # uncategorised tasks
        self.assertIn("Loose task", loose)
        self.assertNotIn("Work task", loose)
        self.assertNotIn("Personal task", loose)

    def test_status_and_category_filters_combine(self):
        work = self.category_id("Work")
        self.add_task("Work todo", category_id=work)
        self.add_task("Work done", category_id=work)
        self.add_task("Elsewhere todo", category_id=self.category_id("Personal"))
        self.client.post("/items/%d/status" % self.task_id("Work done"),
                         data={"status": "done"}, follow_redirects=True)

        both = self.table(status="todo", category=work)
        self.assertIn("Work todo", both)
        self.assertNotIn("Work done", both)            # right category, wrong status
        self.assertNotIn("Elsewhere todo", both)       # right status, wrong category

    def test_several_statuses_can_be_selected_at_once(self):
        self.three_statuses()
        both = self.table(status=["todo", "done"])
        self.assertIn("Todo task", both)
        self.assertIn("Finished task", both)
        self.assertNotIn("Doing task", both)

    def test_several_categories_can_be_selected_at_once(self):
        work = self.category_id("Work")
        self.add_task("Work task", category_id=work)
        self.add_task("Personal task", category_id=self.category_id("Personal"))
        self.add_task("Loose task")

        picked = self.table(category=[work, "none"])
        self.assertIn("Work task", picked)
        self.assertIn("Loose task", picked)
        self.assertNotIn("Personal task", picked)

    def test_selecting_every_value_is_the_same_as_no_filter(self):
        self.three_statuses()
        # Every status ticked in the dropdown = no filter: nothing hidden and
        # no "filtered by" note, exactly as if the dropdown had never been
        # touched.
        page = self.page(status=["todo", "in_progress", "done"])
        for title in ("Todo task", "Doing task", "Finished task"):
            self.assertIn(title, page)
        self.assertNotIn("filtered by", page)
        self.assertIn("3 total", page)
        # Same for every category plus Uncategorised.
        with self.app.app_context():
            everything = [entry["id"] for entry in db.list_categories()]
        everything.append("none")
        page = self.page(category=everything)
        self.assertNotIn("filtered by", page)
        self.assertIn("3 total", page)

    def test_the_dropdown_form_submits_exactly_like_a_browser(self):
        self.three_statuses()
        body = self.page(status="todo")
        # Pull the status dropdown's GET form out of the rendered page and
        # submit it the way a browser would: hidden fields first, then only the
        # ticked checkboxes.
        forms = re.findall(r'<form class="filter-form" method="get".*?</form>',
                           body, re.S)
        self.assertEqual(len(forms), 2)
        status_form = next(f for f in forms if 'name="status"' in f)
        pairs = []
        for tag in re.findall(r"<input[^>]+>", status_form):
            if 'type="hidden"' not in tag and 'type="checkbox"' not in tag:
                continue
            if 'type="checkbox"' in tag and " checked" not in tag:
                continue                      # unticked boxes are not sent
            name = re.search(r'name="([^"]+)"', tag).group(1)
            pairs.append((name, re.search(r'value="([^"]*)"', tag).group(1)))
        submitted = self.client.get("/?" + urlencode(pairs)).get_data(as_text=True)
        self.assertIn("showing 1 of 3", submitted)
        self.assertIn("Todo task", submitted)
        self.assertNotIn("Doing task", submitted)

        # Unticking everything and submitting leaves the view unfiltered.
        pairs = [(key, value) for key, value in pairs if key != "status"]
        submitted = self.client.get("/?" + urlencode(pairs)).get_data(as_text=True)
        self.assertNotIn("filtered by", submitted)
        self.assertIn("3 total", submitted)

    def test_sorting_keeps_the_filters_and_filtering_keeps_the_sort(self):
        work = self.category_id("Work")
        self.add_task("Bravo", category_id=work)
        self.add_task("Alpha", category_id=work)
        body = self.page(status="todo", category=work, sort="task", dir="asc")
        # Every sort header link carries both filters...
        self.assertIn("sort=task&amp;dir=desc&amp;status=todo&amp;category=%d" % work, body)
        self.assertIn("sort=date&amp;dir=desc&amp;status=todo&amp;category=%d" % work, body)
        # ...and each dropdown form carries the sort plus the *other* column's
        # filter as hidden inputs, so applying one cannot disturb the view.
        self.assertIn('type="hidden" name="sort" value="task"', body)
        self.assertIn('type="hidden" name="dir" value="asc"', body)
        self.assertIn('type="hidden" name="category" value="%d"' % work, body)
        self.assertIn('type="hidden" name="status" value="todo"', body)
        # Each "Show all" clears only its own column: the other filter stays.
        show_alls = re.findall(r'class="show-all" href="([^"]+)"', body)
        self.assertEqual(len(show_alls), 2)             # one per dropdown
        self.assertTrue(any("category=" in h and "status=" not in h
                            for h in show_alls), "status Show all kept the filter")
        self.assertTrue(any("status=" in h and "category=" not in h
                            for h in show_alls), "category Show all kept the filter")

    def test_the_active_filter_is_marked_and_clearable(self):
        self.three_statuses()
        body = self.page(status="todo")
        # The dropdown flags itself and says how much of it is ticked...
        self.assertIn('class="filter-menu is-on"', body)
        self.assertIn("Filter (1)", body)
        self.assertIn('value="todo" checked', body)
        self.assertNotIn('value="done" checked', body)
        self.assertIn("showing 1 of 3", body)
        self.assertIn("filtered by <strong>To Do</strong>", body)

        # ...its "Show all" clears just this column but keeps the sort...
        show_alls = re.findall(r'class="show-all" href="([^"]+)"', body)
        self.assertEqual(len(show_alls), 2)
        for href in show_alls:
            self.assertIn("dir=", href)
        self.assertTrue(any("status=" not in h for h in show_alls))

        # "Clear filters" drops every filter but keeps the sort, and following
        # it brings all three tasks back.
        cleared_href = self.clear_href(body)
        self.assertNotIn("status=", cleared_href)
        cleared = self.client.get(cleared_href).get_data(as_text=True)
        for title in ("Todo task", "Doing task", "Finished task"):
            self.assertIn(title, cleared)

    def test_deselect_all_unticks_a_whole_menu_in_one_click(self):
        work = self.category_id("Work")
        personal = self.category_id("Personal")
        self.add_task("Work task", category_id=work)
        self.add_task("Personal task", category_id=personal)
        self.add_task("Loose task")

        # Untouched, each menu offers a Deselect All button for its own column...
        body = self.page()
        buttons = re.findall(
            r'name="deselect" value="(status|category)"[^>]*>Deselect All</button>',
            body)
        self.assertEqual(sorted(buttons), ["category", "status"])
        # ...and with no filter set nothing has been opened yet.
        self.assertNotIn("<details class=\"filter-menu\" open>", body)

        # Pressing it clears the whole menu in one go. No JavaScript: the
        # browser submits ?deselect=category and the server re-renders...
        body = self.page(deselect="category")
        # ...every task still shown (the menu filters nothing)...
        for title in ("Work task", "Personal task", "Loose task"):
            self.assertIn(title, body)
        self.assertNotIn("filtered by", body)
        # ...every category box unticked, ready to tick just the one you want...
        for cid in (work, personal):
            self.assertNotIn('name="category" value="%d" checked' % cid, body)
        self.assertNotIn('name="category" value="none" checked', body)
        # ...with that menu left open for the very next click.
        self.assertEqual(body.count("<details class=\"filter-menu\" open>"), 1)
        # The status menu was left alone: still fully ticked, still closed.
        for value in ("todo", "in_progress", "done"):
            self.assertIn('value="%s" checked' % value, body)
        self.assertEqual(body.count('<details class="filter-menu"'), 2)

        # Now tick just Work and Apply, exactly as a browser would send it:
        pairs = [("sort", "date"), ("dir", "asc"), ("category", str(work))]
        submitted = self.client.get("/?" + urlencode(pairs)).get_data(as_text=True)
        self.assertIn("filtered by", submitted)
        self.assertIn("Work task", submitted)
        self.assertNotIn("Personal task", submitted)
        self.assertNotIn("Loose task", submitted)

    def test_deselect_all_clears_only_its_own_column(self):
        work = self.category_id("Work")
        self.add_task("Work task", category_id=work)
        self.add_task("Loose task")                    # uncategorised
        self.add_task("Finished task")
        self.client.post("/items/%d/status" % self.task_id("Finished task"),
                         data={"status": "done"}, follow_redirects=True)

        # Both columns filtering at once: only "Work task" survives.
        both = self.page(status="todo", category=work)
        self.assertIn("Work task", both)
        self.assertNotIn("Loose task", both)
        self.assertNotIn("Finished task", both)
        self.assertEqual(both.count('class="filter-menu is-on"'), 2)

        # Deselect All in the *category* menu drops the category filter (the
        # ticked box it still sent along is ignored)...
        body = self.page(status="todo", category=work, deselect="category")
        self.assertIn("Work task", body)
        self.assertIn("Loose task", body)              # hidden before, back now
        # ...but the status filter is untouched: still on, still hiding rows.
        self.assertNotIn("Finished task", body)
        self.assertEqual(body.count('class="filter-menu is-on"'), 1)
        self.assertIn("filtered by <strong>To Do</strong>", body)
        self.assertNotIn('name="category" value="%d" checked' % work, body)
        self.assertNotIn('name="category" value="none" checked', body)
        # ...and no link on the page quietly resurrects the cleared column.
        show_alls = re.findall(r'class="show-all" href="([^"]+)"', body)
        self.assertEqual(len(show_alls), 2)
        for href in show_alls:
            self.assertNotIn("category=", href)
        self.assertTrue(any("status=todo" in h for h in show_alls))

    def test_a_filter_that_matches_nothing_still_offers_a_way_out(self):
        self.add_task("Open task")                     # nothing is Done
        body = self.page(status="done")
        self.assertIn("No tasks match these filters.", body)
        self.assertIn("Clear filters", body)
        # The header dropdowns are still rendered, so you are never stuck...
        self.assertEqual(body.count('class="filter-menu'), 2)
        # ...and clearing brings the task back.
        cleared = self.client.get(self.clear_href(body)).get_data(as_text=True)
        self.assertIn("Open task", cleared)

    def test_filters_survive_adding_and_moving_tasks(self):
        self.add_task("Stays put")
        self.add_task("Mover")

        # Quick-add while filtered: the redirect keeps the filter.
        added = self.client.post("/items", query_string={"status": "todo"},
                                 data={"title": "Fresh"}, follow_redirects=False)
        self.assertEqual(added.status_code, 303)
        self.assertIn("status=todo", added.headers["Location"])

        # Changing a status while filtered also keeps it...
        moved = self.client.post("/items/%d/status" % self.task_id("Mover"),
                                 query_string={"status": "todo"},
                                 data={"status": "done"}, follow_redirects=False)
        self.assertEqual(moved.status_code, 303)
        self.assertIn("status=todo", moved.headers["Location"])

        # ...which means the task it just finished leaves the filtered view.
        page = self.client.get(moved.headers["Location"]).get_data(as_text=True)
        self.assertNotIn("Mover", page)
        self.assertIn("Stays put", page)
        self.assertIn("Fresh", page)

    def test_junk_filter_values_are_ignored(self):
        self.add_task("Still here", category_id=self.category_id("Work"))
        for params in ({"status": "cancelled"},
                       {"status": "; DROP TABLE items;--"},
                       {"category": "abc"},
                       {"category": "9999"},
                       {"category": "-1"}):
            body = self.page(**params)
            self.assertIn("Still here", body)           # rendered, not rejected
        self.assertEqual(len(self.tasks()), 1)          # and nothing executed
        # An unknown category is not a filter, so nothing is marked as filtered.
        self.assertNotIn("Clear filters", self.page(category="9999"))

    # -- feature 3: status straight from the table ---------------------------
    def test_status_can_be_marked_from_the_table_in_all_three_states(self):
        self.add_task("Ship the release")
        task_id = self.task_id("Ship the release")

        for status, label in (("in_progress", "In Progress"), ("done", "Done"),
                              ("todo", "To Do")):
            response = self.client.post(
                "/items/%d/status" % task_id, data={"status": status},
                follow_redirects=True,
            )
            page = response.get_data(as_text=True)
            self.assertEqual(self.tasks()[0]["status"], status)
            self.assertIn('class="task-row status-%s"' % status, page)
            self.assertIn("Moved to %s" % label, page)
            # The current state is also written out in the buttons.
            self.assertIn('class="mini is-current"', page)

    def test_invalid_status_is_rejected(self):
        self.add_task("Ship the release")
        response = self.client.post(
            "/items/%d/status" % self.task_id("Ship the release"),
            data={"status": "cancelled"}, follow_redirects=True,
        )
        self.assertIn(b"Status must be one of", response.data)
        self.assertEqual(self.tasks()[0]["status"], "todo")

    def test_banner_shows_counts_but_never_lists_tasks(self):
        self.add_task("Open task")
        self.add_task("Moving task")
        self.add_task("Finished task")
        self.client.post("/items/%d/status" % self.task_id("Moving task"),
                         data={"status": "in_progress"}, follow_redirects=True)
        self.client.post("/items/%d/status" % self.task_id("Finished task"),
                         data={"status": "done"}, follow_redirects=True)

        banner = self.page().split("</header>")[0]
        self.assertIn("1 To Do", banner)
        self.assertIn("1 In Progress", banner)
        self.assertIn("1 Done", banner)
        # The banner is a summary: no task titles, dates or links appear in it.
        self.assertNotIn("banner-list", banner)
        for title in ("Open task", "Moving task", "Finished task"):
            self.assertNotIn(title, banner)

    def test_banner_stays_compact_no_matter_how_many_tasks_exist(self):
        for index in range(12):
            self.add_task("Task %02d" % index)
        banner = self.page().split("</header>")[0]
        self.assertNotIn("banner-list", banner)
        self.assertNotIn("+4 more", banner)
        self.assertIn("12 To Do", banner)                # all twelve are counted
        self.assertNotIn("Task 00", banner)              # none of them are listed
        # The list of tasks itself is untouched by any of this.
        self.assertIn("Task 00", self.table())
        self.assertIn("Task 11", self.table())

    # -- editing ------------------------------------------------------------
    def test_title_priority_category_and_notes_can_be_edited(self):
        self.add_task("Draft the deck", priority="Low")
        response = self.client.post(
            "/items/%d/update" % self.task_id("Draft the deck"),
            data={
                "title": "Draft the deck v2",
                "priority": "High",
                "category_id": self.category_id("Work"),
                "notes": "Include the Q3 numbers",
                "day_date": DAY,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        updated = self.tasks()[0]
        self.assertEqual(updated["title"], "Draft the deck v2")
        self.assertEqual(updated["priority"], "High")
        self.assertEqual(updated["category"], "Work")
        self.assertEqual(updated["notes"], "Include the Q3 numbers")
        self.assertIn("Include the Q3 numbers", response.get_data(as_text=True))

    def test_editing_the_date_moves_the_task(self):
        self.add_task("Reschedule me", day_date="2026-09-29")
        self.client.post(
            "/items/%d/update" % self.task_id("Reschedule me"),
            data={"title": "Reschedule me", "priority": "High",
                  "category_id": "", "notes": "", "day_date": "2026-10-05"},
            follow_redirects=True,
        )
        self.assertEqual(self.tasks()[0]["day_date"], "2026-10-05")
        with self.app.app_context():
            dates = sorted(d["day_date"] for d in db.list_days())
        self.assertEqual(dates, ["2026-09-29", "2026-10-05"])

    def test_blank_title_is_rejected_on_edit(self):
        self.add_task("Keep me")
        response = self.client.post(
            "/items/%d/update" % self.task_id("Keep me"),
            data={"title": "   ", "priority": "High"}, follow_redirects=True,
        )
        self.assertIn(b"needs a title", response.data)
        self.assertEqual(self.tasks()[0]["title"], "Keep me")

    # -- validation ----------------------------------------------------------
    def test_invalid_date_is_rejected_but_app_stays_up(self):
        response = self.client.post(
            "/items", data={"title": "Bad date", "day_date": "29/09/2026"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Please pick a valid date", response.data)
        self.assertEqual(self.tasks(), [])

    def test_invalid_priority_is_rejected(self):
        response = self.add_task("Sneaky", priority="Urgent")
        self.assertIn(b"Priority must be one of", response.data)
        self.assertEqual(self.tasks(), [])

    def test_invalid_category_is_rejected(self):
        missing = self.add_task("Sneaky", category_id="424242")
        self.assertIn(b"no longer exists", missing.data)
        junk = self.add_task("Sneakier", category_id="not-a-number")
        self.assertIn(b"Unknown category", junk.data)
        self.assertEqual(self.tasks(), [])

    def test_blank_title_is_rejected(self):
        response = self.add_task("   ")
        self.assertIn(b"needs a title", response.data)
        self.assertEqual(self.tasks(), [])

    def test_missing_task_returns_404(self):
        self.assertEqual(self.client.get("/items/999/delete").status_code, 404)

    # -- deletion ------------------------------------------------------------
    def test_delete_asks_first_then_deletes(self):
        self.add_task("Pointless task")
        task_id = self.task_id("Pointless task")

        confirm = self.client.get("/items/%d/delete" % task_id)
        self.assertEqual(confirm.status_code, 200)
        self.assertIn(b"Delete this task?", confirm.data)
        self.assertEqual(len(self.tasks()), 1)          # nothing gone yet

        self.client.post("/items/%d/delete" % task_id, follow_redirects=True)
        self.assertEqual(self.tasks(), [])

    # -- bulk delete: the tick boxes down the left of the table --------------
    def test_bulk_delete_renders_a_tick_box_per_row_and_a_top_button(self):
        self.three_statuses()
        body = self.page()
        # The toolbar sits above the table; its action carries the current
        # sort (and, when set, the filters) in the query string...
        bulk = re.search(r'<form[^>]*id="bulk-delete"[^>]*>.*?</form>', body, re.S)
        self.assertIsNotNone(bulk, "no bulk-delete form above the table")
        self.assertIn('action="/items/delete?sort=date&amp;dir=desc"', bulk.group(0))
        self.assertIn("Delete selected", bulk.group(0))
        # ...every row starts with a tick box wired to that one form via the
        # HTML5 form attribute (no JavaScript, no nested forms)...
        for item in self.tasks():
            self.assertIn('name="id" value="%d" form="bulk-delete"' % item["id"],
                          body)
        self.assertEqual(body.count('form="bulk-delete"'), 3)
        # ...and a new, leftmost header cell sits above the tick boxes.
        self.assertIn('<th scope="col" class="col-pick"', body)

    def test_delete_selected_removes_the_tick_rows_in_one_click(self):
        self.three_statuses()
        todo = self.task_id("Todo task")
        doing = self.task_id("Doing task")
        finished = self.task_id("Finished task")

        # No confirmation step: one submit removes exactly the ticked rows.
        response = self.client.post(
            "/items/delete", data={"id": [str(todo), str(doing)]},
            query_string={"sort": "task", "dir": "asc"})
        self.assertEqual(response.status_code, 303)
        location = response.headers["Location"]
        self.assertIn("sort=task", location)       # back to the same view...
        self.assertNotIn("id=", location)          # ...without the ids
        page = self.client.get(location).get_data(as_text=True)
        self.assertIn("Deleted 2 task(s).", page)
        self.assertEqual([i["title"] for i in self.tasks()], ["Finished task"])
        self.assertEqual(page.count('form="bulk-delete"'), 1)   # one box left

        # A single tick gets the same singular flash as the row's own delete.
        page = self.client.post("/items/delete", data={"id": [str(finished)]},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Deleted: Finished task", page)
        self.assertEqual(self.tasks(), [])
        # With no rows left there is no table -- and no toolbar either.
        self.assertNotIn('id="bulk-delete"', page)

    def test_delete_selected_with_nothing_ticked_is_harmless(self):
        self.add_task("Keep me")
        response = self.client.post("/items/delete", data={}, follow_redirects=True)
        self.assertIn("Nothing selected to delete.",
                      response.get_data(as_text=True))
        self.assertEqual(len(self.tasks()), 1)      # nothing happened

    def test_delete_selected_ignores_junk_ids_and_keeps_the_filters(self):
        work = self.category_id("Work")
        self.add_task("Keep", category_id=work)
        self.add_task("Go", category_id=work)
        gone = self.task_id("Go")

        response = self.client.post(
            "/items/delete",
            data={"id": [str(gone), "abc", "-5", "999999"]},
            query_string={"sort": "task", "dir": "asc",
                          "status": "todo", "category": work},
        )
        self.assertEqual(response.status_code, 303)
        location = response.headers["Location"]
        # The view comes back exactly as it was...
        self.assertIn("sort=task", location)
        self.assertIn("status=todo", location)
        self.assertIn("category=%d" % work, location)
        self.assertNotIn("id=", location)
        # ...and only the one real id was acted on; the junk was dropped.
        self.assertEqual([i["title"] for i in self.tasks()], ["Keep"])

    # -- deleting a category -------------------------------------------------
    def test_category_shows_how_many_tasks_use_it(self):
        self.add_task("Uses Work", category_id=self.category_id("Work"))
        self.add_task("Also uses Work", category_id=self.category_id("Work"))
        self.add_task("No category")
        panel = self.page().split("<summary><span class=\"summary-title\">Categories")[1]
        self.assertIn("2 task(s)", panel)      # Work
        self.assertIn("0 task(s)", panel)      # the three unused seeded ones

    def test_delete_category_asks_first_and_mentions_the_tasks(self):
        self.add_task("Uses Work", category_id=self.category_id("Work"))
        category_id = self.category_id("Work")

        confirm = self.client.get("/categories/%d/delete" % category_id)
        self.assertEqual(confirm.status_code, 200)
        self.assertIn(b"Delete category Work?", confirm.data)
        self.assertIn(b"1 task(s) currently use this category", confirm.data)
        self.assertIsNotNone(self.category_id("Work"))      # nothing gone yet

    def test_deleting_a_category_keeps_its_tasks_and_uncategorises_them(self):
        self.add_task("Survives", priority="High", category_id=self.category_id("Work"))
        category_id = self.category_id("Work")

        response = self.client.post("/categories/%d/delete" % category_id,
                                    follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.category_id("Work"))         # category is gone

        stored = self.tasks()
        self.assertEqual(len(stored), 1)                    # but the task is not
        self.assertEqual(stored[0]["title"], "Survives")
        self.assertEqual(stored[0]["priority"], "High")     # other fields intact
        self.assertIsNone(stored[0]["category_id"])
        self.assertIn("now uncategorised", response.get_data(as_text=True))
        self.assertIn("&mdash;", self.table())              # shown as uncategorised

    def test_an_unused_category_can_be_deleted_directly(self):
        self.client.post("/categories", data={"name": "Temporary", "color": "#123456"},
                         follow_redirects=True)
        category_id = self.category_id("Temporary")
        confirm = self.client.get("/categories/%d/delete" % category_id)
        self.assertIn(b"No tasks use this category", confirm.data)
        self.client.post("/categories/%d/delete" % category_id, follow_redirects=True)
        self.assertIsNone(self.category_id("Temporary"))

    def test_a_deleted_category_name_can_be_reused(self):
        self.client.post("/categories", data={"name": "Temp", "color": "#123456"},
                         follow_redirects=True)
        self.client.post("/categories/%d/delete" % self.category_id("Temp"),
                         follow_redirects=True)
        again = self.client.post("/categories", data={"name": "Temp", "color": "#123456"},
                                 follow_redirects=True)
        self.assertNotIn(b"already exists", again.data)
        self.assertIsNotNone(self.category_id("Temp"))

    def test_missing_category_returns_404(self):
        self.assertEqual(self.client.get("/categories/999/delete").status_code, 404)
        self.assertEqual(self.client.post("/categories/999/delete").status_code, 404)

    def test_delete_keeps_the_sort_you_were_using(self):
        self.add_task("Alpha")
        self.add_task("Bravo")
        page = self.client.get(
            "/items/%d/delete" % self.task_id("Alpha"),
            query_string={"sort": "task", "dir": "desc"},
        ).get_data(as_text=True)
        # Cancel goes back to the sorted list, and the delete form posts with it.
        self.assertIn("sort=task&amp;dir=desc", page)

    # -- categories ----------------------------------------------------------
    def test_create_category_and_reject_duplicates_and_bad_colour(self):
        self.client.post("/categories", data={"name": "Health", "color": "#00ff88"},
                         follow_redirects=True)
        self.assertIsNotNone(self.category_id("Health"))

        duplicate = self.client.post("/categories",
                                     data={"name": "health", "color": "#00ff88"},
                                     follow_redirects=True)
        self.assertIn(b"already exists", duplicate.data)

        bad = self.client.post("/categories",
                               data={"name": "Broken", "color": "chartreuse"},
                               follow_redirects=True)
        self.assertIn(b"Colour must be a hex value", bad.data)
        self.assertIsNone(self.category_id("Broken"))

    # -- safety and the "no JavaScript" promise -------------------------------
    def test_titles_with_html_characters_are_escaped(self):
        self.add_task("Fish & Chips <b>urgent</b>")
        body = self.table()
        self.assertIn("Fish &amp; Chips &lt;b&gt;urgent&lt;/b&gt;", body)
        self.assertNotIn("<b>urgent</b>", body)

    def test_no_page_contains_javascript(self):
        self.add_task("Check for scripts")
        task_id = self.task_id("Check for scripts")
        category_id = self.category_id("Work")
        pages = [
            self.client.get("/"),
            self.client.get("/?status=todo&category=%d" % category_id),
            self.client.get("/?status=done"),            # filters that match nothing
            self.client.get("/items/%d/delete" % task_id),
            self.client.get("/categories/%d/delete" % category_id),
        ]
        for response in pages:
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True).lower()
            self.assertNotIn("<script", body)
            self.assertNotIn("javascript:", body)
            self.assertNotIn("onclick", body)
            self.assertNotIn("onchange", body)

        project = os.path.dirname(os.path.abspath(app_module.__file__))
        for folder, _, filenames in os.walk(project):
            if os.sep + "." in folder:
                continue
            for filename in filenames:
                self.assertFalse(filename.endswith(".js"),
                                 "unexpected JavaScript file: %s" % filename)

    def test_stylesheet_covers_each_status_and_priority(self):
        project = os.path.dirname(os.path.abspath(app_module.__file__))
        with open(os.path.join(project, "static", "style.css"), encoding="utf-8") as handle:
            css = handle.read()
        for token in ("status-todo", "status-in_progress", "status-done",
                      "priority-high", "priority-medium", "priority-low",
                      "filter-menu", "filter-toggle", "filter-form",
                      "clear-filters", "empty-cell", "home-grid",
                      "side-panel", "btn-ghost"):
            self.assertIn(token, css)

    def test_home_layout_keeps_the_categories_tab_beside_the_table(self):
        """Both panels live in the two-column home grid (categories on the
        left), and the table area keeps a minimum height so an open filter
        dropdown is never clipped by the horizontally scrolling table wrap."""
        page = self.page()
        self.assertIn('<div class="home-grid">', page)
        self.assertIn('<details class="panel side-panel">', page)
        self.assertIn("</details>\n</div>", page)     # grid wraps both panels
        grid = page.split('<div class="home-grid">')[1]
        self.assertIn('<details class="panel table-panel" open>', grid)
        self.assertIn('<details class="panel side-panel">', grid)

        project = os.path.dirname(os.path.abspath(app_module.__file__))
        with open(os.path.join(project, "static", "style.css"), encoding="utf-8") as handle:
            css = handle.read()
        self.assertIn("min-height: 30rem", css)       # room for the dropdown
        self.assertIn("grid-column: 1", css)          # categories on the left
        self.assertIn("grid-column: 2", css)          # table on the right



    # -- notes ---------------------------------------------------------------
    def _note_id(self, label):
        """Find a note by title/label, in the live list or the archive."""
        with self.app.app_context():
            for note in db.list_notes() + db.list_notes(archived=True):
                if note["title"] == label or note["label"] == label:
                    return note["id"]
        raise AssertionError("no such note: %s" % label)

    def test_notes_tab_lists_notes(self):
        self.client.post("/notes", data={"title": "First", "body": "One", "category_id": ""},
                         follow_redirects=True)
        self.client.post("/notes", data={"title": "Second", "body": "Two", "category_id": ""},
                         follow_redirects=True)
        body = self.client.get("/notes").get_data(as_text=True)
        self.assertIn("Your notes", body)
        self.assertIn("Second", body)          # newest first
        self.assertIn("First", body)
        self.assertIn("One", body)
        self.assertIn("Two", body)

    def test_create_note_stores_title_body_and_category(self):
        response = self.client.post("/notes", data={"title": "Shopping", "body": "Milk, eggs",
                                                       "category_id": self.note_category_id("Journal")},
                                    follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Added note Shopping.", response.data)
        with self.app.app_context():
            notes = db.list_notes()
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]["title"], "Shopping")
            self.assertEqual(notes[0]["body"], "Milk, eggs")
            self.assertEqual(notes[0]["category"], "Journal")

    def test_create_note_default_category_none(self):
        self.client.post("/notes", data={"title": "No category", "body": "text", "category_id": ""},
                         follow_redirects=True)
        with self.app.app_context():
            self.assertIsNone(db.list_notes()[0]["category_id"])
            self.assertIsNone(db.list_notes()[0]["category"])

    def test_title_is_optional(self):
        # A blank (or whitespace-only) title is fine -- the body is the note.
        response = self.client.post("/notes", data={"title": "  ", "body": "Buy more coffee"},
                                    follow_redirects=True)
        self.assertIn(b"Added note Buy more coffee.", response.data)   # label = first body line
        with self.app.app_context():
            note = db.list_notes()[0]
            self.assertEqual(note["title"], "")
            self.assertEqual(note["label"], "Buy more coffee")

    def test_blank_body_is_rejected(self):
        response = self.client.post("/notes", data={"title": "Heading only", "body": "   "},
                                    follow_redirects=True)
        self.assertIn(b"A note needs a body.", response.data)
        with self.app.app_context():
            self.assertEqual(db.list_notes(), [])

    def test_body_textarea_sits_below_the_title_and_has_room(self):
        body = self.client.get("/notes").get_data(as_text=True)
        title_pos = body.index('name="title"')
        body_pos = body.index('name="body"')
        self.assertLess(title_pos, body_pos)                # body below the title box
        textarea = body[body_pos:body.index(">", body_pos)]
        self.assertIn('rows="8"', textarea)                 # a proper writing area
        self.assertIn("required", textarea)
        title_tag = body[title_pos:body.index(">", title_pos)]
        self.assertNotIn("required", title_tag)             # ...but the title is not

    def test_note_too_long_is_rejected(self):
        too_long = "x" * 5001
        response = self.client.post("/notes", data={"title": "Long", "body": too_long},
                                    follow_redirects=True)
        self.assertIn(b"That note is too long", response.data)
        with self.app.app_context():
            self.assertEqual(db.list_notes(), [])

    def test_invalid_category_is_rejected(self):
        missing = self.client.post("/notes", data={"title": "Unknown", "body": "x",
                                                   "category_id": "424242"},
                                   follow_redirects=True)
        self.assertIn(b"no longer exists", missing.data)
        junk = self.client.post("/notes", data={"title": "Junk", "body": "x",
                                                "category_id": "not-a-number"},
                                follow_redirects=True)
        self.assertIn(b"Unknown note category", junk.data)
        with self.app.app_context():
            self.assertEqual(db.list_notes(), [])

    def test_task_categories_cannot_be_used_for_notes(self):
        # The two category tables have independent id sequences. Creating an
        # extra *task* category gives it an id (5) that the Notes tab's own
        # table -- seeded with three -- does not have, so the note rejects it.
        self.client.post("/categories", data={"name": "Zebra", "color": "#123456"})
        task_cat_id = self.category_id("Zebra")
        self.assertIsNotNone(task_cat_id)
        response = self.client.post(
            "/notes",
            data={"title": "Nope", "body": "x", "category_id": task_cat_id},
            follow_redirects=True,
        )
        self.assertIn(b"That note category no longer exists.", response.data)
        with self.app.app_context():
            self.assertEqual(db.list_notes(), [])

    def test_note_and_task_categories_are_independent(self):
        # A category created for notes must not show up in the Tasks sidebar...
        self.client.post("/notes/categories", data={"name": "Brainstorm", "color": "#123456"},
                         follow_redirects=True)
        self.assertIsNotNone(self.note_category_id("Brainstorm"))
        self.assertIsNone(self.category_id("Brainstorm"))
        # ...and one created for tasks must not show up in the Notes select.
        self.client.post("/categories", data={"name": "Chores", "color": "#654321"},
                         follow_redirects=True)
        self.assertIsNotNone(self.category_id("Chores"))
        self.assertIsNone(self.note_category_id("Chores"))
        notes_page = self.client.get("/notes").get_data(as_text=True)
        self.assertIn("Brainstorm", notes_page)
        self.assertNotIn("Chores", notes_page)         # task-only categories stay out
        self.assertIn("Journal", notes_page)           # seeded note categories are in

    def test_note_delete_asks_then_removes_it(self):
        self.client.post("/notes", data={"title": "Trash this", "body": "x"}, follow_redirects=True)
        note_id = self._note_id("Trash this")
        confirm = self.client.get("/notes/%d/delete" % note_id)
        self.assertEqual(confirm.status_code, 200)
        self.assertIn(b"Delete note Trash this?", confirm.data)
        # card title + archive-button title + delete-link title
        self.assertEqual(self.client.get("/notes").get_data(as_text=True).count("Trash this"), 3)

        self.client.post("/notes/%d/delete" % note_id, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(db.list_notes(), [])

    def test_delete_note_redirects_back_to_notes(self):
        self.client.post("/notes", data={"title": "One", "body": "text"}, follow_redirects=True)
        note_id = self._note_id("One")
        response = self.client.post("/notes/%d/delete" % note_id, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Deleted note One.", response.data)

    def test_unknown_note_returns_404(self):
        self.assertEqual(self.client.get("/notes/999/delete").status_code, 404)
        self.assertEqual(self.client.post("/notes/999/delete").status_code, 404)
        self.assertEqual(self.client.post("/notes/999/archive").status_code, 404)
        self.assertEqual(self.client.post("/notes/999/unarchive").status_code, 404)

    # -- archiving -----------------------------------------------------------
    def test_archive_moves_the_note_into_the_archive_dropdown(self):
        self.client.post("/notes", data={"title": "Old plan", "body": "keep me"},
                         follow_redirects=True)
        note_id = self._note_id("Old plan")

        response = self.client.post("/notes/%d/archive" % note_id, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Archived note Old plan.", response.data)

        body = self.client.get("/notes").get_data(as_text=True)
        # The archive section is a collapsed dropdown that announces its count.
        self.assertIn("Archived notes", body)
        self.assertIn("1 note", body)
        archived_section = body[body.index("Archived notes"):]
        self.assertIn("Old plan", archived_section)     # it lives down there now
        self.assertIn("archived", archived_section)     # the badge says so in words
        # ...and it is gone from the live list above the dropdown.
        live_section = body[:body.index("Archived notes")]
        self.assertNotIn("Old plan", live_section)
        with self.app.app_context():
            self.assertEqual(db.list_notes(), [])
            self.assertEqual([n["title"] for n in db.list_notes(archived=True)],
                             ["Old plan"])

    def test_restore_moves_the_note_back(self):
        self.client.post("/notes", data={"title": "Bounce", "body": "x"}, follow_redirects=True)
        note_id = self._note_id("Bounce")
        self.client.post("/notes/%d/archive" % note_id, follow_redirects=True)

        response = self.client.post("/notes/%d/unarchive" % note_id, follow_redirects=True)
        self.assertIn(b"Restored note Bounce from the archive.", response.data)
        body = self.client.get("/notes").get_data(as_text=True)
        self.assertIn("Nothing archived yet.", body)    # dropdown is empty again
        with self.app.app_context():
            self.assertEqual([n["title"] for n in db.list_notes()], ["Bounce"])
            self.assertEqual(db.list_notes(archived=True), [])

    def test_archived_note_can_still_be_deleted(self):
        self.client.post("/notes", data={"title": "Bin me", "body": "x"}, follow_redirects=True)
        note_id = self._note_id("Bin me")
        self.client.post("/notes/%d/archive" % note_id, follow_redirects=True)

        confirm = self.client.get("/notes/%d/delete" % note_id)
        self.assertEqual(confirm.status_code, 200)
        self.assertIn(b"Delete note Bin me?", confirm.data)
        self.client.post("/notes/%d/delete" % note_id, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(db.list_notes(), [])
            self.assertEqual(db.list_notes(archived=True), [])

    def test_archiving_twice_is_a_harmless_no_op(self):
        self.client.post("/notes", data={"title": "Steady", "body": "x"}, follow_redirects=True)
        note_id = self._note_id("Steady")
        self.client.post("/notes/%d/archive" % note_id, follow_redirects=True)
        response = self.client.post("/notes/%d/archive" % note_id, follow_redirects=True)
        self.assertEqual(response.status_code, 200)     # still fine, still archived
        with self.app.app_context():
            self.assertEqual(len(db.list_notes(archived=True)), 1)

    def test_note_categories_panel_is_a_collapsed_dropdown(self):
        body = self.client.get("/notes").get_data(as_text=True)
        start = body.index("Note categories")
        tag_start = body.rfind("<details", 0, start)
        self.assertNotIn(" open", body[tag_start:start])   # collapsed by default

    def test_export_marks_archived_notes(self):
        self.client.post("/notes", data={"title": "Live", "body": "a", "category_id": ""},
                         follow_redirects=True)
        self.client.post("/notes", data={"title": "Put away", "body": "b", "category_id": ""},
                         follow_redirects=True)
        self.client.post("/notes/%d/archive" % self._note_id("Put away"), follow_redirects=True)
        document = json.loads(export.to_json())
        by_title = {note["title"]: note for note in document["notes"]}
        self.assertEqual(by_title["Live"]["archived"], 0)
        self.assertEqual(by_title["Put away"]["archived"], 1)
        # live notes sort ahead of archived ones in the export
        self.assertEqual(document["notes"][0]["title"], "Live")

    def test_note_list_includes_category_colour_dot(self):
        self.client.post("/notes", data={"title": "Ideas note", "body": "x",
                                         "category_id": self.note_category_id("Ideas")},
                         follow_redirects=True)
        body = self.client.get("/notes").get_data(as_text=True)
        self.assertIn('--cat: #7c3aed', body)   # Ideas' hex colour (note category)
        self.assertIn("Ideas", body)

    def test_notes_tab_in_no_js_pages(self):
        pages = [self.client.get("/notes")]
        for response in pages:
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True).lower()
            self.assertNotIn("<script", body)
            self.assertNotIn("javascript:", body)
            self.assertNotIn("onclick", body)
            self.assertNotIn("onchange", body)

    def test_export_json_contains_notes(self):
        self.client.post("/notes", data={"title": "Keep", "body": "Go", "category_id": ""},
                         follow_redirects=True)
        self.client.post("/notes", data={"title": "Discard", "body": "No", "category_id": ""},
                         follow_redirects=True)
        document = json.loads(export.to_json())
        self.assertEqual(len(document["notes"]), 2)
        self.assertEqual(document["notes"][0]["title"], "Discard")   # newest first
        self.assertEqual(document["notes"][0]["body"], "No")
        self.assertIn("categories", document)
        # Notes carry their own categories into the export as well.
        self.assertIn("note_categories", document)
        self.assertEqual([c["name"] for c in document["note_categories"]],
                         ["Ideas", "Journal", "Reference"])
        self.assertEqual(len(document["days"]), 0)

    # -- note categories -----------------------------------------------------
    def test_default_note_categories_are_seeded(self):
        with self.app.app_context():
            names = [c["name"] for c in db.list_note_categories()]
        self.assertEqual(names, ["Ideas", "Journal", "Reference"])

    def test_note_category_can_be_created_and_deleted(self):
        response = self.client.post("/notes/categories",
                                    data={"name": "Recipes", "color": "#ab12cd"},
                                    follow_redirects=True)
        self.assertIn(b"Added category Recipes.", response.data)
        category_id = self.note_category_id("Recipes")
        self.assertIsNotNone(category_id)

        # Attach a note, then walk the confirm -> delete flow.
        self.client.post("/notes", data={"title": "", "body": "Pasta night",
                                         "category_id": category_id},
                         follow_redirects=True)
        confirm = self.client.get("/notes/categories/%d/delete" % category_id)
        self.assertEqual(confirm.status_code, 200)
        self.assertIn(b"Delete category Recipes?", confirm.data)
        self.assertIn(b"1 note(s) currently use this category", confirm.data)

        response = self.client.post("/notes/categories/%d/delete" % category_id,
                                    follow_redirects=True)
        self.assertIn(
            b"Deleted category Recipes. Its 1 note(s) are now uncategorised.",
            response.data,
        )
        self.assertIsNone(self.note_category_id("Recipes"))
        with self.app.app_context():
            note = db.list_notes()[0]
            self.assertIsNone(note["category_id"])     # the note itself survives
            self.assertEqual(note["body"], "Pasta night")

    def test_note_category_create_rejects_bad_input(self):
        blank = self.client.post("/notes/categories", data={"name": "   "},
                                 follow_redirects=True)
        self.assertIn(b"A category needs a name.", blank.data)
        duplicate = self.client.post("/notes/categories", data={"name": "ideas"},
                                     follow_redirects=True)
        self.assertIn(b"ideas already exists.", duplicate.data)   # case-insensitive
        bad_color = self.client.post("/notes/categories",
                                     data={"name": "X", "color": "red"},
                                     follow_redirects=True)
        self.assertIn(b"Colour must be a hex value", bad_color.data)
        with self.app.app_context():
            self.assertEqual([c["name"] for c in db.list_note_categories()],
                             ["Ideas", "Journal", "Reference"])

    def test_unknown_note_category_returns_404(self):
        self.assertEqual(self.client.get("/notes/categories/999/delete").status_code, 404)
        self.assertEqual(self.client.post("/notes/categories/999/delete").status_code, 404)

    def test_legacy_notes_are_migrated_to_their_own_categories(self):
        # Build a database in the *old* shape: notes.category_id points at the
        # task categories table. init_db must rebuild it so the note keeps its
        # category by name, without touching the task categories.
        legacy = os.path.join(_TMP_ROOT, "legacy-%s.db" % self._testMethodName)
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(legacy + suffix):
                os.remove(legacy + suffix)
        conn = sqlite3.connect(legacy)
        conn.executescript(
            """
            CREATE TABLE categories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                color      TEXT NOT NULL DEFAULT '#6b7280',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
                body        TEXT NOT NULL DEFAULT '' CHECK (length(body) <= 5000),
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO categories (name, color) VALUES ('Personal', '#7c3aed');
            INSERT INTO notes (title, body, category_id) VALUES ('Old note', 'kept', 1);
            """
        )
        conn.commit()
        conn.close()

        db.init_db(legacy)      # the migration runs here

        conn = sqlite3.connect(legacy)
        conn.row_factory = sqlite3.Row
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'notes'"
        ).fetchone()[0]
        self.assertIn("REFERENCES note_categories", sql)
        note = conn.execute("SELECT * FROM notes").fetchone()
        self.assertEqual(note["title"], "Old note")
        self.assertEqual(note["body"], "kept")
        kept = conn.execute(
            "SELECT name FROM note_categories WHERE id = ?", (note["category_id"],)
        ).fetchone()
        self.assertEqual(kept["name"], "Personal")     # carried over by name
        self.assertEqual(                             # task categories untouched
            conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0], 1
        )
        conn.close()

    def test_day_rows_are_cleaned_up_with_their_tasks(self):
        # The web UI no longer exposes days, but the data layer guarantees that
        # removing a date removes the tasks that belong to it.
        self.add_task("Goes with the day")
        with self.app.app_context():
            day_id = db.list_days()[0]["id"]
            self.assertTrue(db.delete_day(day_id))
            self.assertEqual(db.list_days(), [])
            self.assertEqual(db.list_items_sorted(), [])

    # -- exports -------------------------------------------------------------
    def test_export_json_contains_days_items_and_categories(self):
        self.add_task("Write the report", category_id=self.category_id("Work"))
        document = json.loads(export.to_json())
        self.assertEqual(len(document["days"]), 1)
        self.assertEqual(document["days"][0]["day_date"], DAY)
        self.assertEqual(document["days"][0]["items"][0]["title"], "Write the report")
        self.assertEqual(document["days"][0]["items"][0]["category"], "Work")
        self.assertEqual([c["name"] for c in document["categories"]],
                         ["Admin", "Errands", "Personal", "Work"])

    def test_export_csv_has_one_row_per_item(self):
        self.add_task("First")
        self.add_task("Second")
        rows = export.to_csv().strip().splitlines()
        self.assertEqual(len(rows), 3)                       # header + 2 tasks
        self.assertTrue(rows[0].startswith("day_date,day_label,title"))
        self.assertIn("First", rows[1] + rows[2])

    def test_export_sql_is_restorable(self):
        self.add_task("Survives a dump")
        dump = export.to_sql()
        self.assertIn("CREATE TABLE", dump)

        restored = os.path.join(_TMP_ROOT, "restored.db")
        if os.path.exists(restored):
            os.remove(restored)
        conn = sqlite3.connect(restored)
        try:
            conn.executescript(dump)
            stored = [row[0] for row in conn.execute("SELECT title FROM items")]
        finally:
            conn.close()
        self.assertEqual(stored, ["Survives a dump"])

    def test_export_writes_to_a_file(self):
        self.add_task("Export me")
        out = os.path.join(_TMP_ROOT, "export-out.json")
        self.assertEqual(export.main(["--format", "json", "--out", out]), 0)
        with open(out) as handle:
            self.assertEqual(len(json.load(handle)["days"]), 1)


def tearDownModule():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
