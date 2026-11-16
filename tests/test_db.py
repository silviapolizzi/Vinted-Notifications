"""Tests for db.py: migrations, outbox, user cache and query helpers."""

import os
import sys
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import base  # noqa: E402,F401
import core  # noqa: E402
import db  # noqa: E402


class MigrationTests(base.TempDatabaseTestCase):
    def test_final_version(self):
        self.assertEqual(db.get_parameter("version"), "1.0.6")

    def test_new_parameters_exist(self):
        for key in (
            "browser_enabled",
            "browser_mode",
            "browser_state",
            "browser_refresh_requested",
            "web_ui_port",
            "send_photo",
            "caption_template",
            "quiet_hours",
            "notifications_paused",
        ):
            self.assertIsNotNone(db.get_parameter(key), key)

    def test_tables_exist(self):
        # A failing query would be swallowed by db helpers, so assert directly
        import sqlite3

        conn = sqlite3.connect(db.DB_PATH)
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        self.assertIn("outbox", names)
        self.assertIn("users", names)

    def test_selector_does_not_skip_migrations(self):
        migration_files = sorted(
            [
                "1.0.3_1.0.4.sql",
                "1.0.4_1.0.5.sql",
                "1.0.5_1.0.5.1.sql",
                "1.0.5.1_1.0.5.2.sql",
                "1.0.5.2_1.0.5.3.sql",
                "1.0.5.3_1.0.5.4.sql",
                "1.0.5.4_1.0.6.sql",
            ]
        )
        self.assertEqual(
            db.get_next_migration(migration_files, "1.0.5"),
            "1.0.5_1.0.5.1.sql",
        )
        self.assertEqual(
            db.get_next_migration(migration_files, "1.0.5.4"),
            "1.0.5.4_1.0.6.sql",
        )
        self.assertIsNone(db.get_next_migration(migration_files, "1.0.6"))

    def test_selector_ignores_unrelated_versions(self):
        self.assertIsNone(db.get_next_migration(["1.0.6.1_1.0.7.sql"], "1.0.6"))


class OutboxTests(base.TempDatabaseTestCase):
    def test_add_and_fetch(self):
        db.add_outbox_item(
            content="hello",
            url="https://www.vinted.it/items/1",
            button_text="Open Vinted",
            query_id=1,
        )
        pending = db.get_pending_outbox()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["content"], "hello")
        self.assertEqual(pending[0]["attempts"], 0)
        self.assertEqual(db.get_outbox_count(), 1)

    def test_mark_sent(self):
        item_id = db.add_outbox_item("hello", "https://x", "Open")
        db.mark_outbox_sent(item_id)
        self.assertEqual(db.get_pending_outbox(), [])
        self.assertEqual(db.get_outbox_count(), 0)

    def test_mark_failed_increments_attempts(self):
        item_id = db.add_outbox_item("hello", "https://x", "Open")
        db.mark_outbox_failed(item_id, "boom")
        pending = db.get_pending_outbox()
        self.assertEqual(pending[0]["attempts"], 1)
        self.assertEqual(pending[0]["last_error"], "boom")

    def test_expire_old_items(self):
        item_id = db.add_outbox_item("hello", "https://x", "Open")
        # Backdate the row
        import sqlite3

        conn = sqlite3.connect(db.DB_PATH)
        try:
            conn.execute(
                "UPDATE outbox SET created_at=? WHERE id=?",
                (int(time.time()) - 90000, item_id),
            )
            conn.commit()
        finally:
            conn.close()

        db.expire_outbox(max_age_seconds=86400)
        self.assertEqual(db.get_pending_outbox(), [])

    def test_cleanup_removes_sent_items_only(self):
        sent_id = db.add_outbox_item("sent", "https://x/1", "Open")
        db.add_outbox_item("pending", "https://x/2", "Open")
        db.mark_outbox_sent(sent_id)

        db.cleanup_outbox(max_age_seconds=0)

        pending = db.get_pending_outbox()
        self.assertEqual([row["content"] for row in pending], ["pending"])

    def test_ordering(self):
        first = db.add_outbox_item("first", "https://x/1", "Open")
        second = db.add_outbox_item("second", "https://x/2", "Open")
        pending = db.get_pending_outbox()
        self.assertEqual([row["id"] for row in pending], [first, second])


class UserCacheTests(base.TempDatabaseTestCase):
    def test_cache_roundtrip(self):
        self.assertIsNone(db.get_cached_user_country("42"))
        db.set_cached_user_country("42", "IT")
        self.assertEqual(db.get_cached_user_country("42"), "IT")

    def test_cache_is_updated(self):
        db.set_cached_user_country("42", "IT")
        db.set_cached_user_country("42", "FR")
        self.assertEqual(db.get_cached_user_country("42"), "FR")


class QueryTests(base.TempDatabaseTestCase):
    def test_query_without_name_or_search_text(self):
        """Regression: this raised TypeError inside get_formatted_query_list."""
        url = "https://www.vinted.it/catalog?brand_ids%5B%5D=53&order=newest_first"
        db.add_query_to_db(url, None)
        listing = core.get_formatted_query_list()
        self.assertIn("https://www.vinted.it/catalog", listing)

    def test_query_with_name(self):
        db.add_query_to_db("https://www.vinted.it/catalog?search_text=nike", "Nike")
        self.assertEqual(core.get_formatted_query_list(), "1. Nike")

    def test_enabled_filter(self):
        db.add_query_to_db("https://www.vinted.it/catalog?search_text=nike", "Nike")
        query_id = db.get_queries()[0][0]

        self.assertEqual(len(db.get_queries_for_scraping()), 1)
        db.set_query_enabled(query_id, False)
        self.assertEqual(db.get_queries_for_scraping(), [])
        db.set_query_enabled(query_id, True)
        self.assertEqual(len(db.get_queries_for_scraping()), 1)

    def test_per_page_is_returned(self):
        db.add_query_to_db("https://www.vinted.it/catalog?search_text=nike", "Nike")
        query_id = db.get_queries()[0][0]
        import sqlite3

        conn = sqlite3.connect(db.DB_PATH)
        try:
            conn.execute("UPDATE queries SET per_page=96 WHERE id=?", (query_id,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(db.get_queries_for_scraping()[0][4], 96)

    def test_get_query(self):
        db.add_query_to_db("https://www.vinted.it/catalog?search_text=nike", "Nike")
        query_id = db.get_queries()[0][0]
        row = db.get_query(query_id)
        self.assertEqual(row[3], "Nike")
        self.assertEqual(row[4], 1)


if __name__ == "__main__":
    unittest.main()
