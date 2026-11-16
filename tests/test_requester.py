"""Tests for the requester browser-state integration and proxy helpers."""

import json
import os
import sys
import time
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import base  # noqa: E402,F401
import browser_session  # noqa: E402
import db  # noqa: E402
from pyVintedVN.requester import Requester  # noqa: E402
from requests.exceptions import HTTPError  # noqa: E402

STATE = {
    "cookies": {"anon_id": "abc123", "datadome": "xyz"},
    "user_agent": "UA-FROM-BROWSER",
    "proxy": None,
    "ts": 1700000000,
}


class BrowserStateTests(base.TempDatabaseTestCase):
    def test_state_is_applied(self):
        db.set_parameter("browser_enabled", "True")
        db.set_parameter("browser_state", json.dumps(STATE))

        requester = Requester()
        self.assertEqual(requester.session.headers["User-Agent"], "UA-FROM-BROWSER")
        self.assertEqual(requester.session.cookies.get("anon_id"), "abc123")

    def test_state_is_ignored_when_disabled(self):
        db.set_parameter("browser_enabled", "False")
        db.set_parameter("browser_state", json.dumps(STATE))

        requester = Requester()
        self.assertNotEqual(requester.session.headers["User-Agent"], "UA-FROM-BROWSER")

    def test_empty_state_is_ignored(self):
        db.set_parameter("browser_enabled", "True")
        db.set_parameter("browser_state", "{}")

        requester = Requester()
        self.assertFalse(requester._apply_browser_state(force=True))
        self.assertIsNone(requester.session.cookies.get("anon_id"))

    def test_invalid_json_is_ignored(self):
        db.set_parameter("browser_enabled", "True")
        db.set_parameter("browser_state", "not json")

        requester = Requester()
        self.assertIsNone(requester._browser_state(force=True))

    def test_locale_keeps_the_browser_user_agent(self):
        db.set_parameter("browser_enabled", "True")
        db.set_parameter("browser_state", json.dumps(STATE))

        requester = Requester()
        requester.set_locale("www.vinted.de")
        self.assertEqual(requester.session.headers["User-Agent"], "UA-FROM-BROWSER")
        self.assertEqual(requester.session.headers["Host"], "www.vinted.de")


class ProxyConversionTests(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(browser_session._proxy_to_playwright(None))

    def test_host_port(self):
        self.assertEqual(
            browser_session._proxy_to_playwright("127.0.0.1:8080"),
            {"server": "http://127.0.0.1:8080"},
        )

    def test_socks(self):
        self.assertEqual(
            browser_session._proxy_to_playwright("socks5://127.0.0.1:1080"),
            {"server": "socks5://127.0.0.1:1080"},
        )

    def test_credentials(self):
        self.assertEqual(
            browser_session._proxy_to_playwright("user:pass@10.0.0.1:3128"),
            {
                "server": "http://10.0.0.1:3128",
                "username": "user",
                "password": "pass",
            },
        )


class FakeResponse:
    status_code = 403
    headers = {}
    text = "blocked"


class GetJsonTests(base.TempDatabaseTestCase):
    def test_browser_fallback_in_full_mode(self):
        db.set_parameter("browser_enabled", "True")
        db.set_parameter("browser_mode", "full")
        requester = Requester()
        requester.get = lambda url, params=None: FakeResponse()

        with mock.patch.object(
            browser_session, "fetch_json", return_value={"items": [1]}
        ) as fetch:
            self.assertEqual(
                requester.get_json("https://api", {"page": 1}), {"items": [1]}
            )
            fetch.assert_called_once_with("https://api", {"page": 1})

    def test_no_fallback_in_essential_mode(self):
        db.set_parameter("browser_enabled", "True")
        db.set_parameter("browser_mode", "essential")
        requester = Requester()
        requester.get = lambda url, params=None: FakeResponse()

        with mock.patch.object(browser_session, "fetch_json") as fetch:
            with self.assertRaises(HTTPError):
                requester.get_json("https://api")
            fetch.assert_not_called()

    def test_no_fallback_when_browser_disabled(self):
        db.set_parameter("browser_enabled", "False")
        db.set_parameter("browser_mode", "full")
        requester = Requester()
        requester.get = lambda url, params=None: FakeResponse()

        with mock.patch.object(browser_session, "fetch_json") as fetch:
            with self.assertRaises(HTTPError):
                requester.get_json("https://api")
            fetch.assert_not_called()


class RecoverSessionTests(base.TempDatabaseTestCase):
    def test_cookies_are_used_when_browser_is_disabled(self):
        db.set_parameter("browser_enabled", "False")
        requester = Requester()

        with mock.patch.object(requester, "set_cookies") as set_cookies:
            self.assertFalse(requester._recover_session(403))
            set_cookies.assert_called_once()

    def test_refreshed_state_is_published_and_picked_up(self):
        db.set_parameter("browser_enabled", "True")
        db.set_parameter("browser_state_ts", "111")
        requester = Requester()
        requester.REFRESH_WAIT_STEPS = 2
        requester.REFRESH_WAIT_INTERVAL = 0

        def fake_sleep(_seconds):
            db.set_parameter("browser_state", json.dumps(STATE))
            db.set_parameter("browser_state_ts", "222")

        with mock.patch.object(browser_session, "is_running", return_value=False):
            with mock.patch("time.sleep", side_effect=fake_sleep):
                self.assertTrue(requester._recover_session(403))

        # The scraper process was asked for a new session ...
        self.assertEqual(db.get_parameter("browser_refresh_requested"), "True")
        # ... and its result is now used
        self.assertEqual(requester.session.headers["User-Agent"], "UA-FROM-BROWSER")


class PriceParsingTests(unittest.TestCase):
    def test_european_format(self):
        self.assertEqual(
            browser_session.parse_price_text("1.234,56\u00a0€"), (1234.56, "EUR")
        )
        self.assertEqual(browser_session.parse_price_text("350,00 €"), (350.0, "EUR"))

    def test_english_format(self):
        self.assertEqual(browser_session.parse_price_text("£12.50"), (12.5, "GBP"))
        self.assertEqual(
            browser_session.parse_price_text("1,234.56 $"), (1234.56, "USD")
        )

    def test_currency_by_domain_when_the_symbol_is_missing(self):
        self.assertEqual(
            browser_session.parse_price_text("25.00", "www.vinted.co.uk"),
            (25.0, "GBP"),
        )

    def test_empty(self):
        self.assertEqual(browser_session.parse_price_text(""), (None, None))


class ItemTitleTests(unittest.TestCase):
    def test_metadata_is_removed(self):
        self.assertEqual(
            browser_session.clean_item_title(
                "Nintendo Switch OLED, Brand: Nintendo, Condizioni: Buone, 350.00 €"
            ),
            "Nintendo Switch OLED",
        )

    def test_plain_title(self):
        self.assertEqual(
            browser_session.clean_item_title("Funda Switch"), "Funda Switch"
        )

    def test_title_from_url(self):
        self.assertEqual(
            browser_session.title_from_url(
                "https://www.vinted.it/items/10833421014-nintendo-switch-oled?x=1"
            ),
            "nintendo switch oled",
        )
        self.assertEqual(browser_session.title_from_url("https://www.vinted.it"), "")


class BuildItemPayloadTests(unittest.TestCase):
    def test_card_becomes_api_payload(self):
        card = {
            "id": "10833421014",
            "brand": "Nintendo",
            "condition": "Buone",
            "price": "350,00\u00a0€",
            "link_title": "Nintendo Switch OLED, Brand: Nintendo, Condizioni: Buone",
            "alt": "",
            "href": "/items/10833421014-nintendo-switch-oled?referrer=catalog",
            "photo": "https://images1.vinted.net/abc.webp",
        }
        payload = browser_session.build_item_payload(
            card, now=int(time.time()), domain="www.vinted.it"
        )

        self.assertEqual(payload["id"], 10833421014)
        self.assertEqual(payload["title"], "Nintendo Switch OLED")
        self.assertEqual(payload["brand_title"], "Nintendo")
        self.assertEqual(payload["price"], {"amount": 350.0, "currency_code": "EUR"})
        self.assertEqual(payload["photo"]["url"], "https://images1.vinted.net/abc.webp")
        self.assertLessEqual(payload["created_at_ts"], int(time.time()))

        from pyVintedVN.items.item import Item

        item = Item(payload)
        self.assertTrue(item.is_valid)
        self.assertTrue(item.is_new_item())
        self.assertEqual(
            item.url, "https://www.vinted.it/items/10833421014-nintendo-switch-oled"
        )

    def test_title_is_not_used_as_brand(self):
        card = {
            "id": "2",
            "href": "/items/2-jeu-fifa-21",
            "price": "5,00 €",
            "link_title": "Jeu FIFA 21, Brand: EA, Condizioni: Buone",
            "brand": "Jeu FIFA 21",
        }
        payload = browser_session.build_item_payload(card)
        self.assertEqual(payload["title"], "Jeu FIFA 21")
        self.assertEqual(payload["brand_title"], "")

    def test_title_falls_back_to_the_slug(self):
        card = {
            "id": "1",
            "href": "/items/1-some-item-name",
            "price": "10,00 €",
            "link_title": "",
            "alt": "",
            "brand": "",
        }
        payload = browser_session.build_item_payload(card)
        self.assertEqual(payload["title"], "some item name")


if __name__ == "__main__":
    unittest.main()
