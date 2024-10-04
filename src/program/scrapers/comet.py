""" Comet scraper module """
import base64
import json
from typing import Dict

import regex
from requests import ConnectTimeout, ReadTimeout
from requests.exceptions import RequestException

from program.media.item import ProfileData
from program.settings.manager import settings_manager
from program.scrapers.shared import _get_stremio_identifier
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

    def run(self, profile: ProfileData) -> Dict[str, str]:
        """Scrape the comet site for the given media items
        and update the object with scraped streams"""
        try:
            return self.scrape(profile)
        except RateLimitExceeded:
            if self.hour_limiter:
                self.hour_limiter.limit_hit()
            else:
                logger.warning(f"Comet ratelimit exceeded for item: {profile.log_string}")
        except ConnectTimeout:
            logger.warning(f"Comet connection timeout for item: {profile.log_string}")
        except ReadTimeout:
            logger.warning(f"Comet read timeout for item: {profile.log_string}")
        except RequestException as e:
            logger.error(f"Comet request exception: {str(e)}")
        except Exception as e:
            logger.error(f"Comet exception thrown: {str(e)}")
        return {}

    def scrape(self, profile: ProfileData) -> Dict[str, str]:
        """Wrapper for `Comet` scrape method"""
        identifier, scrape_type, imdb_id = _get_stremio_identifier(profile)
        url = f"{self.settings.url}/{self.encoded_string}/stream/{scrape_type}/{imdb_id}{identifier or ''}.json"

        if self.second_limiter:
            with self.second_limiter:
                response = get(url, timeout=self.timeout)
        else:
            response = get(url, timeout=self.timeout)

        if not response.is_ok or not getattr(response.data, "streams", None):
            return {}

        torrents: Dict[str, str] = {}
        for stream in response.data.streams:
            if stream.title == "Invalid Comet config.":
                logger.error("Invalid Comet config.")
                return {}

            infohash_pattern = regex.compile(r"(?!.*playback\/)[a-zA-Z0-9]{40}")
            infohash = infohash_pattern.search(stream.url).group()
            title = stream.title.split("\n")[0]

            if not infohash:
                logger.warning(f"Comet infohash not found for title: {title}")
                continue

            torrents[infohash] = title

        if torrents:
            logger.log("SCRAPER", f"Found {len(torrents)} streams for {profile.log_string}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {profile.log_string}")

        return torrents
