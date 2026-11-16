import sqlite3
import time
from traceback import print_exc

DB_PATH = "./data/vinted_notifications.db"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_or_update_sqlite_db(db_path):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Using the sql script
        with open(db_path, "r", encoding="utf-8") as sql_file:
            sql_script = sql_file.read()
            cursor.executescript(sql_script)

        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def is_item_in_db_by_id(id):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT() FROM items WHERE item=?", (id,))
        if cursor.fetchone()[0]:
            return True
        return False
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def get_last_timestamp(query_id):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT last_item FROM queries WHERE id=?", (query_id,))
        result = cursor.fetchone()
        if result:
            return result[0]
        return None
    except Exception:
        print_exc()
        return None
    finally:
        if conn:
            conn.close()


def update_last_timestamp(query_id, timestamp):
    """Move the last seen timestamp forward (never backwards)."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE queries SET last_item=? WHERE id=? "
            "AND (last_item IS NULL OR last_item < ?)",
            (timestamp, query_id, timestamp),
        )
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def add_item_to_db(id, title, query_id, price, timestamp, photo_url, currency="EUR"):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Insert into db the id and the query_id related to the item
        cursor.execute(
            "INSERT INTO items (item, title, price, currency, timestamp, photo_url, query_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, title, price, currency, timestamp, photo_url, query_id),
        )
        # Update the last item for the query (never backwards)
        cursor.execute(
            "UPDATE queries SET last_item=? WHERE id=? "
            "AND (last_item IS NULL OR last_item < ?)",
            (timestamp, query_id, timestamp),
        )
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def get_queries():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, query, last_item, query_name FROM queries")
        return cursor.fetchall()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def is_query_in_db(processed_query):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # replace spaces in searched_text by % to match any query containing the searched text

        cursor.execute(
            "SELECT COUNT() FROM queries WHERE query = ?", (processed_query,)
        )
        if cursor.fetchone()[0]:
            return True
        return False
    except Exception:
        print_exc()
        return False
    finally:
        if conn:
            conn.close()


def add_query_to_db(query, name=None):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if name:
            cursor.execute(
                "INSERT INTO queries (query, last_item, query_name) VALUES (?, NULL, ?)",
                (query, name),
            )
        else:
            cursor.execute(
                "INSERT INTO queries (query, last_item) VALUES (?, NULL)", (query,)
            )
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def get_query_id_by_rowid(rowid):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        query = f"SELECT id FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY ROWID) rn FROM queries) t WHERE rn={rowid}"
        cursor.execute(query)
        result = cursor.fetchone()
        if result:
            return result[0]
        return None
    except Exception:
        print_exc()
        return None
    finally:
        if conn:
            conn.close()


def remove_query_from_db(query_number):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Delete items associated with this query using query_id
        cursor.execute("DELETE FROM items WHERE query_id=?", (query_number,))
        # Delete the query
        cursor.execute("DELETE FROM queries WHERE id=?", (query_number,))
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def remove_all_queries_from_db():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Delete all items first to maintain foreign key integrity
        cursor.execute("DELETE FROM items")
        # Then delete all queries
        cursor.execute("DELETE FROM queries")
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def update_query_in_db(query_id, query, name):
    """
    Update an existing query in the database.

    Args:
        query_id (int): The ID of the query to update
        query (str): The new query URL
        name (str, optional): The new name for the query

    Returns:
        bool: True if the query was updated successfully, False otherwise
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE queries SET query=?, query_name=? WHERE id=?",
            (query, name, query_id),
        )
        conn.commit()
        return True
    except Exception:
        print_exc()
        return False
    finally:
        if conn:
            conn.close()


def add_to_allowlist(country):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO allowlist VALUES (?)", (country,))
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def remove_from_allowlist(country):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM allowlist WHERE country=?", (country,))
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def get_allowlist():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM allowlist")
        # Get list of countries
        countries = [country[0] for country in cursor.fetchall()]
        # Return 0 if there are no countries in the allowlist
        if not countries:
            return 0
        return countries
    finally:
        if conn:
            conn.close()


def clear_allowlist():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM allowlist")
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def get_parameter(key):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM parameters WHERE key=?", (key,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def set_parameter(key, value):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE parameters SET value=? WHERE key=?", (value, key))
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def get_all_parameters():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM parameters")
        return {row[0]: row[1] for row in cursor.fetchall()}
    except Exception:
        print_exc()
        return {}
    finally:
        if conn:
            conn.close()


def get_items(limit=50, query=None):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if query:
            # Get the query_id for the given query
            cursor.execute("SELECT id FROM queries WHERE query=?", (query,))
            result = cursor.fetchone()
            if result:
                query_id = result[0]
                # Get items with the matching query_id
                cursor.execute(
                    "SELECT i.item, i.title, i.price, i.currency, i.timestamp, q.query, i.photo_url, q.query_name FROM items i JOIN queries q ON i.query_id = q.id WHERE i.query_id=? ORDER BY i.timestamp DESC LIMIT ?",
                    (query_id, limit),
                )
            else:
                return []
        else:
            # Join with queries table to get the query text
            cursor.execute(
                "SELECT i.item, i.title, i.price, i.currency, i.timestamp, q.query, i.photo_url, q.query_name FROM items i JOIN queries q ON i.query_id = q.id ORDER BY i.timestamp DESC LIMIT ?",
                (limit,),
            )
        return cursor.fetchall()
    except Exception:
        print_exc()
        return []
    finally:
        if conn:
            conn.close()


def get_total_items_count():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM items")
        return cursor.fetchone()[0]
    except Exception:
        print_exc()
        return 0
    finally:
        if conn:
            conn.close()


def get_total_queries_count():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM queries")
        return cursor.fetchone()[0]
    except Exception:
        print_exc()
        return 0
    finally:
        if conn:
            conn.close()


def get_last_found_item():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT i.item, i.title, i.price, i.currency, i.timestamp, q.query, i.photo_url FROM items i JOIN queries q ON i.query_id = q.id ORDER BY i.timestamp DESC LIMIT 1"
        )
        return cursor.fetchone()
    except Exception:
        print_exc()
        return None
    finally:
        if conn:
            conn.close()


def get_items_per_day():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Get total items
        cursor.execute("SELECT COUNT(*) FROM items")
        total_items = cursor.fetchone()[0]

        if total_items == 0:
            return 0

        # Get earliest and latest timestamps
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM items")
        min_timestamp, max_timestamp = cursor.fetchone()

        # Calculate number of days (add 1 to include both start and end days)
        import datetime

        min_date = datetime.datetime.fromtimestamp(min_timestamp).date()
        max_date = datetime.datetime.fromtimestamp(max_timestamp).date()
        days_diff = (max_date - min_date).days + 1

        # Ensure at least 1 day to avoid division by zero
        days_diff = max(1, days_diff)

        # Calculate items per day
        return round(total_items / days_diff, 1)
    except Exception:
        print_exc()
        return 0
    finally:
        if conn:
            conn.close()


def get_next_migration(migration_files, current_version):
    """
    Return the migration file that upgrades the current version, if any.

    Files are named "<from>_<to>.sql". The underscore is required so that
    "1.0.5" does not match "1.0.5.1_1.0.5.2.sql" (which would skip migrations).

    Args:
        migration_files (list): List of migration file names
        current_version (str): Current version stored in the database

    Returns:
        str | None: The next migration file name, or None if up to date
    """
    prefix = f"{current_version}_"
    candidates = sorted(f for f in migration_files if f.startswith(prefix))
    return candidates[0] if candidates else None


def get_queries_with_status():
    """
    Get all queries with their enabled flag.

    Returns:
        list: Rows of (id, query, query_name, enabled)
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, query, query_name, enabled FROM queries ORDER BY ROWID"
        )
        return cursor.fetchall()
    except Exception:
        print_exc()
        return []
    finally:
        if conn:
            conn.close()


def get_queries_for_scraping():
    """
    Get the enabled queries with their optional per-query item count.

    Returns:
        list: Rows of (id, query, last_item, query_name, per_page)
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, query, last_item, query_name, per_page FROM queries "
            "WHERE enabled IS NULL OR enabled = 1"
        )
        return cursor.fetchall()
    except Exception:
        print_exc()
        return []
    finally:
        if conn:
            conn.close()


def get_query(query_id):
    """
    Get a single query.

    Args:
        query_id (int): The ID of the query

    Returns:
        tuple | None: (id, query, last_item, query_name, enabled)
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, query, last_item, query_name, enabled FROM queries WHERE id=?",
            (query_id,),
        )
        return cursor.fetchone()
    except Exception:
        print_exc()
        return None
    finally:
        if conn:
            conn.close()


def set_query_enabled(query_id, enabled):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE queries SET enabled=? WHERE id=?", (1 if enabled else 0, query_id)
        )
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def get_items_count_by_query():
    """
    Get the number of stored items per query.

    Returns:
        list: Rows of (display_name, count)
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE(q.query_name, q.query), COUNT(i.item) "
            "FROM queries q LEFT JOIN items i ON i.query_id = q.id "
            "GROUP BY q.id ORDER BY COUNT(i.item) DESC"
        )
        return cursor.fetchall()
    except Exception:
        print_exc()
        return []
    finally:
        if conn:
            conn.close()


def get_items_count_since(timestamp):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM items WHERE timestamp >= ?", (timestamp,))
        return cursor.fetchone()[0]
    except Exception:
        print_exc()
        return 0
    finally:
        if conn:
            conn.close()


def get_last_item_timestamp():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(timestamp) FROM items")
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception:
        print_exc()
        return None
    finally:
        if conn:
            conn.close()


def get_cached_user_country(profile_id):
    """
    Get a previously resolved seller country.

    Args:
        profile_id (str): Vinted user id

    Returns:
        str | None: The cached ISO country code, or None
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT country FROM users WHERE profile_id=?", (str(profile_id),)
        )
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception:
        print_exc()
        return None
    finally:
        if conn:
            conn.close()


def set_cached_user_country(profile_id, country):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (profile_id, country, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(profile_id) DO UPDATE SET country=excluded.country, "
            "updated_at=excluded.updated_at",
            (str(profile_id), country, int(time.time())),
        )
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def add_outbox_item(
    content,
    url,
    button_text,
    buy_url=None,
    buy_text=None,
    photo_url=None,
    caption=None,
    query_id=None,
):
    """
    Queue a notification. It is removed from the outbox once sent.

    Returns:
        int | None: The id of the created row
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO outbox (content, caption, url, button_text, buy_url, buy_text, "
            "photo_url, query_id, created_at, attempts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                content,
                caption,
                url,
                button_text,
                buy_url,
                buy_text,
                photo_url,
                query_id,
                int(time.time()),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception:
        print_exc()
        return None
    finally:
        if conn:
            conn.close()


def get_pending_outbox(limit=10):
    """
    Get the oldest unsent notifications.

    Returns:
        list: Dictionaries with the outbox columns
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY created_at ASC, id ASC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception:
        print_exc()
        return []
    finally:
        if conn:
            conn.close()


def mark_outbox_sent(item_id):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE outbox SET sent_at=?, last_error=NULL WHERE id=?",
            (int(time.time()), item_id),
        )
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def mark_outbox_failed(item_id, error):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE outbox SET attempts=attempts+1, last_error=? WHERE id=?",
            (str(error)[:500], item_id),
        )
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def expire_outbox(max_age_seconds=86400):
    """Mark as expired (sent) the notifications that are too old to be useful."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE outbox SET sent_at=?, last_error='expired' "
            "WHERE sent_at IS NULL AND created_at < ?",
            (int(time.time()), int(time.time()) - max_age_seconds),
        )
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def cleanup_outbox(max_age_seconds=7 * 86400):
    """Delete the notifications that were already sent (or expired)."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM outbox WHERE sent_at IS NOT NULL AND sent_at < ?",
            (int(time.time()) - max_age_seconds,),
        )
        conn.commit()
    except Exception:
        print_exc()
    finally:
        if conn:
            conn.close()


def get_outbox_count():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL")
        return cursor.fetchone()[0]
    except Exception:
        print_exc()
        return 0
    finally:
        if conn:
            conn.close()
