""" Jackett scraper module """

from typing import Dict, Generator

from program.media.item import MediaItem, Show
from program.settings.manager import settings_manager
from program.settings.versions import models
from requests import ReadTimeout, RequestException
from RTN import RTN, Torrent, sort_torrents
from RTN.exceptions import GarbageTorrent
from utils.logger import logger
from utils.request import RateLimiter, RateLimitExceeded, get, ping


class Jackett:
    """Scraper for `Jackett`"""

    def __init__(self, hash_cache):
        self.key = "jackett"
        self.api_key = None
        self.settings = settings_manager.settings.scraping.jackett
        self.settings_model = settings_manager.settings.ranking
        self.ranking_model = models.get(self.settings_model.profile)
        self.initialized = self.validate()
        if not self.initialized and not self.api_key:
            return
        self.parse_logging = False
        self.minute_limiter = RateLimiter(
            max_calls=1000, period=3600, raise_on_limit=True
        )
        self.second_limiter = RateLimiter(max_calls=1, period=1)
        self.rtn = RTN(self.settings_model, self.ranking_model)
        self.hash_cache = hash_cache
        logger.success("Jackett initialized!")

    def validate(self) -> bool:
        """Validate Jackett settings."""
        if not self.settings.enabled:
            logger.warning("Jackett is set to disabled.")
            return False
        if self.settings.url and self.settings.api_key:
            self.api_key = self.settings.api_key
            try:
                url = f"{self.settings.url}/api/v2.0/indexers/!status:failing,test:passed/results/torznab?apikey={self.api_key}&cat=2000&t=movie&q=test"
                response = ping(url=url, timeout=60)
                if response.ok:
                    return True
            except ReadTimeout:
                logger.exception("Jackett request timed out. Check your indexers, they may be too slow to respond.")
                return False
            except Exception as e:
                logger.exception(f"Jackett failed to initialize with API Key: {e}")
                return False
        logger.info("Jackett is not configured and will not be used.")
        return False

    def run(self, item: MediaItem) -> Generator[MediaItem, None, None]:
        """Scrape the Jackett site for the given media items
        and update the object with scraped streams"""
        if not item or isinstance(item, Show):
            yield item
            return
        
        try:
            yield self.scrape(item)
        except RateLimitExceeded:
            self.minute_limiter.limit_hit()
            logger.warning(f"Jackett rate limit hit for item: {item.log_string}")
        except RequestException as e:
            logger.error(f"Jackett request exception: {e}")
        except Exception as e:
            logger.exception(f"Jackett failed to scrape item with error: {e}")
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
        """Wrapper for `Jackett` scrape method"""
        with self.minute_limiter:
            query = ""
            if item.type == "movie":
                if not hasattr(item.aired_at, "year") or not item.aired_at.year:
                    query = f"cat=2000&t=movie&q={item.title}"
                else:
                    query = f"cat=2000&t=movie&q={item.title}&year={item.aired_at.year}"
            elif item.type == "season":
                query = f"cat=5000&t=tvsearch&q={item.parent.title}&season={item.number}"
            elif item.type == "episode":
                query = f"cat=5000&t=tvsearch&q={item.parent.parent.title}&season={item.parent.number}&ep={item.number}"
            
            url = f"{self.settings.url}/api/v2.0/indexers/all/results/torznab?apikey={self.api_key}&{query}"
            
            with self.second_limiter:
                response = get(url=url, retry_if_failed=False, timeout=60)
            
            if not response.is_ok or len(response.data.get("rss", {}).get("channel", {}).get("item", [])) <= 0:
                return {}, 0
            
            streams = response.data["rss"]["channel"].get("item", [])
            if not streams:
                return {}, 0
            
            torrents = set()
            correct_title = item.get_top_title()
            if not correct_title:
                logger.debug(f"Correct title not found for {item.log_string}")
                return {}, 0
            
            for stream in streams:
                try:
                    attr = stream.get("torznab:attr", [])
                    infohash_attr = next((a for a in attr if a.get("@name") == "infohash"), None)
                    if not infohash_attr:
                        continue
                    infohash = infohash_attr.get("@value")
                except (TypeError, ValueError, AttributeError):
                    continue

                if self.hash_cache.is_blacklisted(infohash):
                    continue

                try:
                    torrent: Torrent = self.rtn.rank(
                        raw_title=stream.get("title"), infohash=infohash, correct_title=correct_title, remove_trash=True
                    )
                except GarbageTorrent:
                    continue
                
                if torrent and torrent.fetch:
                    torrents.add(torrent)
            
            scraped_torrents = sort_torrents(torrents)
            return scraped_torrents, len(streams)

