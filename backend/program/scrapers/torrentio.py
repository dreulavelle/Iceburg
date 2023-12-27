""" Torrentio scraper module """
from datetime import datetime
from requests.exceptions import RequestException
from utils.logger import logger
from utils.request import RateLimitExceeded, get, RateLimiter
from utils.settings import settings_manager
from utils.utils import parser
import program.media.state as states


class Torrentio:
    """Scraper for torrentio"""

    def __init__(self):
        self.settings = "torrentio"
        self.class_settings = settings_manager.get(self.settings)
        self.last_scrape = 0
        self.filters = self.class_settings["filter"]
        self.minute_limiter = RateLimiter(max_calls=60, period=60, raise_on_limit=True)
        self.second_limiter = RateLimiter(max_calls=1, period=1)
        self.initialized = True

    def run(self, item):
        """Scrape the torrentio site for the given media items
        and update the object with scraped streams"""
        if self._can_we_scrape(item):
            try:
                self._scrape_item(item)
            except RequestException:
                self.minute_limiter.limit_hit()
                return
            except RateLimitExceeded:
                self.minute_limiter.limit_hit()
                return

    def _scrape_item(self, item):
        data = self.api_scrape(item)
        log_string = item.title
        if item.type == "season":
            log_string = f"{item.parent.title} S{item.number}"
        if item.type == "episode":
            log_string = f"{item.parent.parent.title} S{item.parent.number}E{item.number}"
        if len(data) > 0:
            item.streams.update(data)
            logger.debug("Found %s streams for %s", len(data), log_string)
        else:
            logger.debug("Could not find streams for %s", log_string)

    def _can_we_scrape(self, item) -> bool:
        return self._is_released(item) and self._needs_new_scrape(item)

    def _is_released(self, item) -> bool:
        return item.aired_at is not None and item.aired_at < datetime.now()
    
    def _needs_new_scrape(self, item) -> bool:
        return (
            datetime.now().timestamp() - item.scraped_at
            > 60 * 30  # 30 minutes between scrapes
            or item.scraped_at == 0
        )

    def api_scrape(self, item):
        """Wrapper for torrentio scrape method"""
        with self.minute_limiter:
            if item.type == "season":
                identifier = f":{item.number}:1"
                scrape_type = "series"
                imdb_id = item.parent.imdb_id
            elif item.type == "episode":
                identifier = f":{item.parent.number}:{item.number}"
                scrape_type = "series"
                imdb_id = item.parent.parent.imdb_id
            else:
                identifier = None
                scrape_type = "movie"
                imdb_id = item.imdb_id

            url = (
                f"https://torrentio.strem.fun/{self.filters}"
                + f"/stream/{scrape_type}/{imdb_id}"
            )
            if identifier:
                url += f"{identifier}"
            with self.second_limiter:
                response = get(f"{url}.json", retry_if_failed=False)
            if response.is_ok:
                data = {}
                for stream in response.data.streams:
                    if parser.parse(stream.title):
                        data[stream.infoHash] = {
                            "name": stream.title.split("\n👤")[0],
                        }
                if len(data) > 0:
                    return data
            return {}
