""" Comet scraper module """
import base64
import json
from typing import Dict, Union

import regex
from requests import ConnectTimeout, ReadTimeout
from requests.exceptions import RequestException

from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.request import RateLimiter, RateLimitExceeded, get, ping


class Comet:
    """Scraper for `Comet`"""

    def __init__(self):
        self.key = "comet"
        self.settings = settings_manager.settings.scraping.comet
        self.timeout = self.settings.timeout
        self.encoded_string = base64.b64encode(json.dumps({
            "indexers": self.settings.indexers,
            "maxResults": 0,
            "resolutions": ["All"],
            "languages": ["All"],
            "debridService": "realdebrid",
            "debridApiKey": settings_manager.settings.downloaders.real_debrid.api_key,
            "debridStreamProxyPassword": ""
        }).encode("utf-8")).decode("utf-8")
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.second_limiter = RateLimiter(max_calls=1, period=5) if self.settings.ratelimit else None
        logger.success("Comet initialized!")

    def validate(self) -> bool:
        """Validate the Comet settings."""
        if not self.settings.enabled:
            return False
        if not self.settings.url:
            logger.error("Comet URL is not configured and will not be used.")
            return False
        if not isinstance(self.timeout, int) or self.timeout <= 0:
            logger.error("Comet timeout is not set or invalid.")
            return False
        if not isinstance(self.settings.ratelimit, bool):
            logger.error("Comet ratelimit must be a valid boolean.")
            return False
        try:
            url = f"{self.settings.url}/manifest.json"
            response = ping(url=url, timeout=self.timeout)
            if response.is_ok:
                return True
        except Exception as e:
            logger.error(f"Comet failed to initialize: {e}", )
        return False

    def run(self, item: MediaItem) -> Dict[str, str]:
        """Scrape the comet site for the given media items
        and update the object with scraped streams"""
        if not item or isinstance(item, Show):
            return {}

        try:
            # Returns a dict of {infoHash: raw_title}
            return self.scrape(item)
        except RateLimitExceeded:
            if self.hour_limiter:
                self.hour_limiter.limit_hit()
            else:
                logger.warning(f"Comet ratelimit exceeded for item: {item.log_string}")
        except ConnectTimeout:
            logger.warning(f"Comet connection timeout for item: {item.log_string}")
        except ReadTimeout:
            logger.warning(f"Comet read timeout for item: {item.log_string}")
        except RequestException as e:
            logger.error(f"Comet request exception: {str(e)}")
        except Exception as e:
            logger.error(f"Comet exception thrown: {str(e)}")
        return {}

    def scrape(self, item: MediaItem) -> Dict[str, str]:
        """Scrape the given media item"""
        data, stream_count = self.api_scrape(item)
        if data:
            logger.log("SCRAPER", f"Found {len(data)} streams out of {stream_count} for {item.log_string}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {item.log_string}")
        return data

    
    def _determine_scrape(self, item: Union[Show, Season, Episode, Movie]) -> tuple[str, str, str]:
        """Determine the scrape type and identifier for the given media item"""
        try:
            if isinstance(item, Show):
                identifier, scrape_type, imdb_id = f":{item.seasons[0].number}:1", "series", item.imdb_id
            elif isinstance(item, Season):
                identifier, scrape_type, imdb_id = f":{item.number}:1", "series", item.parent.imdb_id
            elif isinstance(item, Episode):
                identifier, scrape_type, imdb_id = f":{item.parent.number}:{item.number}", "series", item.parent.parent.imdb_id
            elif isinstance(item, Movie):
                identifier, scrape_type, imdb_id = None, "movie", item.imdb_id
            else:
                logger.error("Invalid media item type")
                return None, None, None
            return identifier, scrape_type, imdb_id
        except Exception as e:
            logger.warning(f"Failed to determine scrape type or identifier for {item.log_string}: {e}")
            return None, None, None

    def api_scrape(self, item: MediaItem) -> tuple[Dict[str, str], int]:
        """Wrapper for `Comet` scrape method"""
        identifier, scrape_type, imdb_id = self._determine_scrape(item)
        if not imdb_id:
            return {}, 0

        url = f"{self.settings.url}/{self.encoded_string}/stream/{scrape_type}/{imdb_id}{identifier or ''}.json"

        if self.second_limiter:
            with self.second_limiter:
                response = get(url, timeout=self.timeout)
        else:
            response = get(url, timeout=self.timeout)

        if not response.is_ok or not getattr(response.data, "streams", None):
            return {}, 0

        torrents: Dict[str, str] = {}
        for stream in response.data.streams:
            if stream.title == "Invalid Comet config.":
                logger.error("Invalid Comet config.")
                return {}, 0

            infohash_pattern = regex.compile(r"(?!.*playback\/)[a-zA-Z0-9]{40}")
            infohash = infohash_pattern.search(stream.url).group()
            title = stream.title.split("\n")[0]

            if not infohash:
                logger.warning(f"Comet infohash not found for title: {title}")
                continue

            torrents[infohash] = title

        return torrents, len(response.data.streams)
