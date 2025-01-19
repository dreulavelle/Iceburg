from loguru import logger

from program.media import MediaItem, States
from program.services.downloaders import Downloader
from program.services.indexers.trakt import TraktIndexer
from program.services.post_processing import PostProcessing, notify
from program.services.post_processing.subliminal import Subliminal
from program.services.scrapers import Scraping
from program.services.updaters import Updater
from program.settings.manager import settings_manager
from program.symlink import Symlinker
from program.types import ProcessedEvent, Service


def process_event(emitted_by: Service, existing_item: MediaItem | None = None, content_item: MediaItem | None = None) -> ProcessedEvent:
    """Process an event and return the updated item, next service and items to submit."""
    next_service: Service = None
    no_further_processing: ProcessedEvent = (None, [])
    items_to_submit = []

    # Skip processing if item is paused
    if existing_item and existing_item.is_paused:
        logger.debug(f"Skipping {existing_item.log_string} - item is paused")
        return no_further_processing
     
    #not sure if i need to remove this
    return next_service, items_to_submit
