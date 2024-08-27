from program.content import Listrr, Mdblist, Overseerr, PlexWatchlist
from program.content.trakt import TraktContent
from program.downloaders import Downloader
from program.indexers.trakt import TraktIndexer
from program.libraries import SymlinkLibrary
from program.media import Episode, MediaItem, Movie, Season, Show, States
from program.post_processing import PostProcessing, notify
from program.post_processing.subliminal import Subliminal
from program.scrapers import Scraping
from program.symlink import Symlinker
from program.types import ProcessedEvent, Service
from program.updaters import Updater
from utils.logger import logger
from program.settings.manager import settings_manager


def process_event(existing_item: MediaItem | None, emitted_by: Service, item: MediaItem) -> ProcessedEvent:
    """Process an event and return the updated item, next service and items to submit."""
    next_service: Service = None
    updated_item = item
    no_further_processing: ProcessedEvent = (None, None, [])
    items_to_submit = []

    source_services = (Overseerr, PlexWatchlist, Listrr, Mdblist, SymlinkLibrary, TraktContent)
    if emitted_by in source_services or item.state in [States.Requested]:
        next_service = TraktIndexer
        if isinstance(item, Season):
            item = item.parent
            existing_item = existing_item.parent if existing_item else None
        if existing_item and not TraktIndexer.should_submit(existing_item):
            return no_further_processing
        return None, next_service, [item]

    elif item.state in [States.Unknown, States.PartiallyCompleted]:
        if item.type == "show":
            for season in item.seasons:
                if season.state != States.Completed:
                    _, _, sub_items = process_event(season, emitted_by, season)
                    items_to_submit += sub_items
        elif item.type == "season":
            for episode in item.episodes:
                if episode.state != States.Completed:
                    _, _, sub_items = process_event(episode, emitted_by, episode)
                    items_to_submit += sub_items

    elif item.state == States.Indexed:
        next_service = Scraping
        if existing_item:
            if not existing_item.indexed_at:
                if isinstance(item, (Show, Season)):
                    existing_item.fill_in_missing_children(item)
                existing_item.copy_other_media_attr(item)
                existing_item.indexed_at = item.indexed_at
                updated_item = item = existing_item
            if existing_item.state == States.Completed:
                return existing_item, None, []
            elif not emitted_by == Scraping and Scraping.can_we_scrape(existing_item):
                items_to_submit = [existing_item]
            elif item.type == "show":
                items_to_submit = [s for s in item.seasons if s.state != States.Completed and Scraping.can_we_scrape(s)]
            elif item.type == "season":
                items_to_submit = [e for e in item.episodes if e.state != States.Completed and Scraping.can_we_scrape(e)]

    elif item.state == States.Scraped:
        next_service = Downloader
        items_to_submit = [item]

    elif item.state == States.Downloaded:
        next_service = Symlinker
        items_to_submit = [item]

    elif item.state == States.Symlinked:
        next_service = Updater
        items_to_submit = [item]

    elif item.state == States.Completed:
        notify(item)
        # Avoid multiple post-processing runs
        if not emitted_by == PostProcessing:
            if settings_manager.settings.post_processing.subliminal.enabled:
                next_service = PostProcessing
                if item.type in ["movie", "episode"] and Subliminal.should_submit(item):
                    items_to_submit = [item]
                elif item.type == "show":
                    items_to_submit = [e for s in item.seasons for e in s.episodes if e.state == States.Completed and Subliminal.should_submit(e)]
                elif item.type == "season":
                    items_to_submit = [e for e in item.episodes if e.state == States.Completed and Subliminal.should_submit(e)]
                if not items_to_submit:
                    return no_further_processing
        else:
            return no_further_processing

    return updated_item, next_service, items_to_submit