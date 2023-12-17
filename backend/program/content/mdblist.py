"""Mdblist content module"""
import json
from utils.settings import settings_manager
from utils.logger import logger
from utils.request import RateLimitExceeded, RateLimiter, get, ping
from program.media import MediaItemContainer
from program.updaters.trakt import Updater as Trakt


class Content:
    """Content class for mdblist"""

    def __init__(
        self,
    ):
        self.initialized = False
        self.settings = settings_manager.get("mdblist")
        if not self._validate_settings():
            logger.info("mdblist is not configured and will not be used.")
            return
        self.updater = Trakt()
        self.requests_per_2_minutes = self._calculate_request_time()
        self.rate_limiter = RateLimiter(self.requests_per_2_minutes, 120, True)
        self.initialized = True

    def _validate_settings(self):
        response = ping(
            f"https://mdblist.com/api/user?apikey={self.settings['api_key']}"
        )
        return not "Invalid API key!" in response.text

    def update_items(self, media_items: MediaItemContainer):
        """Fetch media from mdblist and add them to media_items attribute
        if they are not already there"""
        try:
            with self.rate_limiter:
                logger.debug("Getting items...")

                items = []
                for list_id in self.settings["lists"]:
                    if list_id:
                        items += self._get_items_from_list(
                            list_id, self.settings["api_key"]
                        )

                new_items = [item for item in items if item not in media_items]
                container = self.updater.create_items(new_items)
                added_items = media_items.extend(container)
                if len(added_items) > 0:
                    logger.info("Added %s items", len(added_items))
                logger.debug("Done!")
        except RateLimitExceeded:
            pass

    def _get_items_from_list(self, list_id: str, api_key: str) -> MediaItemContainer:
        return [item.imdb_id for item in list_items(list_id, api_key)]

    def _calculate_request_time(self):
        limits = my_limits(self.settings["api_key"]).limits
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
