"""
Patchright based browser session.

The browser is used to obtain a *real* set of cookies (and a coherent
User-Agent/proxy) for Vinted, and optionally to perform the API calls from
inside the browser context when plain HTTP requests get blocked.

Only the process that owns the browser may start it (the scraper process):
the resulting state is published in the database so that every other process
can simply reuse it.

Patchright is imported lazily: if it is not installed the application keeps
working with the plain requests based requester.
"""

import json
import os
import re
import threading
import time

import db
import proxies
from logger import get_logger

logger = get_logger(__name__)

STATE_PATH = "./data/browser_state.json"
DEFAULT_DOMAIN = "www.vinted.fr"
DEFAULT_TTL = 1800
RETRY_DELAY = 900
NAVIGATION_TIMEOUT = 45000
RESULTS_TIMEOUT = 15000

# The catalog is server side rendered: the JSON API is blocked (403) but the
# item boxes are in the HTML.
CARD_SELECTOR = '[data-testid^="product-item-id-"]'
EXTRACT_ITEMS_JS = """
(limit) => {
  const cards = [...document.querySelectorAll('[data-testid]')].filter((el) =>
    /^product-item-id-\\d+$/.test(el.getAttribute('data-testid'))
  );
  const text = (card, suffix) => {
    const el = card.querySelector(`[data-testid$="${suffix}"]`);
    return el ? (el.textContent || '').trim() : '';
  };
  return cards.slice(0, limit).map((card) => {
    const link =
      card.querySelector('a[data-testid$="--overlay-link"]') ||
      card.querySelector('a[href*="/items/"]');
    const img = card.querySelector('img');
    return {
      id: card.getAttribute('data-testid').replace('product-item-id-', ''),
      // The description block holds the brand, not the item title
      brand: text(card, '--description-title'),
      condition: text(card, '--description-subtitle'),
      price: text(card, '--price-text'),
      link_title: link ? (link.getAttribute('title') || '').trim() : '',
      alt: img ? (img.getAttribute('alt') || '').trim() : '',
      href: link ? link.getAttribute('href') : '',
      photo: img ? img.getAttribute('src') : '',
    };
  });
}
"""

# Metadata appended to the card link title / image alt, e.g.
# "Nintendo Switch OLED, Brand: Nintendo, Condizioni: Buone, 350.00 €"
CARD_METADATA_MARKERS = (
    "Brand:",
    "Condizioni:",
    "Condizione:",
    "Condition:",
    "Estado:",
    "Zustand:",
    "État:",
    "Staat:",
    "Stan:",
    "Stav:",
    "Tilstand:",
    "Kunto:",
    "Skick:",
)

# Vinted shows the price with the symbol of the domain currency
CURRENCY_SYMBOLS = {
    "€": "EUR",
    "£": "GBP",
    "$": "USD",
    "zł": "PLN",
    "Kč": "CZK",
    "Ft": "HUF",
    "lei": "RON",
    "kr": "SEK",
}
CURRENCY_BY_DOMAIN = {
    "vinted.co.uk": "GBP",
    "vinted.pl": "PLN",
    "vinted.cz": "CZK",
    "vinted.hu": "HUF",
    "vinted.ro": "RON",
    "vinted.se": "SEK",
    "vinted.lt": "EUR",
}


def parse_price_text(value, domain=""):
    """
    Parse the price shown on a catalog card.

    Args:
        value (str): e.g. "350,00 €", "1.234,56 €", "12.5 zł"
        domain (str, optional): The Vinted domain, used to guess the currency

    Returns:
        tuple: (amount as float or None, currency code or None)
    """
    if not value:
        return None, None

    text = value.replace("\xa0", " ").strip()
    currency = None
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            break
    if currency is None:
        for suffix, code in CURRENCY_BY_DOMAIN.items():
            if domain.endswith(suffix):
                currency = code
                break

    match = re.search(r"\d[\d.,\s]*", text)
    if not match:
        return None, currency

    number = match.group(0).replace(" ", "").strip(".,")
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number:
        decimals = number.split(",")[-1]
        number = (
            number.replace(",", ".") if len(decimals) <= 2 else number.replace(",", "")
        )

    try:
        return float(number), currency
    except ValueError:
        return None, currency


def clean_item_title(value):
    """
    Remove the metadata the catalog card appends to the item title.

    Args:
        value (str): The link title or the image alt, e.g.
            "Nintendo Switch OLED, Brand: Nintendo, Condizioni: Buone, 350.00 €"

    Returns:
        str: The item title only
    """
    title = (value or "").strip()
    if not title:
        return ""
    for marker in CARD_METADATA_MARKERS:
        index = title.find(f", {marker}")
        if index > 0:
            return title[:index].strip()
    return title


def title_from_url(url):
    """Build a readable title from the item slug, as a last resort."""
    match = re.search(r"/items/(\d+)-([^/?#]+)", url or "")
    if not match:
        return ""
    slug = re.sub(r"-+", " ", match.group(2)).strip()
    return slug[:100]


def build_item_payload(card, now=None, domain=None):
    """
    Convert a scraped catalog card into a Vinted-API shaped payload.

    The API payload is not available anymore, so the timestamp is the moment
    the item was first seen: it keeps the "only notify new items" logic
    working, while the item id in the database prevents duplicates.

    Args:
        card (dict): The data extracted from the DOM
        now (int, optional): Override the timestamp, for testing
        domain (str, optional): Domain of the search page, for relative links

    Returns:
        dict: A payload accepted by pyVintedVN.items.item.Item
    """
    domain = domain or DEFAULT_DOMAIN
    href = card.get("href") or ""
    if href.startswith("/"):
        href = f"https://{domain}{href}"
    url = href.split("?")[0]

    item_domain = ""
    match = re.match(r"https://([^/]+)", url)
    if match:
        item_domain = match.group(1)

    amount, currency = parse_price_text(card.get("price"), item_domain)
    title = clean_item_title(card.get("link_title") or card.get("alt"))
    if not title:
        title = title_from_url(url) or clean_item_title(card.get("brand"))
    # The description slot holds the brand, but when the seller did not set one
    # Vinted puts the title there: do not report the title as a brand.
    brand = (card.get("brand") or "").strip()
    if not brand or brand.lower() == title.lower() or len(brand) > 40:
        brand = ""

    return {
        "id": int(card["id"]) if str(card.get("id", "")).isdigit() else card.get("id"),
        "title": title,
        "brand_title": brand,
        "url": url,
        "price": {"amount": amount, "currency_code": currency or "EUR"},
        "photo": {"url": card.get("photo")},
        "created_at_ts": int(now if now is not None else time.time()),
    }


def _proxy_to_playwright(proxy):
    """
    Convert a proxy string into the dict expected by Playwright/Patchright.

    Args:
        proxy (str): Proxy in the form "[scheme://][user:pass@]host:port"

    Returns:
        dict | None: Playwright proxy configuration
    """
    if not proxy:
        return None

    scheme = "http"
    value = proxy
    if "://" in value:
        scheme, value = value.split("://", 1)

    if "@" in value:
        auth, host = value.rsplit("@", 1)
        username, _, password = auth.partition(":")
        return {
            "server": f"{scheme}://{host}",
            "username": username,
            "password": password,
        }

    return {"server": f"{scheme}://{value}"}


class BrowserSession:
    """Owns a single persistent Chromium context driven by Patchright."""

    def __init__(self):
        self._playwright = None
        self._context = None
        self._lock = threading.Lock()
        self._domain = DEFAULT_DOMAIN
        self._proxy = None

    def is_running(self):
        return self._context is not None

    def start(self):
        """
        Launch the persistent browser context.

        Returns:
            bool: True if the browser is running
        """
        from patchright.sync_api import sync_playwright

        user_data_dir = (
            db.get_parameter("browser_user_data_dir") or "./data/browser_profile"
        )
        os.makedirs(user_data_dir, exist_ok=True)

        launch_args = ["--disable-dev-shm-usage"]
        if db.get_parameter("browser_no_sandbox") == "True":
            launch_args.append("--no-sandbox")

        options = {
            "user_data_dir": user_data_dir,
            "headless": db.get_parameter("browser_headless") != "False",
            "no_viewport": True,
            "args": launch_args,
        }
        channel = db.get_parameter("browser_channel")
        if channel:
            options["channel"] = channel

        proxy = _proxy_to_playwright(proxies.get_random_proxy())
        if proxy:
            options["proxy"] = proxy
            self._proxy = options["proxy"]["server"]
        else:
            self._proxy = None

        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                **options
            )
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise

        logger.info("Browser session started")
        return True

    def stop(self):
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            logger.debug("Error closing browser context", exc_info=True)
        finally:
            self._context = None
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            logger.debug("Error stopping patchright", exc_info=True)
        finally:
            self._playwright = None

    def refresh(self, domain=None):
        """
        Visit Vinted with the browser and publish the resulting cookies.

        Args:
            domain (str, optional): The Vinted domain to visit

        Returns:
            bool: True if the state was refreshed
        """
        with self._lock:
            domain = domain or db.get_parameter("browser_domain") or DEFAULT_DOMAIN

            page = None
            try:
                if not self.is_running():
                    self.start()
                db.set_parameter("browser_status", "refreshing")
                page = (
                    self._context.pages[0]
                    if self._context.pages
                    else self._context.new_page()
                )
                page.goto(
                    f"https://{domain}/",
                    wait_until="domcontentloaded",
                    timeout=NAVIGATION_TIMEOUT,
                )
                # Give the anti-bot/consent flow a moment to settle
                page.wait_for_timeout(2500)
                cookies = {
                    cookie["name"]: cookie["value"]
                    for cookie in self._context.cookies()
                }
                user_agent = page.evaluate("navigator.userAgent")
                self._publish_state(domain, cookies, user_agent)
                logger.info(
                    f"Browser session refreshed for {domain} ({len(cookies)} cookies)"
                )
                return True
            except Exception as exc:
                message = str(exc)[:300]
                logger.error(f"Browser session refresh failed: {message}")
                db.set_parameter("browser_status", "error")
                db.set_parameter("browser_last_error", message)
                db.set_parameter(
                    "browser_retry_after", str(int(time.time()) + RETRY_DELAY)
                )
                self.stop()
                return False
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        logger.debug("Error closing page", exc_info=True)

    def fetch_json(self, url, params=None):
        """
        Perform a GET request from inside the browser context.

        Args:
            url (str): Absolute URL
            params (dict, optional): Query parameters

        Returns:
            dict | None: Parsed JSON body, or None when unavailable
        """
        if not self.is_running():
            return None
        try:
            response = self._context.request.get(
                url, params=params, timeout=NAVIGATION_TIMEOUT
            )
            if response.status != 200:
                logger.warning(f"Browser request to {url} returned {response.status}")
                return None
            return response.json()
        except Exception as exc:
            logger.error(f"Browser request failed: {str(exc)[:300]}")
            return None

    def search_items(self, search_url, limit=20):
        """
        Read the search results from the rendered catalog page.

        The JSON API is blocked by Vinted, but the catalog is rendered server
        side, so the items can be read from the DOM.

        Args:
            search_url (str): The Vinted search URL
            limit (int, optional): Maximum number of items

        Returns:
            list: Vinted-API shaped payloads
        """
        with self._lock:
            page = None
            try:
                if not self.is_running():
                    self.start()
                page = (
                    self._context.pages[0]
                    if self._context.pages
                    else self._context.new_page()
                )
                page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=NAVIGATION_TIMEOUT,
                )
                page.wait_for_selector(CARD_SELECTOR, timeout=RESULTS_TIMEOUT)
                cards = page.evaluate(EXTRACT_ITEMS_JS, limit)
            except Exception as exc:
                logger.warning(f"Could not read the results of {search_url}: {exc}")
                return []
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        logger.debug("Error closing page", exc_info=True)

        now = int(time.time())
        domain = urlparse_domain(search_url)
        payloads = []
        for card in cards:
            payload = build_item_payload(card, now=now, domain=domain)
            if payload["id"] and payload["url"]:
                payloads.append(payload)
        logger.info(f"Browser scraped {len(payloads)} items from {search_url}")
        return payloads

    def _publish_state(self, domain, cookies, user_agent):
        """Store the session state in the database (and on disk for debugging)."""
        state = {
            "domain": domain,
            "cookies": cookies,
            "user_agent": user_agent,
            "proxy": self._proxy,
            "ts": int(time.time()),
        }
        serialized = json.dumps(state)
        db.set_parameter("browser_state", serialized)
        db.set_parameter("browser_state_ts", str(state["ts"]))
        db.set_parameter("browser_status", "ok")
        db.set_parameter("browser_last_error", "")
        db.set_parameter("browser_retry_after", "0")
        db.set_parameter("browser_refresh_requested", "False")
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as state_file:
                json.dump(state, state_file, indent=2)
        except OSError:
            logger.debug("Could not write the browser state file", exc_info=True)


_session = None
_session_lock = threading.Lock()


def urlparse_domain(url):
    """Return the host of a URL, or None."""
    match = re.match(r"https?://([^/]+)", url or "")
    return match.group(1) if match else None


def get_session():
    """Return the process local browser session (creating it on first use)."""
    global _session
    with _session_lock:
        if _session is None:
            _session = BrowserSession()
        return _session


def is_running():
    return _session is not None and _session.is_running()


def ensure_session(force=False, domain=None):
    """
    Start the browser and refresh the session when needed.

    Called by the scraper process before each cycle. It is a no-op when the
    browser is disabled, the state is still fresh, or a previous failure is
    still cooling down.

    Args:
        force (bool, optional): Ignore TTL and refresh request flags
        domain (str, optional): Vinted domain to visit (e.g. "www.vinted.it")

    Returns:
        bool: True if a valid session state is available
    """
    if db.get_parameter("browser_enabled") != "True":
        return False

    try:
        retry_after = int(db.get_parameter("browser_retry_after") or 0)
    except ValueError:
        retry_after = 0
    if not force and retry_after > time.time():
        return False

    try:
        ttl = int(db.get_parameter("browser_ttl") or DEFAULT_TTL)
    except ValueError:
        ttl = DEFAULT_TTL

    try:
        state_ts = int(db.get_parameter("browser_state_ts") or 0)
    except ValueError:
        state_ts = 0

    refresh_requested = db.get_parameter("browser_refresh_requested") == "True"
    is_fresh = (time.time() - state_ts) < ttl

    session = get_session()
    if is_fresh and not refresh_requested and state_ts > 0:
        return True

    return session.refresh(domain=domain)


def fetch_json(url, params=None):
    """
    Perform a GET request through the browser, when it is available.

    Returns:
        dict | None: Parsed JSON body
    """
    if _session is None or not _session.is_running():
        return None
    return _session.fetch_json(url, params)


def search_items(search_url, limit=20):
    """
    Scrape a Vinted search page with the browser.

    Args:
        search_url (str): The Vinted search URL
        limit (int, optional): Maximum number of items

    Returns:
        list: Vinted-API shaped payloads, empty when the browser is disabled
    """
    if db.get_parameter("browser_enabled") != "True":
        return []

    session = get_session()
    if not session.is_running():
        domain = urlparse_domain(search_url)
        if not session.refresh(domain=domain):
            return []
    return session.search_items(search_url, limit)
