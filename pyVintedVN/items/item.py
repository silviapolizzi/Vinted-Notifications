from datetime import datetime, timezone


class Item:
    """
    Represents a single item from Vinted.

    This class parses and stores various attributes of a Vinted item,
    such as id, title, brand, size, price, etc.

    Attributes:
        raw_data (dict): The raw data of the item as received from the API.
        id (str): The unique identifier of the item.
        title (str): The title of the item.
        brand_title (str): The brand of the item.
        size_title (str): The size of the item, or None if not available.
        currency (str): The currency code of the item's price.
        price (float): The price of the item.
        photo (str): The URL of the item's photo.
        url (str): The URL of the item on Vinted.
        created_at_ts (datetime): The timestamp when the item was created.
        raw_timestamp (int): The raw timestamp value from the API.
        seller_country (str): The seller country when the API exposes it.
    """

    def __init__(self, data):
        """
        Initialize an Item with data from the Vinted API.

        Missing fields are tolerated: Vinted sometimes returns partial payloads
        and a single broken item must not break the whole batch.

        Args:
            data (dict): The item data from the Vinted API.
        """
        self.raw_data = data
        self.id = data.get("id")
        self.title = data.get("title") or ""
        self.brand_title = data.get("brand_title") or ""
        self.size_title = data.get("size_title")

        price = data.get("price") or {}
        self.currency = price.get("currency_code") or "EUR"
        self.price = price.get("amount")

        photo = data.get("photo") or {}
        self.photo = photo.get("url")

        self.url = data.get("url") or ""
        self.buy_url = self._build_buy_url()

        user = data.get("user") or {}
        self.seller_country = user.get("country_iso_code")

        self.raw_timestamp = self._extract_timestamp(data, photo)
        self.created_at_ts = (
            datetime.fromtimestamp(self.raw_timestamp, tz=timezone.utc)
            if self.raw_timestamp
            else None
        )

    def _build_buy_url(self):
        """Build the direct checkout URL of the item."""
        if self.url and "/items/" in self.url and self.id:
            prefix = self.url.split("/items/")[0]
            return (
                f"{prefix}/transaction/buy/new?source_screen=item"
                f"&transaction%5Bitem_id%5D={self.id}"
            )
        return self.url or None

    @staticmethod
    def _extract_timestamp(data, photo):
        """
        Extract the creation timestamp of the item.

        Args:
            data (dict): The raw item data
            photo (dict): The raw photo data

        Returns:
            int: The timestamp, or 0 when it cannot be determined
        """
        high_resolution = photo.get("high_resolution") or {}
        timestamp = high_resolution.get("timestamp") or data.get("created_at_ts") or 0
        try:
            return int(timestamp)
        except (TypeError, ValueError):
            return 0

    @property
    def is_valid(self):
        """Return True if the item has the minimum data needed to notify it."""
        return bool(self.id) and bool(self.url)

    def __eq__(self, other):
        """
        Compare this item with another one.

        Two items are considered the same if they have the same ID.

        Args:
            other (Item): The other item to compare with.

        Returns:
            bool: True if the items have the same ID, False otherwise.
        """
        if not isinstance(other, Item):
            return False
        return self.id == other.id

    def __hash__(self):
        """
        Return a hash value for this item.

        The hash is based on the item's ID, which allows items to be used
        as keys in dictionaries and elements in sets.

        Returns:
            int: A hash value for this item.
        """
        return hash(("id", self.id))

    def is_new_item(self, minutes=20):
        """
        Check if this item is newly listed.

        An item is considered new if it was created within the specified
        number of minutes from the current time.

        Args:
            minutes (int, optional): The number of minutes to consider an item as new.
                Defaults to 20.

        Returns:
            bool: True if the item is new, False otherwise.
        """
        if self.created_at_ts is None:
            return False
        delta = datetime.now(timezone.utc) - self.created_at_ts
        return delta.total_seconds() < minutes * 60

    # Alias for backward compatibility
    isNewItem = is_new_item
