""" Zilean scraper module """

from typing import Dict

from program.media.item import ProfileData
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.ratelimiter import RateLimiter, RateLimitExceeded
from utils.request import get, ping


class Zilean:
    """Scraper for `Zilean`"""

    def __init__(self):
        self.key = "zilean"
        self.settings = settings_manager.settings.scraping.zilean
        self.timeout = self.settings.timeout
        self.rate_limiter = None
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.rate_limiter = RateLimiter(max_calls=1, period=2)
        logger.success("Zilean initialized!")

    def validate(self) -> bool:
        """Validate the Zilean settings."""
        if not self.settings.enabled:
            return False
        if not self.settings.url:
            logger.error("Zilean URL is not configured and will not be used.")
            return False
        if not isinstance(self.timeout, int) or self.timeout <= 0:
            logger.error("Zilean timeout is not set or invalid.")
            return False
        try:
            url = f"{self.settings.url}/healthchecks/ping"
            response = ping(url=url, timeout=self.timeout, specific_rate_limiter=self.rate_limiter)
            return response.is_ok
        except Exception as e:
            logger.error(f"Zilean failed to initialize: {e}")
            return False

    def run(self, data: ProfileData) -> Dict[str, str]:
        """Scrape the Zilean site for the given media items and update the object with scraped items"""
        try:
            return self.scrape(data)
        except RateLimitExceeded:
            self.rate_limiter.limit_hit()
        except Exception as e:
            logger.error(f"Zilean exception thrown: {e}")
        return {}

    def _build_query_params(self, data: ProfileData) -> Dict[str, str]:
        """Build the query params for the Zilean API"""
        params = {"Query": data.get_top_title()}
        if hasattr(data.parent, "aired_at"):
            params["Year"] = data.parent.aired_at.year
        if data.parent.type == "show":
            params["Season"] = 1
        elif data.parent.type == "season":
            params["Season"] = data.parent.number
        elif data.parent.type == "episode":
            params["Season"] = data.parent.parent.number
            params["Episode"] = data.parent.number
        return params

    def scrape(self, data: ProfileData) -> Dict[str, str]:
        """Wrapper for `Zilean` scrape method"""
        url = f"{self.settings.url}/dmm/filtered"
        params = self._build_query_params(data)

        response = get(url, params=params, timeout=self.timeout, specific_rate_limiter=self.rate_limiter)
        if not response.is_ok or not response.data:
            return {}

        torrents: Dict[str, str] = {}
        for result in response.data:
            if not result.raw_title or not result.info_hash:
                continue
            torrents[result.info_hash] = result.raw_title

        if torrents:
            logger.log("SCRAPER", f"Found {len(torrents)} streams for {data.log_string}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {data.log_string}")

        return torrents