""" Prowlarr scraper module """

import json
import queue
import threading
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import requests
from pydantic import BaseModel
from requests import HTTPError, ReadTimeout, RequestException, Timeout

from program.media.item import ProfileData
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.ratelimiter import RateLimiter, RateLimitExceeded


class ProwlarrIndexer(BaseModel):
    """Indexer model for Prowlarr"""
    title: Optional[str] = None
    id: Optional[str] = None
    link: Optional[str] = None
    type: Optional[str] = None
    language: Optional[str] = None
    tv_search_capabilities: Optional[List[str]] = None
    movie_search_capabilities: Optional[List[str]] = None


class Prowlarr:
    """Scraper for `Prowlarr`"""

    def __init__(self):
        self.key = "Prowlarr"
        self.api_key = None
        self.indexers = None
        self.settings = settings_manager.settings.scraping.prowlarr
        self.timeout = self.settings.timeout
        self.second_limiter = None
        self.rate_limit = self.settings.ratelimit
        self.initialized = self.validate()
        if not self.initialized and not self.api_key:
            return
        logger.success("Prowlarr initialized!")

    def validate(self) -> bool:
        """Validate Prowlarr settings."""
        if not self.settings.enabled:
            return False
        if self.settings.url and self.settings.api_key:
            self.api_key = self.settings.api_key
            try:
                if not isinstance(self.timeout, int) or self.timeout <= 0:
                    logger.error("Prowlarr timeout is not set or invalid.")
                    return False
                if not isinstance(self.settings.ratelimit, bool):
                    logger.error("Prowlarr ratelimit must be a valid boolean.")
                    return False
                indexers = self._get_indexers()
                if not indexers:
                    logger.error("No Prowlarr indexers configured.")
                    return False
                self.indexers = indexers
                if self.rate_limit:
                    self.second_limiter = RateLimiter(max_calls=len(self.indexers), period=self.settings.limiter_seconds)
                self._log_indexers()
                return True
            except ReadTimeout:
                logger.error("Prowlarr request timed out. Check your indexers, they may be too slow to respond.")
                return False
            except Exception as e:
                logger.error(f"Prowlarr failed to initialize with API Key: {e}")
                return False
        logger.warning("Prowlarr is not configured and will not be used.")
        return False

    def run(self, profile: ProfileData) -> Dict[str, str]:
        """Scrape the Prowlarr site for the given media items
        and update the object with scraped streams"""
        if not profile:
            return {}

        try:
            return self.scrape(profile)
        except RateLimitExceeded:
            if self.second_limiter:
                self.second_limiter.limit_hit()
            else:
                logger.warning(f"Prowlarr ratelimit exceeded for item: {profile.parent.log_string}")
        except RequestException as e:
            logger.error(f"Prowlarr request exception: {e}")
        except Exception as e:
            logger.error(f"Prowlarr failed to scrape item with error: {e}")
        return {}

    def scrape(self, profile: ProfileData) -> Dict[str, str]:
        """Scrape the given media item"""
        profile, stream_count = self.api_scrape(profile)
        if profile:
            logger.log("SCRAPER", f"Found {len(profile)} streams out of {stream_count} for {profile.parent.log_string}")
        else:
            logger.log("NOT_FOUND", f"No streams found for {profile.parent.log_string}")
        return profile

    def api_scrape(self, profile: ProfileData) -> tuple[Dict[str, str], int]:
        """Wrapper for `Prowlarr` scrape method"""
        results_queue = queue.Queue()
        threads = [
            threading.Thread(target=self._thread_target, args=(profile, indexer, results_queue))
            for indexer in self.indexers
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        results = self._collect_results(results_queue)
        return self._process_results(results)

    def _thread_target(self, profile: ProfileData, indexer: ProwlarrIndexer, results_queue: queue.Queue):
        try:
            start_time = time.perf_counter()
            result = self._search_indexer(profile, indexer)
            search_duration = time.perf_counter() - start_time
        except TypeError as e:
            logger.error(f"Invalid Type for {profile.parent.log_string}: {e}")
            result = []
            search_duration = 0
        item_title = profile.parent.log_string # probably not needed, but since its concurrent, it's better to be safe
        logger.debug(f"Scraped {item_title} from {indexer.title} in {search_duration:.2f} seconds with {len(result)} results")
        results_queue.put(result)

    def _search_indexer(self, profile: ProfileData, indexer: ProwlarrIndexer) -> List[Tuple[str, str]]:
        """Search for the given item on the given indexer"""
        if profile.parent.type == "movie":
            return self._search_movie_indexer(profile, indexer)
        elif profile.parent.type in ["show", "season", "episode"]:
            return self._search_series_indexer(profile, indexer)
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

    def _search_movie_indexer(self, profile: ProfileData, indexer: ProwlarrIndexer) -> List[Tuple[str, str]]:
        """Search for movies on the given indexer"""
        if indexer.movie_search_capabilities is None:
            return []
        params = {
            "apikey": self.api_key,
            "t": "movie",
            "cat": "2000",
            "q": profile.parent.title,
        }
        if indexer.movie_search_capabilities and "year" in indexer.movie_search_capabilities:
            if hasattr(profile.parent.aired_at, "year") and profile.parent.aired_at.year: params["year"] = profile.parent.aired_at.year
        if indexer.movie_search_capabilities and "imdbid" in indexer.movie_search_capabilities:
            params["imdbid"] = profile.parent.ids["imdb_id"]
        url = f"{self.settings.url}/api/v1/indexer/{indexer.id}/newznab"
        return self._fetch_results(url, params, indexer.title, "movie")

    def _search_series_indexer(self, profile: ProfileData, indexer: ProwlarrIndexer) -> List[Tuple[str, str]]:
        """Search for series on the given indexer"""
        if indexer.tv_search_capabilities is None:
            return []
        q, season, ep = self._get_series_search_params(profile)

        if not q:
            logger.debug(f"No search query found for {profile.log_string}")
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
            params["imdbid"] = profile.parent.get_top_imdb_id()

        url = f"{self.settings.url}/api/v1/indexer/{indexer.id}/newznab"
        return self._fetch_results(url, params, indexer.title, "series")

    def _get_series_search_params(self, profile: ProfileData) -> Tuple[str, int, Optional[int]]:
        """Get search parameters for series"""
        title = profile.parent.get_top_title()
        if profile.parent.type == "show":
            return title, None, None
        elif profile.parent.type == "season":
            return title, profile.parent.number, None
        elif profile.parent.type == "episode":
            return title, profile.parent.parent.number, profile.parent.number
        return "", 0, None

    def _get_indexers(self) -> List[ProwlarrIndexer]:
        """Get the indexers from Prowlarr"""
        url = f"{self.settings.url}/api/v1/indexer?apikey={self.api_key}"
        try:
            response = requests.get(url)
            response.raise_for_status()
            return self._get_indexer_from_json(response.text)
        except Exception as e:
            logger.error(f"Exception while getting indexers from Prowlarr: {e}")
            return []

    def _get_indexer_from_json(self, json_content: str) -> list[ProwlarrIndexer]:
        """Parse the indexers from the XML content"""
        indexer_list = []
        for indexer in json.loads(json_content):
            indexer_list.append(ProwlarrIndexer(title=indexer["name"], id=str(indexer["id"]), link=indexer["infoLink"], type=indexer["protocol"], language=indexer["language"], movie_search_capabilities=(s[0] for s in indexer["capabilities"]["movieSearchParams"]) if  len([s for s in indexer["capabilities"]["categories"] if s["name"] == "Movies"]) > 0 else None, tv_search_capabilities=(s[0] for s in indexer["capabilities"]["tvSearchParams"]) if  len([s for s in indexer["capabilities"]["categories"] if s["name"] == "TV"]) > 0 else None))
            
        return indexer_list

    def _fetch_results(self, url: str, params: Dict[str, str], indexer_title: str, search_type: str) -> List[Tuple[str, str]]:
        """Fetch results from the given indexer"""
        try:
            if self.second_limiter:
                with self.second_limiter:
                    response = requests.get(url, params=params, timeout=self.timeout)
            else:
                response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return self._parse_xml(response.text, indexer_title)
        except (HTTPError, ConnectionError, Timeout):
            logger.debug(f"Indexer failed to fetch results for {search_type.title()} with indexer {indexer_title}")
        except Exception as e:
            if "Prowlarr.Common.IndexerException" in str(e):
                logger.error(f"Indexer exception while fetching results from {indexer_title} ({search_type}): {e}")
            else:
                logger.error(f"Exception while fetching results from {indexer_title} ({search_type}): {e}")
        return []

    def _parse_xml(self, xml_content: str, indexer_title: str) -> list[tuple[str, str]]:
        """Parse the torrents from the XML content"""
        xml_root = ET.fromstring(xml_content)
        result_list = []
        infohashes_found = False
        data = xml_root.findall(".//item")
        for item in data:
            infoHash = item.find(
                ".//torznab:attr[@name='infohash']",
                namespaces={"torznab": "http://torznab.com/schemas/2015/feed"}
            )
            if infoHash is None or len(infoHash.attrib["value"]) != 40:
                continue
            infohashes_found = True
            result_list.append((item.find(".//title").text, infoHash.attrib["value"]))
        len_data = len(data)
        if infohashes_found is False and len_data > 0:
            logger.warning(f"{self.key} Tracker {indexer_title} may never return infohashes, consider disabling: {len_data} items found, None contain infohash.")
        return result_list

    def _log_indexers(self) -> None:
        """Log the indexers information"""
        for indexer in self.indexers:
            if not indexer.movie_search_capabilities:
                logger.debug(f"Movie search not available for {indexer.title}")
            if not indexer.tv_search_capabilities:
                logger.debug(f"TV search not available for {indexer.title}")