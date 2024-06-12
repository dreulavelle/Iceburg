""" Jackett scraper module """

import queue
import threading
import time
import xml.etree.ElementTree as ET
from typing import Dict, Generator, List, Optional, Tuple

import requests
from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.settings.manager import settings_manager
from program.settings.versions import models
from pydantic import BaseModel
from requests import HTTPError, ReadTimeout, RequestException, Timeout
from RTN import RTN, Torrent, sort_torrents
from RTN.exceptions import GarbageTorrent
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

    def __init__(self, hash_cache):
        self.key = "jackett"
        self.api_key = None
        self.indexers = None
        self.hash_cache = hash_cache
        self.settings = settings_manager.settings.scraping.jackett
        self.settings_model = settings_manager.settings.ranking
        self.ranking_model = models.get(self.settings_model.profile)
        self.initialized = self.validate()
        if not self.initialized and not self.api_key:
            return
        self.rtn = RTN(self.settings_model, self.ranking_model)
        self.second_limiter = RateLimiter(max_calls=len(self.indexers), period=2)
        logger.success("Jackett initialized!")

    def validate(self) -> bool:
        """Validate Jackett settings."""
        if not self.settings.enabled:
            logger.warning("Jackett is set to disabled.")
            return False
        if self.settings.url and self.settings.api_key:
            self.api_key = self.settings.api_key
            try:
                indexers = self._get_indexers()
                if not indexers:
                    logger.error("No Jackett indexers configured.")
                    return False
                self.indexers = indexers
                self._log_indexers()
                return True
            except ReadTimeout:
                logger.error("Jackett request timed out. Check your indexers, they may be too slow to respond.")
                return False
            except Exception as e:
                logger.error(f"Jackett failed to initialize with API Key: {e}")
                return False
        logger.info("Jackett is not configured and will not be used.")
        return False

    def run(self, item: MediaItem) -> Generator[MediaItem, None, None]:
        """Scrape the Jackett site for the given media items
        and update the object with scraped streams"""
        if not item:
            yield item
            return

        try:
            yield self.scrape(item)
        except RateLimitExceeded:
            self.second_limiter.limit_hit()
            logger.warning(f"Jackett rate limit hit for item: {item.log_string}")
        except RequestException as e:
            logger.error(f"Jackett request exception: {e}")
        except Exception as e:
            logger.error(f"Jackett failed to scrape item with error: {e}")
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
        return self._process_results(item, results)

    def _thread_target(self, item: MediaItem, indexer: JackettIndexer, results_queue: queue.Queue):
        try:
            start_time = time.perf_counter()
            result = self._search_indexer(item, indexer)
            search_duration = time.perf_counter() - start_time
        except TypeError as e:
            logger.error(f"Invalid Type for {item.log_string}: {e}")
            result = []
            search_duration = 0
        item_title = item.log_string # probably not needed, but since its concurrent, it's better to be safe
        logger.debug(f"Scraped {item_title} from {indexer.title} in {search_duration:.2f} seconds with {len(result)} results")
        results_queue.put(result)

    def _search_indexer(self, item: MediaItem, indexer: JackettIndexer) -> List[Tuple[str, str]]:
        """Search for the given item on the given indexer"""
        if isinstance(item, Movie):
            return self._search_movie_indexer(item, indexer)
        elif isinstance(item, (Season, Episode)):
            return self._search_series_indexer(item, indexer)
        else:
            raise TypeError("Only Movie and Series is allowed!")

    def _collect_results(self, results_queue: queue.Queue) -> List[Tuple[str, str]]:
        """Collect results from the queue"""
        results = []
        while not results_queue.empty():
            results.extend(results_queue.get())
        return results

    def _process_results(self, item: MediaItem, results: List[Tuple[str, str]]) -> Tuple[Dict[str, Torrent], int]:
        """Process the results and return the torrents"""
        torrents = set()
        correct_title = item.get_top_title()
        if not correct_title:
            logger.debug(f"Correct title not found for {item.log_string}")
            return {}, 0

        for result in results:
            if result[1] is None or self.hash_cache.is_blacklisted(result[1]):
                continue
            try:
                torrent: Torrent = self.rtn.rank(
                    raw_title=result[0], infohash=result[1], correct_title=correct_title, remove_trash=True
                )
            except GarbageTorrent:
                continue
            if torrent and torrent.fetch:
                torrents.add(torrent)
        scraped_torrents = sort_torrents(torrents)
        return scraped_torrents, len(scraped_torrents)

    def _search_movie_indexer(self, item: MediaItem, indexer: JackettIndexer) -> List[Tuple[str, str]]:
        """Search for movies on the given indexer"""
        params = {
            "apikey": self.api_key,
            "t": "movie",
            "cat": "2000",
            "q": item.title,
            "year": item.aired_at.year if hasattr(item.aired_at, "year") and item.aired_at.year else None
        }

        if indexer.movie_search_capabilities and "imdbid" in indexer.movie_search_capabilities:
            params["imdbid"] = item.imdb_id

        url = f"{self.settings.url}/api/v2.0/indexers/{indexer.id}/results/torznab/api"
        return self._fetch_results(url, params, indexer.title, "movie")

    def _search_series_indexer(self, item: MediaItem, indexer: JackettIndexer) -> List[Tuple[str, str]]:
        """Search for series on the given indexer"""
        q, season, ep = self._get_series_search_params(item)

        params = {
            "apikey": self.api_key,
            "t": "tvsearch",
            "cat": "5000",
            "q": q,
            "season": season,
            "ep": ep
        }

        if indexer.tv_search_capabilities and "imdbid" in indexer.tv_search_capabilities:
            params["imdbid"] = item.imdb_id

        url = f"{self.settings.url}/api/v2.0/indexers/{indexer.id}/results/torznab/api"
        return self._fetch_results(url, params, indexer.title, "series")

    def _get_series_search_params(self, item: MediaItem) -> Tuple[str, int, Optional[int]]:
        """Get search parameters for series"""
        if isinstance(item, Season):
            return item.get_top_title(), item.number, None
        elif isinstance(item, Episode):
            return item.get_top_title(), item.parent.number, item.number
        return "", 0, None

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

    def _fetch_results(self, url: str, params: Dict[str, str], indexer_title: str, search_type: str) -> List[Tuple[str, str]]:
        """Fetch results from the given indexer"""
        with self.second_limiter:
            try:
                response = requests.get(url, params=params)
                response.raise_for_status()
                return self._parse_xml(response.text)
            except (HTTPError, ConnectionError, Timeout) as e:
                logger.debug(f"Indexer failed to fetch results for {search_type}: {indexer_title}")
            except Exception as e:
                if "Jackett.Common.IndexerException" in str(e):
                    logger.error(f"Indexer exception while fetching results from {indexer_title} ({search_type}): {e}")
                else:
                    logger.error(f"Exception while fetching results from {indexer_title} ({search_type}): {e}")
            return []

    def _parse_xml(self, xml_content: str) -> list[tuple[str, str]]:
        """Parse the torrents from the XML content"""
        xml_root = ET.fromstring(xml_content)
        result_list = []
        for item in xml_root.findall(".//item"):
            infoHash = item.find(
                ".//torznab:attr[@name='infohash']",
                namespaces={"torznab": "http://torznab.com/schemas/2015/feed"}
            )
            if infoHash is None or len(infoHash.attrib["value"]) != 40:
                continue
            result_list.append((item.find(".//title").text, infoHash.attrib["value"]))
        return result_list

    def _log_indexers(self) -> None:
        """Log the indexers information"""
        for indexer in self.indexers:
            logger.debug(f"Indexer: {indexer.title} - {indexer.link} - {indexer.type}")
            if not indexer.movie_search_capabilities:
                logger.debug(f"Movie search not available for {indexer.title}")
            if not indexer.tv_search_capabilities:
                logger.debug(f"TV search not available for {indexer.title}")
