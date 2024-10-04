""" Orionoid scraper module """
from typing import Dict

from program.media.item import MediaItem, ProfileData
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.ratelimiter import RateLimiter, RateLimitExceeded
from utils.request import get

KEY_APP = "D3CH6HMX9KD9EMD68RXRCDUNBDJV5HRR"


class Orionoid:
    """Scraper for `Orionoid`"""

    def __init__(self):
        self.key = "orionoid"
        self.base_url = "https://api.orionoid.com"
        self.settings = settings_manager.settings.scraping.orionoid
        self.timeout = self.settings.timeout
        self.is_premium = False
        self.is_unlimited = False
        self.initialized = False
        if self.validate():
            self.is_premium = self.check_premium()
            self.initialized = True
        else:
            return
        self.rate_limiter = RateLimiter(max_calls=1, period=5)
        logger.success("Orionoid initialized!")

    def validate(self) -> bool:
        """Validate the Orionoid class_settings."""
        if not self.settings.enabled:
            return False
        if len(self.settings.api_key) != 32 or self.settings.api_key == "":
            logger.error("Orionoid API Key is not valid or not set. Please check your settings.")
            return False
        if not isinstance(self.timeout, int) or self.timeout <= 0:
            logger.error("Orionoid timeout is not set or invalid.")
            return False
        try:
            url = f"{self.base_url}?keyapp={KEY_APP}&keyuser={self.settings.api_key}&mode=user&action=retrieve"
            response = get(url, retry_if_failed=True, timeout=self.timeout)
            if response.is_ok and hasattr(response.data, "result"):
                if response.data.result.status != "success":
                    logger.error(
                        f"Orionoid API Key is invalid. Status: {response.data.result.status}",
                    )
                    return False
                if not response.is_ok:
                    logger.error(
                        f"Orionoid Status Code: {response.status_code}, Reason: {response.data.reason}",
                    )
                    return False
                if response.data.data.subscription.package.type == "unlimited":
                    self.is_unlimited = True
            return True
        except Exception as e:
            logger.exception(f"Orionoid failed to initialize: {e}")
            return False

    def check_premium(self) -> bool:
        """Check if the user is active, has a premium account, and has RealDebrid service enabled."""
        url = f"{self.base_url}?keyapp={KEY_APP}&keyuser={self.settings.api_key}&mode=user&action=retrieve"
        response = get(url, retry_if_failed=False)
        if response.is_ok and hasattr(response.data, "data"):
            active = response.data.data.status == "active"
            premium = response.data.data.subscription.package.premium
            debrid = response.data.data.service.realdebrid
            if active and premium and debrid:
                return True
        return False

    def check_limit(self) -> bool:
        """Check if the user has exceeded the rate limit for the Orionoid API."""
        url = f"{self.base_url}?keyapp={KEY_APP}&keyuser={self.settings.api_key}&mode=user&action=retrieve"
        try:
            response = get(url)
            if response.is_ok and hasattr(response.data, "data"):
                remaining = response.data.data.requests.streams.daily.remaining
                if remaining is None:
                    return False
                elif remaining and remaining <= 0:
                    return True
        except Exception as e:
            logger.error(f"Orionoid failed to check limit: {e}")
            return False

    def run(self, profile: ProfileData) -> Dict[str, str]:
        """Scrape the orionoid site for the given media items and update the object with scraped streams."""
        if not self.is_unlimited:
            limit_hit = self.check_limit()
            if limit_hit:
                logger.debug("Orionoid daily limits have been reached")
                return {}

        try:
            return self.scrape(profile)
        except RateLimitExceeded:
            self.rate_limiter.limit_hit()
        except Exception as e:
            logger.opt(exception=True).error(f"Orionoid exception for item: {profile.log_string} - Exception: {e}")
        return {}

    def _build_query_params(self, profile: ProfileData) -> dict:
        """Construct the query parameters for the Orionoid API based on the media item."""
        media_type = "movie" if profile.parent.type == "movie" else "show"
        imdbid: str = profile.parent.get_top_imdb_id()
        if not imdbid:
            raise ValueError("IMDB ID is missing for the media item")

        params = {
            "keyapp": KEY_APP,
            "keyuser": self.settings.api_key,
            "mode": "stream",
            "action": "retrieve",
            "type": media_type,
            "idimdb": imdbid[2:],
            "streamtype": "torrent",
            "protocoltorrent": "magnet"
        }

        if profile.parent.type == "season":
            params["numberseason"] = profile.parent.number
        elif profile.parent.type == "episode":
            params["numberseason"] = profile.parent.parent.number
            params["numberepisode"] = profile.parent.number

        if self.settings.cached_results_only:
            params["access"] = "realdebridtorrent"
            params["debridlookup"] = "realdebrid"

        for key, value in self.settings.parameters.items():
            if key not in params:
                params[key] = value

        return params

    def scrape(self, profile: ProfileData) -> Dict[str, str]:
        """Wrapper for `Orionoid` scrape method"""
        params = self._build_query_params(profile)
        response = get(self.base_url, params=params, timeout=self.timeout, specific_rate_limiter=self.rate_limiter)
        if not response.is_ok or not hasattr(response.data, "data"):
            return {}

        torrents = {}
        for stream in response.data.data.streams:
            if not stream.file.hash or not stream.file.name:
                continue
            torrents[stream.file.hash] = stream.file.name

        if torrents:
            logger.log("SCRAPER", f"Found {len(torrents)} streams for {profile.log_string}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {profile.log_string}")

        return torrents