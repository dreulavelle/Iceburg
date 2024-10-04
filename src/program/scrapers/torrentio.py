""" Torrentio scraper module """
from typing import Dict

from program.media.item import ProfileData
from program.scrapers.shared import _get_stremio_identifier
from program.settings.manager import settings_manager
from program.settings.models import TorrentioConfig
from utils.logger import logger
from utils.ratelimiter import RateLimiter, RateLimitExceeded
from utils.request import get, ping


class Torrentio:
    """Scraper for `Torrentio`"""

    def __init__(self):
        self.key = "torrentio"
        self.settings: TorrentioConfig = settings_manager.settings.scraping.torrentio
        self.timeout: int = self.settings.timeout
        self.initialized: bool = self.validate()
        if not self.initialized:
            return
        self.rate_limiter: RateLimiter = RateLimiter(max_calls=1, period=5)
        logger.success("Torrentio initialized!")

    def validate(self) -> bool:
        """Validate the Torrentio settings."""
        if not self.settings.enabled:
            return False
        if not self.settings.url:
            logger.error("Torrentio URL is not configured and will not be used.")
            return False
        if not isinstance(self.timeout, int) or self.timeout <= 0:
            logger.error("Torrentio timeout is not set or invalid.")
            return False
        try:
            url = f"{self.settings.url}/{self.settings.filter}/manifest.json"
            response = ping(url=url, timeout=10)
            if response.is_ok:
                return True
        except Exception as e:
            logger.error(f"Torrentio failed to initialize: {e}", )
            return False
        return True

    def run(self, profile: ProfileData) -> Dict[str, str]:
        """Scrape Torrentio with the given media item for streams"""
        try:
            return self.scrape(profile)
        except RateLimitExceeded:
            self.rate_limiter.limit_hit()
        except Exception as e:
            logger.error(f"Torrentio exception thrown: {str(e)}")
        return {}

    def scrape(self, profile: ProfileData) -> Dict[str, str]:
        """Wrapper for `Torrentio` scrape method"""
        identifier, scrape_type, imdb_id = _get_stremio_identifier(profile)
        if not imdb_id:
            return {}

        url = f"{self.settings.url}/{self.settings.filter}/stream/{scrape_type}/{imdb_id}"
        if identifier:
            url += identifier

        response = get(f"{url}.json", timeout=self.timeout, specific_rate_limiter=self.rate_limiter)
        if not response.is_ok or not response.data.streams:
            return {}

        torrents: Dict[str, str] = {}
        for stream in response.data.streams:
            if not stream.infoHash:
                continue

            stream_title = stream.title.split("\n👤")[0]
            raw_title = stream_title.split("\n")[0]
            torrents[stream.infoHash] = raw_title

        if torrents:
            logger.log("SCRAPER", f"Found {len(torrents)} streams for {profile.log_string}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {profile.log_string}")

        return torrents