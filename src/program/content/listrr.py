"""Listrr content module"""
from typing import Generator

from requests.exceptions import HTTPError

from program.db.db_functions import _filter_existing_items
from program.indexers.trakt import get_imdbid_from_tmdb
from program.media.item import MediaItem
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.request import get, ping


class Listrr:
    """Content class for Listrr"""

    def __init__(self):
        self.key = "listrr"
        self.url = "https://listrr.pro/api"
        self.settings = settings_manager.settings.content.listrr
        self.headers = {"X-Api-Key": self.settings.api_key}
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.recurring_items: set[str] = set()
        logger.success("Listrr initialized!")

    def validate(self) -> bool:
        """Validate Listrr settings."""
        if not self.settings.enabled:
            return False
        if self.settings.api_key == "" or len(self.settings.api_key) != 64:
            logger.error("Listrr api key is not set or invalid.")
            return False
        valid_list_found = False
        for _, content_list in [
            ("movie_lists", self.settings.movie_lists),
            ("show_lists", self.settings.show_lists),
        ]:
            if content_list is None or not any(content_list):
                continue
            for item in content_list:
                if item == "" or len(item) != 24:
                    return False
            valid_list_found = True
        if not valid_list_found:
            logger.error("Both Movie and Show lists are empty or not set.")
            return False
        try:
            response = ping("https://listrr.pro/", additional_headers=self.headers)
            if not response.is_ok:
                logger.error(
                    f"Listrr ping failed - Status Code: {response.status_code}, Reason: {response.response.reason}",
                )
            return response.is_ok
        except Exception as e:
            logger.error(f"Listrr ping exception: {e}")
            return False

    def run(self) -> Generator[MediaItem, None, None]:
        """Fetch new media from `Listrr`"""
        try:
            movie_items = self._get_items_from_Listrr("Movies", self.settings.movie_lists)
            show_items = self._get_items_from_Listrr("Shows", self.settings.show_lists)
        except Exception as e:
            logger.error(f"Failed to fetch items from Listrr: {e}")
            return

        listrr_items = movie_items + show_items
        non_existing_items = _filter_existing_items(listrr_items)
        new_non_recurring_items = [item for item in non_existing_items if item.ids["imdb_id"] not in self.recurring_items]
        self.recurring_items.update([item.ids["imdb_id"] for item in new_non_recurring_items])

        if new_non_recurring_items:
            logger.info(f"Fetched {len(new_non_recurring_items)} new items from Listrr")

        yield new_non_recurring_items

    def _get_items_from_Listrr(self, content_type, content_lists) -> list[MediaItem]:  # noqa: C901, PLR0912
        """Fetch unique IMDb IDs from Listrr for a given type and list of content."""
        unique_ids: set[str] = set()
        if not content_lists:
            return list(unique_ids)

        for list_id in content_lists:
            if not list_id or len(list_id) != 24:
                continue

            page, total_pages = 1, 1
            while page <= total_pages:
                try:
                    url = f"{self.url}/List/{content_type}/{list_id}/ReleaseDate/Descending/{page}"
                    response = get(url, additional_headers=self.headers).response
                    data = response.json()
                    total_pages = data.get("pages", 1)
                    for item in data.get("items", []):
                        imdb_id = item.get("imDbId")
                        if imdb_id:
                            unique_ids.add(imdb_id)
                        elif content_type == "Movies" and item.get("tmDbId"):
                            imdb_id = get_imdbid_from_tmdb(item["tmDbId"])
                            if imdb_id:
                                unique_ids.add(imdb_id)
                except HTTPError as e:
                    if e.response.status_code in [400, 404, 429, 500]:
                        break
                except Exception as e:
                    logger.error(f"An error occurred: {e}")
                    break
                page += 1
        return list(unique_ids)