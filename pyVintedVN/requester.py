import json
import proxies
import sys
import os
import db
import random
import time
import requests
from requests.exceptions import HTTPError, RequestException

# Add the parent directory to sys.path to import logger
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import get_logger

# Get logger for this module
logger = get_logger(__name__)


class Requester:
    """
    A class for handling HTTP requests to Vinted.

    This class manages session headers, cookies, and provides methods for making
    HTTP requests with retry logic for handling authentication issues.

    When the Patchright browser session is enabled the requester reuses the
    cookies, the User-Agent and the proxy produced by the browser, so that the
    Vinted anti-bot sees a coherent client.
    """

    # (connect, read) timeout in seconds
    TIMEOUT = (10, 30)
    MAX_RETRIES = 3
    # How long the browser state read from the database is cached in memory
    STATE_CACHE_TTL = 15
    # How long we wait for another process to refresh the browser state
    REFRESH_WAIT_STEPS = 4
    REFRESH_WAIT_INTERVAL = 2

    def __init__(self, debug=False):
        """
        Initialize the Requester with default headers and session.

        Sets up the request headers with a randomly selected User-Agent,
        initializes the session, and configures default settings.

        Args:
            debug (bool, optional): Whether to print debug messages. Defaults to False.
        """

        self.VINTED_AUTH_URL = "https://www.vinted.fr/"
        self.session = requests.Session()
        self.debug = debug
        self._state = None
        self._state_ts = 0

        self.session.headers.update(self._build_headers("www.vinted.fr"))
        self._apply_browser_state(force=True)

        if self.debug:
            logger.debug(f"Using User-Agent: {self.session.headers['User-Agent']}")

    def _build_headers(self, locale):
        """
        Build the default headers for a locale.

        Args:
            locale (str): The locale domain to use (e.g., 'www.vinted.fr')

        Returns:
            dict: The headers to use
        """
        # Get user agents and default headers from the database
        user_agents_json = db.get_parameter("user_agents")
        default_headers_json = db.get_parameter("default_headers")

        # Parse JSON strings
        user_agents = json.loads(user_agents_json) if user_agents_json else []
        default_headers = (
            json.loads(default_headers_json) if default_headers_json else {}
        )

        return {
            # Grabs a user agent from the database
            "User-Agent": random.choice(user_agents) if user_agents else "Mozilla/5.0",
            **(default_headers or {}),
            "Host": locale,
        }

    def _browser_state(self, force=False):
        """
        Read the browser session state published by the scraper process.

        Args:
            force (bool, optional): Ignore the in-memory cache

        Returns:
            dict | None: The state, or None when there is no usable state
        """
        now = time.time()
        if (
            not force
            and self._state is not None
            and (now - self._state_ts) < self.STATE_CACHE_TTL
        ):
            return self._state

        raw = db.get_parameter("browser_state")
        state = None
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed.get("cookies"):
                    state = parsed
            except (TypeError, ValueError):
                logger.debug("Invalid browser state in database", exc_info=True)

        self._state = state
        self._state_ts = now
        return state

    def _apply_browser_state(self, force=False):
        """
        Reuse the cookies and the User-Agent of the browser session.

        Args:
            force (bool, optional): Ignore the in-memory cache

        Returns:
            bool: True if a browser state was applied
        """
        if db.get_parameter("browser_enabled") != "True":
            return False

        state = self._browser_state(force)
        if not state:
            return False

        self.session.cookies.update(state.get("cookies") or {})
        user_agent = state.get("user_agent")
        if user_agent:
            self.session.headers["User-Agent"] = user_agent
        return True

    def _configure_proxy(self):
        """
        Configure a proxy for the session.

        Returns:
            bool: True if a proxy was configured
        """
        if db.get_parameter("browser_enabled") == "True":
            state = self._browser_state()
            if state and state.get("proxy"):
                # Keep the same exit IP the cookies were issued for
                return proxies.configure_proxy(self.session, state["proxy"])
        return proxies.configure_proxy(self.session)

    def set_locale(self, locale):
        """
        Set the locale of the requester.

        Updates the authentication URL and headers to use the specified locale.

        Args:
            locale (str): The locale domain to use (e.g., 'www.vinted.fr', 'www.vinted.de')
        """
        self.VINTED_AUTH_URL = f"https://{locale}/"
        headers = self._build_headers(locale)
        self.session.headers.update(headers)
        # The browser state always wins over the random user agent pool
        self._apply_browser_state(force=True)
        if self.debug:
            logger.debug(
                f"Locale set to {locale} with User-Agent: {self.session.headers['User-Agent']}"
            )

    def _recover_session(self, status_code):
        """
        Try to get a valid session again after a 401/403/429.

        Args:
            status_code (int): The status code that triggered the recovery

        Returns:
            bool: True if the session was renewed
        """
        if db.get_parameter("browser_enabled") == "True":
            import browser_session

            if browser_session.is_running():
                if browser_session.ensure_session(force=True):
                    self._apply_browser_state(force=True)
                    return True
            else:
                # Ask the process that owns the browser to renew the state
                previous_ts = db.get_parameter("browser_state_ts")
                db.set_parameter("browser_refresh_requested", "True")
                for _ in range(self.REFRESH_WAIT_STEPS):
                    time.sleep(self.REFRESH_WAIT_INTERVAL)
                    if db.get_parameter("browser_state_ts") != previous_ts:
                        self._apply_browser_state(force=True)
                        return True

        self.set_cookies()
        return False

    def _browser_fetch_json(self, url, params=None):
        """
        Use the browser context as a last resort for a blocked request.

        Returns:
            dict | None: Parsed JSON body
        """
        if db.get_parameter("browser_enabled") != "True":
            return None
        if db.get_parameter("browser_mode") != "full":
            return None

        import browser_session

        return browser_session.fetch_json(url, params)

    def get(self, url, params=None):
        """
        Make a GET request with retry logic.

        If a 401/403/429 status code is received, it will attempt to refresh
        the browser session (or the cookies) and retry the request up to
        MAX_RETRIES times.

        Args:
            url (str): The URL to request
            params (dict, optional): Query parameters for the request

        Returns:
            requests.Response: The response object if successful

        Raises:
            HTTPError: If the request fails after all retries
        """
        proxy_configured = self._configure_proxy()
        if self.debug and proxy_configured:
            logger.debug(f"Using proxy: {self.session.proxies}")

        locale = self._locale_host()
        tried = 0
        new_session = False
        while tried < self.MAX_RETRIES:
            tried += 1
            try:
                response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            except RequestException as exc:
                if tried >= self.MAX_RETRIES:
                    raise
                logger.warning(f"Retry {tried}/{self.MAX_RETRIES}: {exc}")
                time.sleep(tried)
                continue

            if response.status_code == 200:
                return response

            if response.status_code in (401, 403, 429) and tried < self.MAX_RETRIES:
                logger.warning(
                    f"Received {response.status_code} for {url}, refreshing session "
                    f"({tried}/{self.MAX_RETRIES})"
                )
                self._recover_session(response.status_code)
                continue

            if tried == self.MAX_RETRIES:
                # If we've reached max retries, return the last response
                # even if it's not a 200 status code
                if response.status_code in (401, 403) and not new_session:
                    logger.error(
                        f"Received {response.status_code} error for URL: {url}\n"
                        f"Response headers: {dict(response.headers)}\n"
                        f"Response body (first 500 chars): {response.text[:500]}"
                    )
                    new_session = True
                    self.session = requests.Session()
                    self.session.headers.update(self._build_headers(locale))
                    self._apply_browser_state(force=True)
                    self._configure_proxy()
                    tried = 0
                    continue
                return response

        # This should only happen if the loop exits without returning
        raise HTTPError(
            f"Failed to get a valid response after {self.MAX_RETRIES} attempts"
        )

    def get_json(self, url, params=None):
        """
        Make a GET request and return the parsed JSON body.

        Falls back to the browser context when HTTP requests keep being blocked
        and the browser is configured in "full" mode.

        Args:
            url (str): The URL to request
            params (dict, optional): Query parameters for the request

        Returns:
            dict: The parsed JSON body

        Raises:
            HTTPError: If the request fails
        """
        response = self.get(url=url, params=params)
        if response.status_code == 200:
            return response.json()

        data = self._browser_fetch_json(url, params)
        if data is not None:
            return data

        raise HTTPError(
            f"Failed to fetch {url} (status {response.status_code})",
            response=response,
        )

    def _locale_host(self):
        return self.session.headers.get("Host", "www.vinted.fr")

    def post(self, url, params=None):
        """
        Make a POST request.

        Args:
            url (str): The URL to request
            params (dict, optional): Parameters for the request

        Returns:
            requests.Response: The response object if successful

        Raises:
            HTTPError: If the request fails
        """
        proxy_configured = self._configure_proxy()
        if self.debug and proxy_configured:
            logger.debug(f"Using proxy: {self.session.proxies}")

        response = self.session.post(url, params, timeout=self.TIMEOUT)
        response.raise_for_status()
        return response

    def set_cookies(self):
        """
        Reset and fetch new cookies for authentication.

        Clears the current session cookies and makes a HEAD request to
        the Vinted authentication URL to get new cookies.
        """
        self.session.cookies.clear_session_cookies()
        try:
            self.session.head(self.VINTED_AUTH_URL, timeout=self.TIMEOUT)
            if self.debug:
                logger.debug("Cookies set!")
        except Exception:
            if self.debug:
                logger.error(
                    "There was an error fetching cookies for vinted", exc_info=True
                )

    def update_cookies(self, cookies: dict):
        """
        Update the session cookies with the provided dictionary.

        Args:
            cookies (dict): Dictionary of cookies to update
        """
        self.session.cookies.update(cookies)
        if self.debug:
            logger.debug(f"Cookies manually updated ({len(cookies)} cookies received)")

    # Alias for backward compatibility
    setLocale = set_locale
    setCookies = set_cookies


# Singleton instance of the Requester class
requester = Requester()
