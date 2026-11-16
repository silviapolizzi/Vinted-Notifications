"""Tests for the pure helpers in core.py."""

import datetime as dt
import os
import queue
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

# The requester singleton reads the settings at import time: base creates a
# throwaway database before anything else is imported.
import base  # noqa: E402
import core  # noqa: E402
import db  # noqa: E402
from pyVintedVN.items.item import Item  # noqa: E402


def make_item(**overrides):
    data = {
        "id": 1,
        "title": "Nike Air <Max> & Co",
        "brand_title": "Nike",
        "url": "https://www.vinted.it/items/1-nike",
        "price": {"amount": "50.0", "currency_code": "EUR"},
        "photo": {
            "url": "https://img/1.jpg",
            "high_resolution": {"timestamp": 1700000000},
        },
        "user": {"id": 99, "country_iso_code": "IT"},
    }
    data.update(overrides)
    return Item(data)


class FormatPriceTests(unittest.TestCase):
    def test_none(self):
        self.assertEqual(core.format_price(None, "EUR"), "N/D")

    def test_float(self):
        self.assertEqual(core.format_price(50.0, "EUR"), "50 EUR")

    def test_decimals(self):
        self.assertEqual(core.format_price("12.50", "EUR"), "12.5 EUR")

    def test_string(self):
        self.assertEqual(core.format_price("abc", "EUR"), "abc EUR")


class FormatMessageTests(unittest.TestCase):
    def test_html_is_escaped(self):
        item = make_item()
        message = core.format_item_message("{title}|{brand}|{price}", item)
        self.assertIn("&lt;Max&gt; &amp; Co", message)
        self.assertNotIn("<Max>", message)

    def test_invalid_template_falls_back(self):
        item = make_item()
        message = core.format_item_message("{unknown}", item)
        self.assertIn("Nike Air", message)

    def test_truncate_html_keeps_entities_intact(self):
        text = "a" * 10 + "&amp;"
        truncated = core.truncate_html(text, limit=12)
        self.assertLessEqual(len(truncated), 12)
        self.assertFalse(truncated.endswith("&"))


class SplitMessageTests(unittest.TestCase):
    def test_short(self):
        self.assertEqual(core.split_message("hello"), ["hello"])

    def test_long(self):
        text = "\n".join(["line"] * 2000)
        parts = core.split_message(text, limit=4096)
        self.assertTrue(all(len(part) <= 4096 for part in parts))
        self.assertEqual("".join(parts).count("line"), 2000)


class QuietHoursTests(unittest.TestCase):
    def test_disabled(self):
        self.assertFalse(core.is_quiet_time(""))
        self.assertFalse(core.is_quiet_time("off"))

    def test_invalid(self):
        self.assertFalse(core.is_quiet_time("nonsense"))
        self.assertFalse(core.is_valid_quiet_spec("23-07"))

    def test_same_day(self):
        now = dt.datetime(2024, 1, 1, 10, 30)
        self.assertTrue(core.is_quiet_time("09:00-12:00", now=now))
        self.assertFalse(core.is_quiet_time("12:00-14:00", now=now))

    def test_crossing_midnight(self):
        late = dt.datetime(2024, 1, 1, 23, 30)
        early = dt.datetime(2024, 1, 1, 6, 30)
        afternoon = dt.datetime(2024, 1, 1, 15, 0)
        self.assertTrue(core.is_quiet_time("23:00-07:00", now=late))
        self.assertTrue(core.is_quiet_time("23:00-07:00", now=early))
        self.assertFalse(core.is_quiet_time("23:00-07:00", now=afternoon))


class BanwordsTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(core.parse_banwords(""), [])
        self.assertEqual(core.parse_banwords(" Wii ||| 3ds "), ["wii", "3ds"])

    def test_add_and_remove(self):
        value = core.add_banword("wii", "Amiibo")
        self.assertEqual(core.parse_banwords(value), ["wii", "amiibo"])
        value = core.add_banword(value, "wii")
        self.assertEqual(core.parse_banwords(value), ["wii", "amiibo"])
        value = core.remove_banword(value, "WII")
        self.assertEqual(core.parse_banwords(value), ["amiibo"])

    def test_contains(self):
        self.assertTrue(core.contains_banwords("Amiibo card", "wii ||| amiibo"))
        self.assertFalse(core.contains_banwords("Switch game", "wii ||| amiibo"))
        self.assertFalse(core.contains_banwords("Switch game", ""))


class ItemParsingTests(unittest.TestCase):
    def test_missing_optional_fields(self):
        item = Item({"id": 5, "url": "https://www.vinted.it/items/5-x"})
        self.assertTrue(item.is_valid)
        self.assertEqual(item.currency, "EUR")
        self.assertIsNone(item.price)
        self.assertIsNone(item.photo)
        self.assertEqual(item.raw_timestamp, 0)
        self.assertFalse(item.is_new_item())

    def test_partial_photo(self):
        item = Item({"id": 6, "url": "https://x/items/6-y", "photo": {}})
        self.assertIsNone(item.photo)
        self.assertEqual(item.raw_timestamp, 0)

    def test_buy_url(self):
        item = make_item()
        self.assertIn("transaction/buy/new", item.buy_url)
        self.assertIn("item_id%5D=1", item.buy_url)

    def test_missing_url_is_invalid(self):
        self.assertFalse(Item({"id": 7}).is_valid)

    def test_new_item(self):
        item = make_item()
        self.assertFalse(item.is_new_item())  # timestamp from 2023


class ClearItemQueueTests(base.TempDatabaseTestCase):
    """End to end check of the outbox path, without Telegram."""

    def _prepare(self, item):
        db.add_query_to_db("https://www.vinted.it/catalog?search_text=nike", "Nike")
        query_id = db.get_queries()[0][0]
        items_queue = queue.Queue()
        items_queue.put(([item], query_id))
        new_items_queue = queue.Queue()
        core.clear_item_queue(items_queue, new_items_queue)
        return query_id, new_items_queue

    def test_new_item_is_queued_in_the_outbox(self):
        _, new_items_queue = self._prepare(make_item())

        pending = db.get_pending_outbox()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["url"], "https://www.vinted.it/items/1-nike")
        self.assertIn("Nike Air &lt;Max&gt; &amp; Co", pending[0]["content"])
        self.assertIn("50 EUR", pending[0]["content"])
        self.assertIn("Max", pending[0]["caption"])
        self.assertEqual(pending[0]["photo_url"], "https://img/1.jpg")
        self.assertIsNone(pending[0]["buy_url"])
        self.assertEqual(new_items_queue.qsize(), 1)
        self.assertTrue(db.is_item_in_db_by_id(1))

    def test_photo_is_disabled(self):
        db.set_parameter("send_photo", "False")
        self._prepare(make_item())

        pending = db.get_pending_outbox()
        self.assertIsNone(pending[0]["photo_url"])
        self.assertIsNone(pending[0]["caption"])

    def test_buy_button(self):
        db.set_parameter("show_buy_button", "True")
        self._prepare(make_item())

        pending = db.get_pending_outbox()
        self.assertIn("transaction/buy/new", pending[0]["buy_url"])
        self.assertEqual(pending[0]["buy_text"], "Buy")

    def test_banword_skips_the_item(self):
        db.set_parameter("banwords", "max ||| amiibo")
        self._prepare(make_item())

        self.assertEqual(db.get_pending_outbox(), [])

    def test_items_with_the_same_timestamp_are_all_notified(self):
        """Regression: DOM items share the scrape timestamp."""
        db.add_query_to_db("https://www.vinted.it/catalog?search_text=nike", "Nike")
        query_id = db.get_queries()[0][0]
        items = [
            make_item(
                id=index,
                url=f"https://www.vinted.it/items/{index}-nike-{index}",
                photo={
                    "url": f"https://img/{index}.jpg",
                    "high_resolution": {"timestamp": 1700000000},
                },
            )
            for index in range(1, 4)
        ]

        items_queue = queue.Queue()
        items_queue.put((items, query_id))
        core.clear_item_queue(items_queue, queue.Queue())

        pending = db.get_pending_outbox()
        self.assertEqual(len(pending), 3)
        self.assertEqual(len({row["url"] for row in pending}), 3)

    def test_item_is_notified_only_once(self):
        query_id, _ = self._prepare(make_item())
        db.mark_outbox_sent(db.get_pending_outbox()[0]["id"])

        items_queue = queue.Queue()
        items_queue.put(([make_item()], query_id))
        core.clear_item_queue(items_queue, queue.Queue())

        self.assertEqual(db.get_pending_outbox(), [])

    def test_invalid_item_is_ignored(self):
        self._prepare(Item({"id": 99}))

        self.assertEqual(db.get_pending_outbox(), [])

    def test_seen_item_updates_the_timestamp(self):
        query_id, _ = self._prepare(make_item())
        db.mark_outbox_sent(db.get_pending_outbox()[0]["id"])
        older = make_item(
            photo={
                "url": "https://img/1.jpg",
                "high_resolution": {"timestamp": 1600000000},
            }
        )

        items_queue = queue.Queue()
        items_queue.put(([older], query_id))
        core.clear_item_queue(items_queue, queue.Queue())

        self.assertEqual(db.get_last_timestamp(query_id), 1700000000)


if __name__ == "__main__":
    unittest.main()
