""" Mediafusion scraper module """
import json
from typing import Dict, Generator

import requests
from program.media.item import Episode, MediaItem, Season, Show
from program.settings.manager import settings_manager
from program.settings.models import AppModel
from program.settings.versions import models
from requests import ConnectTimeout, ReadTimeout
from requests.exceptions import RequestException
from RTN import RTN, Torrent, sort_torrents
from RTN.exceptions import GarbageTorrent
from utils.logger import logger
from utils.request import RateLimiter, RateLimitExceeded, get, ping


class Mediafusion:
    """Scraper for `Mediafusion`"""

    def __init__(self, hash_cache):
        self.key = "mediafusion"
        self.api_key = None
        self.downloader = None
        self.app_settings: AppModel = settings_manager.settings
        self.settings = self.app_settings.scraping.mediafusion
        self.settings_model = self.app_settings.ranking
        self.ranking_model = models.get(self.settings_model.profile)
        self.rtn = RTN(self.settings_model, self.ranking_model)
        self.timeout = self.settings.timeout
        self.hash_cache = hash_cache
        self.encrypted_string = None
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.second_limiter = RateLimiter(max_calls=1, period=2) if self.settings.ratelimit else None
        logger.success("Mediafusion initialized!")

    def validate(self) -> bool:
        """Validate the Mediafusion settings."""
        if not self.settings.enabled:
            logger.warning("Mediafusion is set to disabled.")
            return False
        if not self.settings.url:
            logger.error("Mediafusion URL is not configured and will not be used.")
            return False
        if not isinstance(self.timeout, int) or self.timeout <= 0:
            logger.error("Mediafusion timeout is not set or invalid.")
            return False
        if not isinstance(self.settings.ratelimit, bool):
            logger.error("Mediafusion ratelimit must be a valid boolean.")
            return False
        if not self.settings.catalogs:
            logger.error("Configure at least one Mediafusion catalog.")
            return False

        if self.app_settings.downloaders.real_debrid.enabled:
            self.api_key = self.app_settings.downloaders.real_debrid.api_key
            self.downloader = "realdebrid"
        elif self.app_settings.downloaders.torbox.enabled:
            self.api_key = self.app_settings.downloaders.torbox.api_key
            self.downloader = "torbox"
        else:
            logger.error("No downloader enabled, please enable at least one.")
            return False

        payload = {
            "streaming_provider": {
                "token": self.api_key,
                "service": self.downloader,
                "enable_watchlist_catalogs": False
            },
            "selected_catalogs": self.settings.catalogs,
            "selected_resolutions": ["4K", "2160p", "1440p", "1080p", "720p"],
            "enable_catalogs": False,
            "max_size": "inf",
            "max_streams_per_resolution": "10",
            "torrent_sorting_priority": ["cached", "resolution", "size", "seeders", "created_at"],
            "show_full_torrent_name": True,
            "api_password": None
        }

        url = f"{self.settings.url}/encrypt-user-data"
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.request("POST", url, json=payload, headers=headers)
            self.encrypted_string = json.loads(response.content)['encrypted_str']
        except Exception as e:
            logger.error(f"Failed to encrypt user data: {e}")
            return False

        try:
            url = f"{self.settings.url}/manifest.json"
            response = ping(url=url, timeout=self.timeout)
            return response.ok
        except Exception as e:
            logger.error(f"Mediafusion failed to initialize: {e}")
            return False

    def run(self, item: MediaItem) -> Generator[MediaItem, None, None]:
        """Scrape the mediafusion site for the given media items
        and update the object with scraped streams"""
        if not item or isinstance(item, Show):
            yield item
            return

        try:
            yield self.scrape(item)
        except RateLimitExceeded:
            if self.second_limiter:
                self.second_limiter.limit_hit()
            else:
                logger.warning(f"Mediafusion ratelimit exceeded for item: {item.log_string}")
        except ConnectTimeout:
            logger.warning(f"Mediafusion connection timeout for item: {item.log_string}")
        except ReadTimeout:
            logger.warning(f"Mediafusion read timeout for item: {item.log_string}")
        except RequestException as e:
            logger.error(f"Mediafusion request exception: {e}")
        except Exception as e:
            logger.error(f"Mediafusion exception thrown: {e}")
        yield item

    def scrape(self, item: MediaItem) -> MediaItem:
        """Scrape the given media item"""
        data, stream_count = self.api_scrape(item)
        if data:
            item.streams.update(data)
            logger.log("SCRAPER", f"Found {len(data)} streams out of {stream_count} for {item.log_string}")
        elif stream_count > 0:
            logger.log("NOT_FOUND", f"Could not find good streams for {item.log_string} out of {stream_count}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {item.log_string}")
        return item

    def api_scrape(self, item: MediaItem) -> tuple[Dict[str, Torrent], int]:
        """Wrapper for `Mediafusion` scrape method"""
        identifier, scrape_type, imdb_id = None, "movie", item.imdb_id
        if isinstance(item, Season):
            identifier, scrape_type, imdb_id = f":{item.number}:1", "series", item.parent.imdb_id
        elif isinstance(item, Episode):
            identifier, scrape_type, imdb_id = f":{item.parent.number}:{item.number}", "series", item.parent.parent.imdb_id

        url = f"{self.settings.url}/{self.encrypted_string}/stream/{scrape_type}/{imdb_id}"
        if identifier:
            url += identifier

        if self.second_limiter:
            with self.second_limiter:
                response = get(f"{url}.json", timeout=self.timeout)
        else:
            response = get(f"{url}.json", timeout=self.timeout)

        if not response.is_ok or len(response.data.streams) <= 0:
            return {}, 0

        torrents = set()
        correct_title = item.get_top_title()
        if not correct_title:
            logger.scraper(f"Correct title not found for {item.log_string}")
            return {}, 0

        for stream in response.data.streams:
            raw_title = stream.description.split("\n💾")[0].replace("📂 ", "")
            info_hash = stream.url.split("?info_hash=")[1]
            if not info_hash or not raw_title:
                continue
            if self.hash_cache and self.hash_cache.is_blacklisted(info_hash):
                continue
            try:
                torrent = self.rtn.rank(raw_title=raw_title, infohash=info_hash, correct_title=correct_title, remove_trash=True)
            except GarbageTorrent:
                continue
            if torrent and torrent.fetch:
                torrents.add(torrent)
        scraped_torrents = sort_torrents(torrents)
        return scraped_torrents, len(response.data.streams)
