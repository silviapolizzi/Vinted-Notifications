import datetime as dt
import html
import random
import re
import time

import browser_session
import db
import requests
from pyVintedVN import Vinted, requester
from requests.exceptions import HTTPError
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from logger import get_logger

# Get logger for this module
logger = get_logger(__name__)

DEFAULT_MESSAGE_TEMPLATE = "🆕 Title : {title}\n💶 Price : {price}\n🛍️ Brand : {brand}"
DEFAULT_CAPTION_TEMPLATE = "🆕 {title}\n💶 {price}\n🛍️ {brand}"
# Maximum number of seller country lookups per scraping cycle
MAX_COUNTRY_LOOKUPS = 5
# Quiet hours window, e.g. "23:00-07:00"
QUIET_SPEC_PATTERN = re.compile(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})")


def format_price(price, currency):
    """
    Format an item price for a notification.

    Args:
        price (float | str | None): The raw price
        currency (str | None): The currency code

    Returns:
        str: The formatted price, or "N/D" when it is not available
    """
    if price is None or price == "":
        return "N/D"
    try:
        value = f"{float(price):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        value = str(price)
    currency = currency or ""
    return f"{value} {currency}".strip()


def format_item_message(template, item):
    """
    Render a notification template for an item.

    Values are HTML escaped because the message is sent with parse_mode="HTML":
    an unescaped "&" or "<" in a title makes Telegram reject the message.

    Args:
        template (str): The template with {title}, {price}, {brand}, {image}
        item (Item): The item to format

    Returns:
        str: The rendered message
    """
    fields = {
        "title": html.escape(str(item.title or "")),
        "price": html.escape(format_price(item.price, item.currency)),
        "brand": html.escape(str(item.brand_title or "")),
        "image": html.escape(item.photo or "", quote=True),
    }
    try:
        return template.format(**fields).strip()
    except (KeyError, IndexError, ValueError):
        logger.warning("Invalid message template, using the default one")
        return DEFAULT_MESSAGE_TEMPLATE.format(**fields).strip()


def split_message(text, limit=4096):
    """
    Split a message so that it fits the Telegram length limit.

    Args:
        text (str): The message to split
        limit (int, optional): Maximum length of each part

    Returns:
        list: The message parts
    """
    if len(text) <= limit:
        return [text]

    parts = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            parts.append(current)
            current = line
        else:
            current += line
    if current:
        parts.append(current)
    return [part for part in parts if part]


def truncate_html(text, limit=1024):
    """
    Truncate an HTML formatted text without leaving a broken entity.

    Args:
        text (str): The text to truncate
        limit (int, optional): Maximum length

    Returns:
        str: The truncated text
    """
    if len(text) <= limit:
        return text
    truncated = text[: limit - 3]
    # Drop a possibly incomplete HTML entity at the end
    truncated = re.sub(r"&[a-zA-Z#0-9]*$", "", truncated)
    return truncated.rstrip() + "..."


def is_valid_quiet_spec(spec):
    """
    Check that a quiet hours spec looks like "HH:MM-HH:MM".

    Args:
        spec (str): The spec to validate

    Returns:
        bool: True if the spec is well formed
    """
    if not spec:
        return False
    return bool(QUIET_SPEC_PATTERN.fullmatch(spec.strip()))


def is_quiet_time(spec, now=None):
    """
    Check if the current time is inside a quiet hours window.

    Args:
        spec (str): "HH:MM-HH:MM", empty or "off" for no quiet hours
        now (datetime.datetime, optional): The time to check, for testing

    Returns:
        bool: True if notifications must stay queued
    """
    if not spec or spec.strip().lower() in ("off", "none"):
        return False

    if not is_valid_quiet_spec(spec):
        return False

    start_hour, start_minute, end_hour, end_minute = (
        int(group) for group in QUIET_SPEC_PATTERN.fullmatch(spec.strip()).groups()
    )
    start = (start_hour % 24) * 60 + (start_minute % 60)
    end = (end_hour % 24) * 60 + (end_minute % 60)
    if start == end:
        return False

    current = now or dt.datetime.now()
    current_minutes = current.hour * 60 + current.minute
    if start < end:
        return start <= current_minutes < end
    return current_minutes >= start or current_minutes < end


def process_query(query, name=None):
    """
    Process a Vinted query URL by:
    1. Checking if the URL is a brand URL and converting it to standard format if needed
    2. Parsing the URL and extracting query parameters
    3. Ensuring the order flag is set to "newest_first"
    4. Removing time and search_id parameters
    5. Rebuilding the query string and URL
    6. Checking if the query already exists in the database
    7. Adding the query to the database if it doesn't exist

    Args:
        query (str): The Vinted query URL
        name (str, optional): A name for the query. If provided, it will be used as the query name.

    Returns:
        tuple: (message, is_new_query)
            - message (str): Status message
            - is_new_query (bool): True if query was added, False if it already existed
    """
    # Check if the URL is a brand URL (format: url/brand/id-name)
    parsed_url = urlparse(query)
    path_parts = parsed_url.path.strip("/").split("/")

    if len(path_parts) >= 2 and path_parts[0] == "brand":
        # Extract the brand ID from the format "id-name"
        brand_id_with_name = path_parts[1]
        brand_id = brand_id_with_name.split("-")[0]

        # Create a new URL with the standard format
        new_path = "/catalog"
        new_query_params = {"brand_ids[]": [brand_id]}
        new_query_string = urlencode(new_query_params, doseq=True)

        # Rebuild the URL
        query = urlunparse(
            (parsed_url.scheme, parsed_url.netloc, new_path, "", new_query_string, "")
        )
        logger.info(f"Converted brand URL to standard format: {query}")

        # Parse the URL and extract the query parameters
        parsed_url = urlparse(query)

    query_params = parse_qs(parsed_url.query)

    # Ensure the order flag is set to newest_first
    query_params["order"] = ["newest_first"]
    # Remove time and search_id if provided
    query_params.pop("time", None)
    query_params.pop("search_id", None)
    query_params.pop("disabled_personalization", None)
    query_params.pop("page", None)

    # Rebuild the query string and the entire URL
    new_query = urlencode(query_params, doseq=True)
    processed_query = urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            new_query,
            parsed_url.fragment,
        )
    )

    # Some queries are made with filters only, so we need to check if the search_text is present
    if db.is_query_in_db(processed_query) is True:
        return "Query already exists.", False
    else:
        # add the query to the db
        db.add_query_to_db(processed_query, name)
        return "Query added.", True


def get_formatted_query_list():
    """
    Get a formatted list of all queries in the database.

    Returns:
        str: A formatted string with all queries, numbered
    """
    all_queries = db.get_queries()
    queries_keywords = []
    for query in all_queries:
        parsed_url = urlparse(query[1])
        query_params = parse_qs(parsed_url.query)

        # Get the name or Extract the value of 'search_text'
        query_name = query[3] or query_params.get("search_text", [None])[0] or query[1]

        queries_keywords.append(query_name)

    query_list = ("\n").join(
        [str(i + 1) + ". " + j for i, j in enumerate(queries_keywords)]
    )
    return query_list


def process_remove_query(number):
    """
    Process the removal of a query from the database.

    Args:
        number (str): The number of the query to remove or "all" to remove all queries

    Returns:
        tuple: (message, success)
            - message (str): Status message
            - success (bool): True if query was removed successfully
    """
    if number == "all":
        db.remove_all_queries_from_db()
        return "All queries removed.", True

    # Check if number is a valid digit
    if number.isdigit():
        # Remove the query from the database
        db.remove_query_from_db(number)
        return "Query removed.", True
    else:
        return "Invalid number.", False


def process_update_query(query_id, query, name):
    """
    Process the update of a query in the database.

    Args:
        query_id (int): The ID of the query to update
        query (str): The new Vinted query URL
        name (str, optional): A new name for the query. If provided, it will be used as the query name.

    Returns:
        tuple: (message, success)
            - message (str): Status message
            - success (bool): True if query was updated successfully
    """
    # Parse the URL and extract the query parameters
    parsed_url = urlparse(query)
    query_params = parse_qs(parsed_url.query)

    # Ensure the order flag is set to newest_first
    query_params["order"] = ["newest_first"]
    # Remove time and search_id if provided
    query_params.pop("time", None)
    query_params.pop("search_id", None)
    query_params.pop("disabled_personalization", None)
    query_params.pop("page", None)

    # Rebuild the query string and the entire URL
    new_query = urlencode(query_params, doseq=True)
    processed_query = urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            new_query,
            parsed_url.fragment,
        )
    )

    # Update the query in the database
    if db.update_query_in_db(query_id, processed_query, name):
        return "Query updated.", True
    else:
        return "Failed to update query.", False


def process_add_country(country):
    """
    Process the addition of a country to the allowlist.

    Args:
        country (str): The country code to add

    Returns:
        tuple: (message, country_list)
            - message (str): Status message
            - country_list (list): Current list of allowed countries
    """
    # Format the country code (remove spaces)
    country = country.replace(" ", "")
    country_list = db.get_allowlist()

    # Validate the country code (check if it's 2 characters long)
    if len(country) != 2:
        return "Invalid country code", country_list

    # Check if the country is already in the allowlist
    # If country_list is 0, it means the allowlist is empty
    if country_list != 0 and country.upper() in country_list:
        return f'Country "{country.upper()}" already in allowlist.', country_list

    # Add the country to the allowlist
    db.add_to_allowlist(country.upper())
    return "Country added.", db.get_allowlist()


def process_remove_country(country):
    """
    Process the removal of a country from the allowlist.

    Args:
        country (str): The country code to remove

    Returns:
        tuple: (message, country_list)
            - message (str): Status message
            - country_list (list): Current list of allowed countries
    """
    # Format the country code (remove spaces)
    country = country.replace(" ", "")

    # Validate the country code (check if it's 2 characters long)
    if len(country) != 2:
        return "Invalid country code", db.get_allowlist()

    # Remove the country from the allowlist
    db.remove_from_allowlist(country.upper())
    return "Country removed.", db.get_allowlist()


def get_user_country(profile_id):
    """
    Get the country code for a Vinted user.

    Makes an API request to retrieve the user's country code.
    Handles rate limiting by trying an alternative endpoint.
    Results are cached in the database: with an allowlist enabled this method
    was previously called once per item, which is the main cause of 429s.

    Args:
        profile_id (str): The Vinted user's profile ID

    Returns:
        str: The user's country code (2-letter ISO code) or "XX" if it can't be determined
    """
    cached_country = db.get_cached_user_country(profile_id)
    if cached_country:
        return cached_country

    # Users are shared between all Vinted platforms, so we can use whatever locale we want
    url = f"https://www.vinted.fr/api/v2/users/{profile_id}?localize=false"
    response = requester.get(url)
    # That's a LOT of requests, so if we get a 429 we wait a bit before retrying once
    if response.status_code == 429:
        # In case of rate limit, we're switching the endpoint. This one is slower, but it doesn't RL as soon.
        # We're limiting the items per page to 1 to grab as little data as possible
        url = f"https://www.vinted.fr/api/v2/users/{profile_id}/items?page=1&per_page=1"
        response = requester.get(url)
        try:
            user_country = response.json()["items"][0]["user"]["country_iso_code"]
        except (KeyError, IndexError):
            logger.warning(
                "Couldn't get the country due to too many requests. Returning default value."
            )
            user_country = "XX"
    else:
        try:
            user_country = response.json()["user"]["country_iso_code"]
        except (KeyError, ValueError):
            user_country = "XX"

    if user_country != "XX":
        db.set_cached_user_country(profile_id, user_country)
    return user_country


def resolve_seller_country(item):
    """
    Get the seller country of an item without any blocking HTTP call.

    Items without a country are treated as "XX" (allowed): the cache is warmed
    up by the scraper process, which owns the browser session.

    Args:
        item (Item): The item

    Returns:
        str: The ISO country code or "XX"
    """
    if item.seller_country:
        return item.seller_country

    profile_id = (item.raw_data.get("user") or {}).get("id")
    if profile_id is None:
        return "XX"
    return db.get_cached_user_country(profile_id) or "XX"


def process_items(queue):
    """
    Process all queries from the database, search for items, and put them in the queue.
    Uses the global items_queue by default, but can accept a custom queue for backward compatibility.

    Args:
        queue (Queue, optional): The queue to put the items in. Defaults to the global items_queue.

    Returns:
        None
    """

    all_queries = db.get_queries_for_scraping()

    # Renew the browser session when needed (no-op when Patchright is disabled).
    # Visit the domain of the queries so that the cookies match it.
    domain = urlparse(all_queries[0][1]).netloc if all_queries else None
    browser_session.ensure_session(domain=domain)

    # Initialize Vinted
    vinted = Vinted()

    # Get the number of items per query from the database
    items_per_query = int(db.get_parameter("items_per_query"))

    allowlist_enabled = db.get_allowlist() != 0
    country_lookups = 0

    # for each keyword we parse data
    for query in all_queries:
        per_page = query[4] or items_per_query
        try:
            all_items = vinted.items.search(query[1], nbr_items=per_page)
        except HTTPError as err:
            # A single failing query must not stop the whole cycle
            logger.error(f"Failed to scrape query {query[1]}: {err}")
            continue
        except Exception as err:
            logger.error(
                f"Unexpected error while scraping {query[1]}: {err}", exc_info=True
            )
            continue

        # Filter to only include new items. This should reduce the amount of db calls.
        data = [item for item in all_items if item.is_new_item()]

        # Warm up the seller country cache without hammering the API
        if allowlist_enabled:
            country_lookups = warm_user_countries(data, country_lookups)

        queue.put((data, query[0]))
        logger.info(f"Scraped {len(data)} items for query: {query[1]}")

        # Small jitter between queries to look less like a bot
        time.sleep(random.uniform(0.5, 1.5))


def warm_user_countries(items, lookups=0):
    """
    Resolve the seller country of the items that are not cached yet.

    Args:
        items (list): The items of the current query
        lookups (int): Number of lookups already done in this cycle

    Returns:
        int: The updated number of lookups
    """
    for item in items:
        if lookups >= MAX_COUNTRY_LOOKUPS:
            break
        if item.seller_country:
            continue
        profile_id = (item.raw_data.get("user") or {}).get("id")
        if profile_id is None or db.get_cached_user_country(profile_id):
            continue
        try:
            get_user_country(profile_id)
        except Exception as err:
            # The users endpoint is blocked too: stop here instead of retrying
            # for every item of the cycle.
            logger.warning(f"Could not resolve the country of user {profile_id}: {err}")
            break
        lookups += 1
    return lookups


def clear_item_queue(items_queue, new_items_queue):
    """
    Process items from the items_queue.
    This function is scheduled to run frequently.

    New items are written to the outbox table (so they survive restarts) and
    pushed to the RSS queue.
    """
    if items_queue.empty():
        return

    data, query_id = items_queue.get()
    banwords_str = db.get_parameter("banwords")
    message_template = db.get_parameter("message_template") or DEFAULT_MESSAGE_TEMPLATE
    caption_template = db.get_parameter("caption_template") or DEFAULT_CAPTION_TEMPLATE
    send_photo = db.get_parameter("send_photo") == "True"
    show_buy_button = db.get_parameter("show_buy_button") == "True"
    allowlist = db.get_allowlist()

    for item in reversed(data):
        # Skip items the API returned with missing required fields
        if not item.is_valid:
            continue

        # If the item is already in the db, just update the timestamp.
        # The check is on the item id and not on the query timestamp: when the
        # catalog is scraped from the DOM all the items of a page share the
        # same "first seen" timestamp, so a timestamp based filter would skip
        # every item but the first one.
        if db.is_item_in_db_by_id(item.id) is True:
            db.update_last_timestamp(query_id, item.raw_timestamp)
            pass
        # If there's an allowlist and
        # If the user's country is not in the allowlist, we just update the timestamp
        elif allowlist != 0 and resolve_seller_country(item) not in (
            allowlist + ["XX"]
        ):
            db.update_last_timestamp(query_id, item.raw_timestamp)
            pass
        # Check if the item title contains any banwords
        elif banwords_str and contains_banwords(item.title, banwords_str):
            # If it contains banwords, just update the timestamp and skip
            db.update_last_timestamp(query_id, item.raw_timestamp)
            pass
        else:
            # We create the message
            content = format_item_message(message_template, item)
            caption = (
                format_item_message(caption_template, item)
                if send_photo and item.photo
                else None
            )
            buy_url = item.buy_url if show_buy_button else None
            buy_text = "Buy" if buy_url else None

            # Queue the notification: it survives restarts and is retried
            db.add_outbox_item(
                content=content,
                caption=caption,
                url=item.url,
                button_text="Open Vinted",
                buy_url=buy_url,
                buy_text=buy_text,
                photo_url=item.photo if caption else None,
                query_id=query_id,
            )
            # Keep feeding the RSS feed (plain text: it escapes on its own)
            new_items_queue.put(
                (html.unescape(content), item.url, "Open Vinted", None, None)
            )

            # Add the item to the db
            db.add_item_to_db(
                id=item.id,
                timestamp=item.raw_timestamp,
                price=item.price,
                title=item.title,
                photo_url=item.photo,
                query_id=query_id,
                currency=item.currency,
            )


def parse_banwords(banwords_str):
    """
    Split the banwords parameter into a list of lowercase words.

    Args:
        banwords_str (str): Banwords separated by 3 pipe characters

    Returns:
        list: The banwords
    """
    if not banwords_str:
        return []
    return [word.strip().lower() for word in banwords_str.split("|||") if word.strip()]


def add_banword(banwords_str, word):
    """
    Return the banwords string with a new word appended.

    Args:
        banwords_str (str): Current banwords
        word (str): Word to add

    Returns:
        str: The updated banwords string
    """
    banwords = parse_banwords(banwords_str)
    word = word.strip().lower()
    if word and word not in banwords:
        banwords.append(word)
    return " ||| ".join(banwords)


def remove_banword(banwords_str, word):
    """
    Return the banwords string without the given word.

    Args:
        banwords_str (str): Current banwords
        word (str): Word to remove

    Returns:
        str: The updated banwords string
    """
    word = word.strip().lower()
    banwords = [b for b in parse_banwords(banwords_str) if b != word]
    return " ||| ".join(banwords)


def contains_banwords(title, banwords_str):
    """
    Check if a title contains any banwords.

    Args:
        title (str): The title to check
        banwords_str (str): List of banwords separated by 3 pipe character
    Returns:
        bool: True if the title contains any banwords, False otherwise
    """

    # Split the banwords string into a list using pipe as delimiter
    banwords = parse_banwords(banwords_str)

    # If the list is empty, return False
    if not banwords:
        return False

    # Check if any banword is in the title (case-insensitive)
    title_lower = title.lower()
    for word in banwords:
        if word in title_lower:
            return True

    return False


def check_version():
    """
    Check if the application is up to date
    """
    try:
        # Get URL from the database
        github_url = db.get_parameter("github_url")
        # Get version from the database
        ver = db.get_parameter("version")
        # Get latest version from the repository
        url = f"{github_url}/releases/latest"
        response = requests.get(url)

        if response.status_code == 200:
            latest_version = response.url.split("/")[-1]
            is_up_to_date = ver == latest_version
            return is_up_to_date, ver, latest_version, github_url
        else:
            # If we can't check, assume it's up to date
            return True, ver, ver, github_url
    except Exception as e:
        logger.error(f"Error checking for new version: {str(e)}", exc_info=True)
        # If we can't check, assume it's up to date
        return True, ver, ver, github_url
