"""Mdblist content module"""

from typing import Generator

from program.db.db_functions import _filter_existing_items
from program.media.item import MediaItem
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.ratelimiter import RateLimiter, RateLimitExceeded
from utils.request import get, ping


class Mdblist:
    """Content class for mdblist"""

    def __init__(self):
        self.key = "mdblist"
        self.settings = settings_manager.settings.content.mdblist
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.requests_per_2_minutes = self._calculate_request_time()
        self.rate_limiter = RateLimiter(self.requests_per_2_minutes, 120, True)
        self.recurring_items = set()
        logger.success("mdblist initialized")

    def validate(self):
        if not self.settings.enabled:
            return False
        if self.settings.api_key == "" or len(self.settings.api_key) != 25:
            logger.error("Mdblist api key is not set.")
            return False
        if not self.settings.lists:
            logger.error("Mdblist is enabled, but list is empty.")
            return False
        response = ping(f"https://mdblist.com/api/user?apikey={self.settings.api_key}")
        if "Invalid API key!" in response.response.text:
            logger.error("Mdblist api key is invalid.")
            return False
        return True

    def run(self) -> Generator[MediaItem, None, None]:
        """Fetch media from mdblist and add them to media_items attribute
        if they are not already there"""
        items_to_yield = []
        try:
            with self.rate_limiter:
                for list in self.settings.lists:
                    if not list:
                        continue

                    if isinstance(list, int):
                        items = list_items_by_id(list, self.settings.api_key)
                    else:
                        items = list_items_by_url(list, self.settings.api_key)
                    for item in items:
                        if hasattr(item, "error") or not item or item.ids["imdb_id"] is None:
                            continue
                        if item.ids["imdb_id"].startswith("tt"):
                            items_to_yield.append(MediaItem(
                                {"imdb_id": item.ids["imdb_id"], "requested_by": self.key}
                            ))
        except RateLimitExceeded:
            pass

        non_existing_items = _filter_existing_items(items_to_yield)
        new_non_recurring_items = [item for item in non_existing_items if item.ids["imdb_id"] not in self.recurring_items and isinstance(item, MediaItem)]
        self.recurring_items.update([item.ids["imdb_id"] for item in new_non_recurring_items])

        if new_non_recurring_items:
            logger.info(f"Found {len(new_non_recurring_items)} new items to fetch")
        yield new_non_recurring_items

    def _calculate_request_time(self):
        limits = my_limits(self.settings.api_key).limits
        daily_requests = limits.api_requests
        return daily_requests / 24 / 60 * 2


# API METHODS


def my_limits(api_key: str):
    """Wrapper for mdblist api method 'My limits'"""
    response = get(f"http://www.mdblist.com/api/user?apikey={api_key}")
    return response.data


def list_items_by_id(list_id: int, api_key: str):
    """Wrapper for mdblist api method 'List items'"""
    response = get(
        f"http://www.mdblist.com/api/lists/{str(list_id)}/items?apikey={api_key}"
    )
    return response.data


def list_items_by_url(url: str, api_key: str):
    url = url if url.endswith("/") else f"{url}/"
    url = url if url.endswith("json/") else f"{url}json/"
    response = get(url, params={"apikey": api_key})
    return response.data