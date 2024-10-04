import threading
from datetime import datetime
from typing import Dict, Generator, List

from program.media.item import ProfileData
from program.media.stream import Stream
from program.scrapers.annatar import Annatar
from program.scrapers.comet import Comet
from program.scrapers.jackett import Jackett
from program.scrapers.knightcrawler import Knightcrawler
from program.scrapers.mediafusion import Mediafusion
from program.scrapers.orionoid import Orionoid
from program.scrapers.prowlarr import Prowlarr
from program.scrapers.shared import _parse_results
from program.scrapers.torbox import TorBoxScraper
from program.scrapers.torrentio import Torrentio
from program.scrapers.zilean import Zilean
from program.settings.manager import settings_manager
from utils.logger import logger


class Scraping:
    def __init__(self):
        self.key = "scraping"
        self.initialized = False
        self.settings = settings_manager.settings.scraping
        self.services = {
            Annatar: Annatar(),
            Torrentio: Torrentio(),
            Knightcrawler: Knightcrawler(),
            Orionoid: Orionoid(),
            Jackett: Jackett(),
            TorBoxScraper: TorBoxScraper(),
            Mediafusion: Mediafusion(),
            Prowlarr: Prowlarr(),
            Zilean: Zilean(),
            Comet: Comet()
        }
        self.initialized = self.validate()
        if not self.initialized:
            return

    def validate(self):
        return any(service.initialized for service in self.services.values())

    def run(self, profile: ProfileData) -> Generator[ProfileData, None, None]:
        """Scrape an item."""
        sorted_streams = self.scrape(profile)
        for stream in sorted_streams.values():
            if stream not in profile.streams:
                profile.streams.append(stream)
        profile.scraped_at = datetime.now()
        profile.scraped_times= profile.scraped_times + 1

        if not profile.streams:
            logger.log("NOT_FOUND", f"Scraping returned no good results for {profile.log_string}")

        yield profile

    def scrape(self, profile: ProfileData, log = True) -> Dict[str, Stream]:
        """Scrape an item."""
        threads: List[threading.Thread] = []
        results: Dict[str, str] = {}
        total_results = 0
        results_lock = threading.RLock()

        def run_service(service, profile):
            nonlocal total_results
            service_results = service.run(profile)

            if not isinstance(service_results, dict):
                logger.error(f"Service {service.__class__.__name__} returned invalid results: {service_results}")
                return

            with results_lock:
                results.update(service_results)
                total_results += len(service_results)

        for service_name, service in self.services.profiles():
            if service.initialized:
                thread = threading.Thread(target=run_service, args=(service, profile), name=service_name.__name__)
                threads.append(thread)
                thread.start()

        for thread in threads:
            thread.join()

        if total_results != len(results):
            logger.debug(f"Scraped {profile.log_string} with {total_results} results, removed {total_results - len(results)} duplicate hashes")

        sorted_streams: Dict[str, Stream] = _parse_results(profile, results, log)

        if sorted_streams and (log and settings_manager.settings.debug):
            item_type = profile.parent.type.title()
            top_results = list(sorted_streams.values())[:10]
            for sorted_tor in top_results:
                item_info = f"[{item_type}]"
                if profile.parent.type == "season":
                    item_info = f"[{item_type} {profile.parent.number}]"
                elif profile.parent.type == "episode":
                    item_info = f"[{item_type} {profile.parent.parent.number}:{profile.parent.number}]"
                logger.debug(f"{item_info} Parsed '{sorted_tor.parsed_title}' with rank {sorted_tor.rank} ({sorted_tor.infohash}): '{sorted_tor.raw_title}'")

        return sorted_streams

    @classmethod
    def can_we_scrape(cls, profile: ProfileData) -> bool:
        """Check if we can scrape an profile."""
        if not profile.is_released:
            logger.debug(f"Cannot scrape {profile.parent.log_string}: item is not released")
            return False
        if not cls.should_submit(profile):
            logger.debug(f"Cannot scrape {profile.parent.log_string}: profile has been scraped recently, backing off")
            return False
        return True

    @staticmethod
    def should_submit(profile: ProfileData) -> bool:
        """Check if an item should be submitted for scraping."""
        settings = settings_manager.settings.scraping
        scrape_time = 5 * 60  # 5 minutes by default

        if profile.scraped_times >= 2 and profile.scraped_times <= 5:
            scrape_time = settings.after_2 * 60 * 60
        elif profile.scraped_times > 5 and profile.scraped_times <= 10:
            scrape_time = settings.after_5 * 60 * 60
        elif profile.scraped_times > 10:
            scrape_time = settings.after_10 * 60 * 60

        return (
            not profile.scraped_at
            or (datetime.now() - profile.scraped_at).total_seconds() > scrape_time
        )