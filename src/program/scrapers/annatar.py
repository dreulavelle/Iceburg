""" Annatar scraper module """
from typing import Dict

from requests import ConnectTimeout, ReadTimeout
from requests.exceptions import RequestException

from program.media.item import ProfileData
from program.settings.manager import settings_manager
from program.scrapers.shared import _get_stremio_identifier
from utils.logger import logger
from utils.ratelimiter import RateLimiter, RateLimitExceeded
from utils.request import get


class Annatar:
    """Scraper for `Annatar`"""

    def __init__(self):
        self.key = "annatar"
        self.url = None
        self.settings = settings_manager.settings.scraping.annatar
        self.query_limits = "limit=2000&timeout=10"
        self.timeout = self.settings.timeout
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.second_limiter = RateLimiter(max_calls=1, period=2) if self.settings.ratelimit else None
        logger.success("Annatar initialized!")

    def validate(self) -> bool:
        """Validate the Annatar settings."""
        if not self.settings.enabled:
            return False
        if not isinstance(self.settings.url, str) or not self.settings.url:
            logger.error("Annatar URL is not configured and will not be used.")
            return False
        if not isinstance(self.settings.limit, int) or self.settings.limit <= 0:
            logger.error("Annatar limit is not set or invalid.")
            return False
        if not isinstance(self.timeout, int) or self.timeout <= 0:
            logger.error("Annatar timeout is not set or invalid.")
            return False
        if not isinstance(self.settings.ratelimit, bool):
            logger.error("Annatar ratelimit must be a valid boolean.")
            return False
        try:
            response = get(f"{self.settings.url}/manifest.json", timeout=15)
            if not response.is_ok:
                return False
            return True
        except ReadTimeout:
            logger.debug("Annatar read timeout during initialization.")
            return False
        except Exception as e:
            logger.error(f"Annatar failed to initialize: {e}")
            return False

    def run(self, profile: ProfileData) -> Dict[str, str]:
        """Scrape the Annatar site for the given media items
        and update the object with scraped streams"""
        try:
            return self.scrape(profile)
        except RateLimitExceeded:
            if self.second_limiter:
                self.second_limiter.limit_hit()
        except ConnectTimeout:
            logger.debug(f"Annatar connection timeout for item: {profile.log_string}")
        except ReadTimeout:
            logger.debug(f"Annatar read timeout for item: {profile.log_string}")
        except RequestException as e:
            if e.response.status_code == 525:
                logger.error(f"Annatar SSL handshake failed for item: {profile.log_string}")
            elif e.response.status_code == 429:
                if self.second_limiter:
                    self.second_limiter.limit_hit()
            else:
                logger.error(f"Annatar request exception: {e}")
        except Exception as e:
            logger.error(f"Annatar failed to scrape item with error: {e}", exc_info=True)
        return {}

    def scrape(self, profile: ProfileData) -> Dict[str, str]:
        """Wrapper for `Annatar` scrape method"""
        identifier, scrape_type, imdb_id = _get_stremio_identifier(profile)

        if identifier is not None:
            url = f"{self.settings.url}/search/imdb/{scrape_type}/{imdb_id}?{identifier}&{self.query_limits}"
        else:
            url = f"{self.settings.url}/search/imdb/{scrape_type}/{imdb_id}?{self.query_limits}"

        if self.second_limiter:
            with self.second_limiter:
                response = get(url, timeout=self.timeout)
        else:
            response = get(url, timeout=self.timeout)

        if not response.is_ok or not response.data.media:
            return {}

        torrents: Dict[str, str] = {}
        for stream in response.data.media:
            if not stream.hash:
                continue
            torrents[stream.hash] = stream.title

        if torrents:
            logger.log("SCRAPER", f"Found {len(torrents)} streams for {profile.log_string}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {profile.log_string}")

        return torrents