""" Jackett scraper module """
import traceback
from typing import Optional
from pydantic import BaseModel
from requests import ReadTimeout, RequestException
from utils.logger import logger
from utils.settings import settings_manager
from utils.parser import parser
from utils.request import RateLimitExceeded, get, RateLimiter, ping


class JackettConfig(BaseModel):
    enabled: bool
    url: Optional[str]
    api_key: Optional[str]


class Jackett:
    """Scraper for `Jackett`"""

    def __init__(self, _):
        self.key = "jackett"
        self.api_key = None
        self.settings = JackettConfig(**settings_manager.get(f"scraping.{self.key}"))
        self.initialized = self.validate_settings()
        if not self.initialized and not self.api_key:
            return
        self.minute_limiter = RateLimiter(max_calls=60, period=60, raise_on_limit=True)
        self.second_limiter = RateLimiter(max_calls=1, period=10)
        self.parse_logging = False
        logger.info("Jackett initialized!")

    def validate_settings(self) -> bool:
        """Validate Jackett settings."""
        if not self.settings.enabled:
            logger.debug("Jackett is set to disabled.")
            return False
        if self.settings.url and self.settings.api_key:
            self.api_key = self.settings.api_key
            try:
                url = f"{self.settings.url}/api/v2.0/indexers/!status:failing,test:passed/results/torznab?apikey={self.api_key}&cat=2000&t=movie&q=test"
                response = ping(url=url, timeout=60)
                if response.ok:
                    return True
            except ReadTimeout:
                return True
            except Exception as e:
                logger.exception("Jackett failed to initialize with API Key: %s", e)
                return False
        if self.settings.url:
            try:
                url = f"{self.settings.url}/api/v2.0/server/config"
                response = get(url=url, retry_if_failed=False, timeout=60)
                if response.is_ok and response.data.api_key is not None:
                    self.api_key = response.data.api_key
                    return True
                if not response.is_ok:
                    return False
            except ReadTimeout:
                return True
            except Exception as e:
                logger.exception("Jackett failed to initialize: %s", e)
                return False
        logger.info("Jackett is not configured and will not be used.")
        return False

    def run(self, item):
        """Scrape Jackett for the given media items"""
        if item is None or not self.initialized:
            return
        try:
            self._scrape_item(item)
        except RateLimitExceeded as e:
            self.minute_limiter.limit_hit()
            logger.warn("Jackett rate limit hit for item: %s", item.log_string)
            return
        except RequestException as e:
            self.minute_limiter.limit_hit()
            logger.exception("Jackett request exception: %s", e, exc_info=True)
            return
        except Exception as e:
            self.minute_limiter.limit_hit()
            # logger.debug("Jackett exception for item: %s - Exception: %s", item.log_string, e.args[0], exc_info=True)
            # logger.debug("Exception details: %s", traceback.format_exc())
            return

    def _scrape_item(self, item):
        """Scrape the given media item"""
        data, stream_count = self.api_scrape(item)
        if len(data) > 0:
            item.streams.update(data)
            logger.info("Found %s streams out of %s for %s", len(data), stream_count, item.log_string)
        else:
            logger.debug("Could not find streams for %s", item.log_string)

    def api_scrape(self, item):
        """Wrapper for `Jackett` scrape method"""
        # https://github.com/Jackett/Jackett/wiki/Jackett-Categories
        with self.minute_limiter:
            query = ""
            if item.type == "movie":
                query = f"&cat=2000,2010,2020,2030,2040,2045,2050,2080&t=movie&q={item.title}&year{item.aired_at.year}"
            if item.type == "season":
                query = f"&cat=5000,5010,5020,5030,5040,5045,5050,5060,5070,5080&t=tvsearch&q={item.parent.title}&season={item.number}"
            if item.type == "episode":
                query = f"&cat=5000,5010,5020,5030,5040,5045,5050,5060,5070,5080&t=tvsearch&q={item.parent.parent.title}&season={item.parent.number}&ep={item.number}"
            url = f"{self.settings.url}/api/v2.0/indexers/!status:failing,test:passed/results/torznab?apikey={self.api_key}{query}"
            with self.second_limiter:
                response = get(url=url, retry_if_failed=False, timeout=60)
            if response.is_ok:
                data = {}
                streams = response.data["rss"]["channel"].get("item", [])
                parsed_data_list = [parser.parse(item, stream.get("title")) for stream in streams if type(stream) != str]
                for stream, parsed_data in zip(streams, parsed_data_list):
                    if type(stream) == str:
                        logger.debug("Found another string: %s", stream)
                        continue
                    if parsed_data.get("fetch", True) and parsed_data.get("title_match", False):
                        attr = stream.get("torznab:attr", [])
                        infohash_attr = next((a for a in attr if a.get("@name") == "infohash"), None)
                        if infohash_attr:
                            infohash = infohash_attr.get("@value")
                            data[infohash] = {"name": stream.get("title")}
                if self.parse_logging:
                    for parsed_data in parsed_data_list:
                        logger.debug("Jackett Fetch: %s - Parsed item: %s", parsed_data["fetch"], parsed_data["string"])
                if data:
                    item.parsed_data.extend(parsed_data_list)
                    return data, len(streams)
                return {}, 0
