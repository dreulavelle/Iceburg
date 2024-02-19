"""Mdblist content module"""
from typing import Generator

from utils.logger import logger
from program.settings.manager import settings_manager
from program.media.item import MediaItem
from utils.request import RateLimitExceeded, RateLimiter, get, ping



class Mdblist():
    """Content class for mdblist"""

    def __init__(self):
        self.key = "mdblist"
        self.settings = settings_manager.settings.content.mdblist
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.requests_per_2_minutes = self._calculate_request_time()
        self.rate_limiter = RateLimiter(self.requests_per_2_minutes, 120, True)
        logger.info("mdblist initialized")

    def validate(self):
        if not self.settings.enabled:
            logger.debug("Mdblist is set to disabled.")
            return False
        if self.settings.lists == [""]:
            logger.error("Mdblist is enabled, but list is empty.")
            return False
        if self.settings.api_key == "" or len(self.settings.api_key) != 25:
            logger.error("Mdblist api key is not set.")
            return False
        response = ping(f"https://mdblist.com/api/user?apikey={self.settings.api_key}")
        if "Invalid API key!" in response.text:
            logger.error("Mdblist api key is invalid.")
            return False
        return True

    def run(self) -> Generator[MediaItem, None, None]:
        """Fetch media from mdblist and add them to media_items attribute
        if they are not already there"""

        try:
            with self.rate_limiter:
                for list_id in self.settings.lists:
                    if not list_id:
                        continue
                    for item in list_items(list_id, self.settings.api_key):
                        yield MediaItem({'imdb_id': item.imdb_id, 'requested_by': self.__class__})
        except RateLimitExceeded:
            pass
        return

    def _calculate_request_time(self):
        limits = my_limits(self.settings.api_key).limits
        daily_requests = limits.api_requests
        requests_per_2_minutes = daily_requests / 24 / 60 * 2
        return requests_per_2_minutes


# API METHODS


def my_limits(api_key: str):
    """Wrapper for mdblist api method 'My limits'"""
    response = get(f"http://www.mdblist.com/api/user?apikey={api_key}")
    return response.data


def list_items(list_id: str, api_key: str):
    """Wrapper for mdblist api method 'List items'"""
    response = get(f"http://www.mdblist.com/api/lists/{list_id}/items?apikey={api_key}")
    return response.data
