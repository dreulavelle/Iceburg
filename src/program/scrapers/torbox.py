from typing import Dict

from requests import RequestException
from requests.exceptions import ConnectTimeout, ReadTimeout, RetryError

from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.ratelimiter import RateLimiter, RateLimitExceeded
from utils.request import get, ping


class TorBoxScraper:
    def __init__(self):
        self.key = "torbox"
        self.settings = settings_manager.settings.scraping.torbox_scraper
        self.base_url = "http://search-api.torbox.app"
        self.user_plan = None
        self.timeout = self.settings.timeout
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.rate_limiter = RateLimiter(max_calls=1, period=5)
        logger.success("TorBox Scraper is initialized")

    def validate(self) -> bool:
        """Validate the TorBox Scraper as a service"""
        if not self.settings.enabled:
            return False
        if not isinstance(self.timeout, int) or self.timeout <= 0:
            logger.error("TorBox timeout is not set or invalid.")
            return False
        try:
            response = ping(f"{self.base_url}/torrents/imdb:tt0944947?metadata=false&season=1&episode=1", timeout=self.timeout)
            return response.is_ok
        except Exception as e:
            logger.exception(f"Error validating TorBox Scraper: {e}")
            return False

    def run(self, item: MediaItem) -> Dict[str, str]:
        """Scrape the TorBox site for the given media items
        and update the object with scraped streams"""
        if not item or isinstance(item, Show):
            return {}

        try:
            return self.scrape(item)
        except RateLimitExceeded:
            self.rate_limiter.limit_hit()
        except ConnectTimeout:
            logger.log("NOT_FOUND", f"TorBox is caching request for {item.log_string}, will retry later")
        except ReadTimeout:
            logger.warning(f"TorBox read timeout for item: {item.log_string}")
        except RetryError:
            logger.warning(f"TorBox retry error for item: {item.log_string}")
        except TimeoutError:
            logger.warning(f"TorBox timeout error for item: {item.log_string}")
        except RequestException as e:
            if e.response and e.response.status_code == 418:
                logger.log("NOT_FOUND", f"TorBox has no metadata for item: {item.log_string}, unable to scrape")
            elif e.response and e.response.status_code == 500:
                logger.log("NOT_FOUND", f"TorBox is caching request for {item.log_string}, will retry later")
        except Exception as e:
            logger.error(f"TorBox exception thrown: {e}")
        return {}

    def scrape(self, item: MediaItem) -> Dict[str, str]:
        """Scrape the given item"""
        try:
            data, stream_count = self.api_scrape(item)
        except:
            raise

        if data:
            logger.log("SCRAPER", f"Found {len(data)} streams out of {stream_count} for {item.log_string}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {item.log_string}")
        return data

    def api_scrape(self, item: MediaItem) -> tuple[Dict[str, str], int]:
        """Wrapper for `Torbox` scrape method using Torbox API"""
        # Example URLs:
        # https://search-api.torbox.app/torrents/imdb:tt0080684?metadata=false
        # https://search-api.torbox.app/torrents/imdb:tt0080684?metadata=false&season=1
        # https://search-api.torbox.app/torrents/imdb:tt0080684?metadata=false&season=1&episode=1
        if isinstance(item, (Movie, Show)):
            url = f"{self.base_url}/torrents/imdb:{item.imdb_id}?metadata=false"
        elif isinstance(item, Season):
            url = f"{self.base_url}/torrents/imdb:{item.parent.imdb_id}?metadata=false&season={item.number}"
        elif isinstance(item, Episode):
            url = f"{self.base_url}/torrents/imdb:{item.parent.parent.imdb_id}?metadata=false&season={item.parent.number}&episode={item.number}"
        else:
            return {}, 0

        response = get(url, timeout=self.timeout, specific_rate_limiter=self.rate_limiter)
        if not response.is_ok or not response.data.data.torrents:
            return {}, 0

        torrents = {}
        for torrent_data in response.data.data.torrents:
            raw_title = torrent_data.raw_title
            info_hash = torrent_data.hash
            if not info_hash or not raw_title:
                continue

            torrents[info_hash] = raw_title

        return torrents, len(response.data.data.torrents)