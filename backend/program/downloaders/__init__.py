from .realdebrid import RealDebridDownloader
from .alldebrid import AllDebridDownloader
from .torbox import TorBoxDownloader
from program.media.item import MediaItem
from utils.logger import logger


class Downloader:
    def __init__(self, hash_cache):
        self.key = "downloader"
        self.initialized = False
        self.services = {
            RealDebridDownloader: RealDebridDownloader(hash_cache),
            TorBoxDownloader: TorBoxDownloader(hash_cache),
            AllDebridDownloader: AllDebridDownloader(hash_cache),
        }
        self.initialized = self.validate()

    def validate(self):
        initialized_services = [service for service in self.services.values() if service.initialized]
        if len(initialized_services) > 1:
            logger.error("More than one downloader service is initialized. Only one downloader can be initialized at a time.")
            return False
        return len(initialized_services) == 1

    def run(self, item: MediaItem):
        for service in self.services.values():
            if service.initialized:
                return service.run(item)