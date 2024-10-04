from typing import Tuple
from program.content import Listrr, Mdblist, Overseerr, PlexWatchlist
from program.content.trakt import TraktContent
from program.db.db_functions import _imdb_exists_in_db
from program.downloaders import Downloader
from program.indexers.trakt import TraktIndexer
from program.libraries import SymlinkLibrary
from program.media import Episode, MediaItem, Movie, Season, Show, States
from program.media.item import ProfileData
from program.post_processing import PostProcessing, notify
from program.post_processing.subliminal import Subliminal
from program.scrapers import Scraping
from program.settings.manager import settings_manager
from program.symlink import Symlinker
from program.types import ProcessedEvent, Service
from program.updaters import Updater
from utils.logger import logger


def process_event(existing_item: MediaItem | None, emitted_by: Service, item: MediaItem | ProfileData) -> ProcessedEvent:
    """Process an event and return the updated item, next service and items to submit."""
    next_service: Service = None
    updated_item = item
    no_further_processing: ProcessedEvent = (None, None, ())
    items_to_submit = []

    if isinstance(item, MediaItem):
        if emitted_by in (Overseerr, PlexWatchlist, Listrr, Mdblist, SymlinkLibrary, TraktContent, "ApiAdd"):
            if _imdb_exists_in_db(item.ids["imdb_id"]) and item.last_state == States.Completed:
                logger.debug(f"Item {item.log_string} already exists in the database.")
                return no_further_processing
            if isinstance(item, Season):
                item = item.parent
                existing_item = existing_item.parent if existing_item else None
            if existing_item and not TraktIndexer.should_submit(existing_item):
                return no_further_processing
            return None, next_service, [(item, TraktIndexer)]

        if existing_item and not existing_item.indexed_at:
            if item.type in ("show", "season"):
                existing_item.fill_in_missing_children(item)
            existing_item.copy_other_media_attr(item)
            existing_item.indexed_at = item.indexed_at
            updated_item = item = existing_item
        if item.last_state == States.Completed:
            return item, None, []
        else:
            for profile in item.profiles:
                if profile.last_state != States.Completed:
                    _, sub_items = process_event(None, emitted_by, profile)
                    items_to_submit += sub_items

    elif isinstance(item, ProfileData):
        profile = item
        if profile.last_state == States.Requested:
            if Scraping.should_submit(profile):
                items_to_submit = [(profile, Scraping)]
            else:
                if item.parent.type == "show":
                    _,  sub_items = process_event(item.parent, emitted_by, item.parent)
                    items_to_submit += sub_items
                elif item.parent.type == "season":
                    _,  sub_items = process_event(item.parent, emitted_by, item.parent)
                    items_to_submit += sub_items

    # elif item.last_state == States.Scraped:
    #     next_service = Downloader
    #     items_to_submit = [item]

    # elif item.last_state == States.Downloaded:
    #     next_service = Symlinker
    #     items_to_submit = [item]

    # elif item.last_state == States.Symlinked:
    #     next_service = Updater
    #     items_to_submit = [item]

    # elif item.last_state == States.Completed:
    #     # If a user manually retries an item, lets not notify them again
    #     if emitted_by not in ["Manual", PostProcessing]:
    #         notify(item)
    #     # Avoid multiple post-processing runs
    #     if not emitted_by == PostProcessing:
    #         if settings_manager.settings.post_processing.subliminal.enabled:
    #             next_service = PostProcessing
    #             if item.type in ["movie", "episode"] and Subliminal.should_submit(item):
    #                 items_to_submit = [item]
    #             elif item.type == "show":
    #                 items_to_submit = [e for s in item.seasons for e in s.episodes if e.last_state == States.Completed and Subliminal.should_submit(e)]
    #             elif item.type == "season":
    #                 items_to_submit = [e for e in item.episodes if e.last_state == States.Completed and Subliminal.should_submit(e)]
    #             if not items_to_submit:
    #                 return no_further_processing
    #     else:
    #         return no_further_processing

    return updated_item, items_to_submit


        # elif item.last_state in [States.PartiallyCompleted, States.Ongoing]:
        #     if item.type == "show":
        #         for season in item.seasons:
        #             if season.last_state not in [States.Completed, States.Unreleased]:
        #                 _, _, sub_items = process_event(season, emitted_by, season)
        #                 items_to_submit += sub_items
        #     elif item.type == "season":
        #         for episode in item.episodes:
        #             if episode.last_state != States.Completed:
        #                 _, _, sub_items = process_event(episode, emitted_by, episode)
        #                 items_to_submit += sub_items