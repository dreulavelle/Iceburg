""" Jackett scraper module """

import queue
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Generator, List, Optional, Tuple

import requests
from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.settings.manager import settings_manager
from pydantic import BaseModel
from requests import HTTPError, ReadTimeout, RequestException, Timeout
from utils.logger import logger
from utils.request import RateLimiter, RateLimitExceeded


class JackettIndexer(BaseModel):
    """Indexer model for Jackett"""
    title: Optional[str] = None
    id: Optional[str] = None
    link: Optional[str] = None
    type: Optional[str] = None
    language: Optional[str] = None
    tv_search_capabilities: Optional[List[str]] = None
    movie_search_capabilities: Optional[List[str]] = None


class Jackett:
    """Scraper for `Jackett`"""

    def __init__(self):
        self.key = "jackett"
        self.api_key = None
        self.indexers = None
        self.settings = settings_manager.settings.scraping.jackett
        self.timeout = self.settings.timeout
        self.second_limiter = None
        self.rate_limit = self.settings.ratelimit
        self.initialized = self.validate()
        if not self.initialized and not self.api_key:
            return
        logger.success("Jackett initialized!")

    def validate(self) -> bool:
        """Validate Jackett settings."""
        if not self.settings.enabled:
            logger.warning("Jackett is set to disabled.")
            return False
        if self.settings.url and self.settings.api_key:
            self.api_key = self.settings.api_key
            try:
                if not isinstance(self.timeout, int) or self.timeout <= 0:
                    logger.error("Jackett timeout is not set or invalid.")
                    return False
                if not isinstance(self.settings.ratelimit, bool):
                    logger.error("Jackett ratelimit must be a valid boolean.")
                    return False
                indexers = self._get_indexers()
                if not indexers:
                    logger.error("No Jackett indexers configured.")
                    return False
                self.indexers = indexers
                if self.rate_limit:
                    self.second_limiter = RateLimiter(max_calls=len(self.indexers), period=2)
                self._log_indexers()
                return True
            except ReadTimeout:
                logger.error("Jackett request timed out. Check your indexers, they may be too slow to respond.")
                return False
            except Exception as e:
                logger.error(f"Jackett failed to initialize with API Key: {e}")
                return False
        logger.warning("Jackett is not configured and will not be used.")
        return False

    def run(self, item: MediaItem) -> Generator[MediaItem, None, None]:
        """Scrape the Jackett site for the given media items
        and update the object with scraped streams"""
        try:
            return self.scrape(item)
        except RateLimitExceeded:
            if self.second_limiter:
                self.second_limiter.limit_hit()
        except RequestException as e:
            logger.error(f"Jackett request exception: {e}")
        except Exception as e:
            logger.error(f"Jackett failed to scrape item with error: {e}")
        return {}

    def scrape(self, item: MediaItem) -> Dict[str, str]:
        """Scrape the given media item"""
        data, stream_count = self.api_scrape(item)
        if data:
            logger.log("SCRAPER", f"Found {len(data)} streams out of {stream_count} for {item.log_string}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {item.log_string}")
        return data

    def api_scrape(self, item: MediaItem) -> tuple[Dict[str, str], int]:
        """Wrapper for `Jackett` scrape method"""
        results_queue = queue.Queue()
        threads = [
            threading.Thread(target=self._thread_target, args=(item, indexer, results_queue))
            for indexer in self.indexers
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        results = self._collect_results(results_queue)
        return self._process_results(results)

    def _thread_target(self, item: MediaItem, indexer: JackettIndexer, results_queue: queue.Queue):
        """Thread target for searching indexers"""
        try:
            start_time = time.perf_counter()
            result = self._search_indexer(item, indexer)
            search_duration = time.perf_counter() - start_time
        except TypeError as e:
            logger.error(f"Invalid Type for {item.log_string}: {e}")
            result = []
            search_duration = 0
        item_title = item.log_string
        
        item_year =  item.get_top_year()

        if not item_year and hasattr(item, "year") and item.year:
            # haven't seen anything without a year but fallback ?
            item_year = item.year
        logger.debug(f"Scraped {item_title} {item_year} from {indexer.title} in {search_duration:.2f} seconds with {len(result)} results")
        results_queue.put(result)

    def _search_indexer(self, item: MediaItem, indexer: JackettIndexer) -> List[Tuple[str, str]]:
        """Search for the given item on the given indexer"""
        if isinstance(item, Movie):
            return self._search_movie_indexer(item, indexer)
        elif isinstance(item, (Show, Season, Episode)):
            return self._search_series_indexer(item, indexer)
        else:
            raise TypeError("Only Movie and Series is allowed!")

    def _collect_results(self, results_queue: queue.Queue) -> List[Tuple[str, str]]:
        """Collect results from the queue"""
        results = []
        while not results_queue.empty():
            results.extend(results_queue.get())
        return results

    def _process_results(self, results: List[Tuple[str, str]]) -> Tuple[Dict[str, str], int]:
        """Process the results and return the torrents"""
        torrents: Dict[str, str] = {}
        for result in results:
            if result[1] is None:
                continue
            # infohash: raw_title
            torrents[result[1]] = result[0]
        return torrents, len(results)

    def _search_movie_indexer(self, item: MediaItem, indexer: JackettIndexer) -> List[Tuple[str, str]]:
        """Search for movies on the given indexer"""
        if indexer.movie_search_capabilities == None:
            return []
        params = {
            "apikey": self.api_key,
            "t": "movie",
            "cat": "2000",
            "q": item.title,
        }
        year = None
        if hasattr(item.aired_at, "year") and item.aired_at.year: 
            year = item.aired_at.year
        
        if indexer.movie_search_capabilities and "imdbid" in indexer.movie_search_capabilities:
            params["imdbid"] = item.imdb_id

        url = f"{self.settings.url}/api/v2.0/indexers/{indexer.id}/results/torznab/api"
        return self._fetch_results(url, params, indexer.title, "movie", year)

    def _search_series_indexer(self, item: MediaItem, indexer: JackettIndexer) -> List[Tuple[str, str]]:
        """Search for series on the given indexer"""
        if indexer.tv_search_capabilities == None:
            return []
        
        q, item_year, season_year, season, ep = self._get_series_search_params(item)

        if not q:
            logger.debug(f"No search query found for {item.log_string}")
            return []

        params = {
            "apikey": self.api_key,
            "t": "tvsearch",
            "cat": "5000",
            "q": q
        }

        if ep and indexer.tv_search_capabilities and "ep" in indexer.tv_search_capabilities: params["ep"] = ep 
        if season and indexer.tv_search_capabilities and "season" in indexer.tv_search_capabilities: params["season"] = season
        
        
        
        if indexer.tv_search_capabilities and "imdbid" in indexer.tv_search_capabilities:
            params["imdbid"] = item.imdb_id if isinstance(item, [Episode, Show]) else item.parent.imdb_id

        url = f"{self.settings.url}/api/v2.0/indexers/{indexer.id}/results/torznab/api"
        return self._fetch_results(url, params, indexer.title, "series", item_year, season_year)

    def _get_series_search_params(self, item: MediaItem) -> Tuple[str, int, Optional[int]]:
        """Get search parameters for series"""
        if isinstance(item, Show):
            return item.get_top_title(), item.get_top_year(), None, None, None
        elif isinstance(item, Season):
            return item.get_top_title(), item.get_top_year(), item.get_season_year(), item.number, None
        elif isinstance(item, Episode):
            return item.get_top_title(), item.get_top_year(), item.get_season_year(), item.parent.number, item.number
        return "", None, None, 0, None

    def _get_indexers(self) -> List[JackettIndexer]:
        """Get the indexers from Jackett"""
        url = f"{self.settings.url}/api/v2.0/indexers/all/results/torznab/api?apikey={self.api_key}&t=indexers&configured=true"
        try:
            response = requests.get(url)
            response.raise_for_status()
            return self._get_indexer_from_xml(response.text)
        except Exception as e:
            logger.error(f"Exception while getting indexers from Jackett: {e}")
            return []

    def _get_indexer_from_xml(self, xml_content: str) -> list[JackettIndexer]:
        """Parse the indexers from the XML content"""
        xml_root = ET.fromstring(xml_content)

        indexer_list = []
        for item in xml_root.findall(".//indexer"):
            indexer_data = {
                "title": item.find("title").text,
                "id": item.attrib["id"],
                "link": item.find("link").text,
                "type": item.find("type").text,
                "language": item.find("language").text.split("-")[0],
                "movie_search_capabilities": None,
                "tv_search_capabilities": None
            }
            movie_search = item.find(".//searching/movie-search[@available='yes']")
            tv_search = item.find(".//searching/tv-search[@available='yes']")
            if movie_search is not None:
                indexer_data["movie_search_capabilities"] = movie_search.attrib["supportedParams"].split(",")
            if tv_search is not None:
                indexer_data["tv_search_capabilities"] = tv_search.attrib["supportedParams"].split(",")
            indexer = JackettIndexer(**indexer_data)
            indexer_list.append(indexer)
        return indexer_list

    def _fetch_results(self, url: str, params: Dict[str, str], indexer_title: str, search_type: str, item_year: int = None, season_year: int = None ) -> List[Tuple[str, str]]:
        """Fetch results from the given indexer"""
        try:
            if self.second_limiter:
                with self.second_limiter:
                    response = requests.get(url, params=params, timeout=self.timeout)
            else:
                response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return self._parse_xml(response.text, item_year, season_year)
        except (HTTPError, ConnectionError, Timeout):
            logger.debug(f"Indexer failed to fetch results for {search_type}: {indexer_title}")
        except Exception as e:
            if "Jackett.Common.IndexerException" in str(e):
                logger.error(f"Indexer exception while fetching results from {indexer_title} ({search_type}): {e}")
            else:
                logger.error(f"Exception while fetching results from {indexer_title} ({search_type}): {e}")
        return []

    def _parse_xml(self, xml_content: str, item_year: int = None, season_year: int = None) -> list[tuple[str, str]]:
        """
        Parse the torrents from the XML content, 
        ensuring the year in the publication date is equal to or after the specified item year,
        or the title contains an exact match of item_year or season_year.
        """
        xml_root = ET.fromstring(xml_content)
        result_list = []
        #  regex to match years and avoid resolutions like 1080x1920 or 1920 x 1080
        year_pattern = re.compile(r'\b(19\d{2}|20\d{2})(?![pP]|\d|x\s*\d{3,4}|\s*x\s*\d{3,4})\b')
        for item in xml_root.findall(".//item"):
            infoHash = item.find(
                ".//torznab:attr[@name='infohash']",
                namespaces={"torznab": "http://torznab.com/schemas/2015/feed"}
            )
            if infoHash is None or len(infoHash.attrib["value"]) != 40:
                continue

            title = item.find(".//title").text
            pubDate_text = item.find(".//pubDate").text
            try:
                pubDate = datetime.strptime(pubDate_text, '%a, %d %b %Y %H:%M:%S %z')
                pubDate_year = pubDate.year
            except ValueError:
                logger.debug(f"Invalid pubDate format: {pubDate_text}")
                continue  # Skip if the date format is incorrect or missing

            if item_year is not None or season_year is not None:
                title_years = year_pattern.findall(title)
                title_year_valid = any(
                    (item_year is not None and int(year) == item_year) or
                    (season_year is not None and int(year) == season_year)
                    for year in title_years
                )
                if not (title_year_valid or (pubDate_year >= item_year and not any(year for year in title_years if year != str(item_year)))):
                    logger.debug(f"Removing invalid torrent name: {title}, Publish Date: {pubDate} Publish Year: {pubDate_year} Title years matched {title_years}")
                    continue  # Skip the entry if conditions based on item_year or season_year are not met

            result_list.append((title, infoHash.attrib["value"]))

        return result_list

    def _log_indexers(self) -> None:
        """Log the indexers information"""
        for indexer in self.indexers:
            # logger.debug(f"Indexer: {indexer.title} - {indexer.link} - {indexer.type}")
            if not indexer.movie_search_capabilities:
                logger.debug(f"Movie search not available for {indexer.title}")
            if not indexer.tv_search_capabilities:
                logger.debug(f"TV search not available for {indexer.title}")
