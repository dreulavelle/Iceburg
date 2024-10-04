"""Plex Watchlist Module"""
from typing import Generator, Union

from plexapi.myplex import MyPlexAccount
from requests import HTTPError, Session
from urllib3 import HTTPConnectionPool

from program.db.db_functions import _filter_existing_items
from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.request import get, ping


class PlexWatchlist:
    """Class for managing Plex Watchlists"""

    def __init__(self):
        self.key = "plex_watchlist"
        self.rss_enabled = False
        self.settings = settings_manager.settings.content.plex_watchlist
        self.token = settings_manager.settings.updaters.plex.token
        self.account = None
        self.session = Session()
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.recurring_items: set[str] = set() # set of imdb ids
        logger.success("Plex Watchlist initialized!")

    def validate(self):
        if not self.settings.enabled:
            return False
        if not self.token:
            logger.error("Plex token is not set!")
            return False
        try:
            self.account = MyPlexAccount(self.session, token=self.token)
        except Exception as e:
            logger.error(f"Unable to authenticate Plex account: {e}")
            return False
        if self.settings.rss:
            for rss_url in self.settings.rss:
                try:
                    response = ping(rss_url)
                    response.response.raise_for_status()
                    self.rss_enabled = True
                except HTTPError as e:
                    if e.response.status_code == 404:
                        logger.warning(f"Plex RSS URL {rss_url} is Not Found. Please check your RSS URL in settings.")
                        return False
                    else:
                        logger.warning(
                            f"Plex RSS URL {rss_url} is not reachable (HTTP status code: {e.response.status_code})."
                        )
                        return False
                except Exception as e:
                    logger.error(f"Failed to validate Plex RSS URL {rss_url}: {e}", exc_info=True)
                    return False
        return True

    def run(self) -> Generator[MediaItem, None, None]:
        """Fetch new media from `Plex Watchlist` and RSS feed if enabled."""
        try:
            watchlist_items: list[str] = self._get_items_from_watchlist()
            rss_items: list[str] = self._get_items_from_rss() if self.rss_enabled else []
        except Exception as e:
            logger.warning(f"Error fetching items: {e}")
            return

        plex_items: set[str] = set(watchlist_items) | set(rss_items)
        items_to_yield: list[MediaItem] = [MediaItem({"imdb_id": imdb_id, "requested_by": self.key}) for imdb_id in plex_items if imdb_id and imdb_id.startswith("tt")]
        non_existing_items = _filter_existing_items(items_to_yield)
        new_non_recurring_items = [item for item in non_existing_items if item.ids["imdb_id"] not in self.recurring_items and isinstance(item, MediaItem)]
        self.recurring_items.update([item.ids["imdb_id"] for item in new_non_recurring_items])

        if new_non_recurring_items:
            logger.info(f"Found {len(new_non_recurring_items)} new items to fetch")

        yield new_non_recurring_items

    def _get_items_from_rss(self) -> list[str]:
        """Fetch media from Plex RSS Feeds."""
        rss_items: list[str] = []
        for rss_url in self.settings.rss:
            try:
                response = self.session.get(rss_url + "?format=json", timeout=60)
                for _item in response.json().get("items", []):
                    imdb_id = self._extract_imdb_ids(_item.get("guids", []))
                    if imdb_id and imdb_id.startswith("tt"):
                        rss_items.append(imdb_id)
                        self.recurring_items.add(imdb_id)
                    else:
                        logger.log("NOT_FOUND", f"Failed to extract IMDb ID from {_item['title']}")
            except Exception as e:
                logger.error(f"An unexpected error occurred while fetching Plex RSS feed from {rss_url}: {e}")
        return rss_items

    def _get_items_from_watchlist(self) -> list[str]:
        """Fetch media from Plex watchlist"""
        items = self.account.watchlist()
        watchlist_items: list[str] = []
        for item in items:
            try:
                if hasattr(item, "guids") and item.guids:
                    imdb_id: str = next((guid.id.split("//")[-1] for guid in item.guids if guid.id.startswith("imdb://")), "")
                    if imdb_id and imdb_id.startswith("tt"):
                        watchlist_items.append(imdb_id)
                        self.recurring_items.add(imdb_id)
                    else:
                        logger.log("NOT_FOUND", f"Unable to extract IMDb ID from {item.title} ({item.year}) with data id: {imdb_id}")
                else:
                    logger.log("NOT_FOUND", f"{item.title} ({item.year}) is missing guids attribute from Plex")
            except Exception as e:
                logger.error(f"An unexpected error occurred while fetching Plex watchlist item {item.title}: {e}")
        return watchlist_items

    def _extract_imdb_ids(self, guids: list) -> str | None:
        """Helper method to extract IMDb IDs from guids"""
        for guid in guids:
            if guid and guid.startswith("imdb://"):
                imdb_id = guid.split("//")[-1]
                if imdb_id:
                    return imdb_id
        return None


# api

def _ratingkey_to_imdbid(ratingKey: str) -> str | None:
    """Convert Plex rating key to IMDb ID"""
    token = settings_manager.settings.updaters.plex.token
    filter_params = "includeGuids=1&includeFields=guid,title,year&includeElements=Guid"
    url = f"https://metadata.provider.plex.tv/library/metadata/{ratingKey}?X-Plex-Token={token}&{filter_params}"
    response = get(url)
    if response.is_ok and hasattr(response.data, "MediaContainer"):
        metadata = response.data.MediaContainer.Metadata[0]
        return next((guid.id.split("//")[-1] for guid in metadata.Guid if "imdb://" in guid.id), None)
    logger.debug(f"Failed to fetch IMDb ID for ratingKey: {ratingKey}")
    return None