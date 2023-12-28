"""Plex library module"""
import concurrent.futures
import os
import threading
import time
import uuid
import requests
from typing import List, Optional
from plexapi import exceptions
from plexapi.server import PlexServer
from requests.exceptions import ConnectionError
from pydantic import BaseModel, HttpUrl
from utils.logger import logger
from utils.settings import settings_manager as settings
from program.updaters.trakt import get_imdbid_from_tvdb
from program.media.container import MediaItemContainer
from program.media.state import Symlink, Library
from utils.request import get, post
from program.media.item import (
    MediaItem,
    Movie,
    Show,
    Season,
    Episode,
)


class PlexConfig(BaseModel):
    user: Optional[str] = None
    token: Optional[str] = None
    url: Optional[HttpUrl] = None
    watchlist_url: Optional[str] = None


class Plex(threading.Thread):
    """Plex library class"""

    def __init__(self, media_items: MediaItemContainer):
        super().__init__(name="Plex")
        # Plex class library is a necessity
        while True:
            try:
                temp_settings = settings.get("plex")
                self.library_path = os.path.abspath(
                    os.path.join(settings.get("container_mount"), os.pardir, "library")
                )
                self.plex = PlexServer(
                    temp_settings["url"], temp_settings["token"], timeout=60
                )
                self.running = False
                self.log_worker_count = False
                self.media_items = media_items
                self._update_items()
                break
            except exceptions.Unauthorized:
                logger.error("Wrong plex token, retrying in 2...")
            except ConnectionError:
                logger.error("Couldnt connect to plex, retrying in 2...")
            except TimeoutError as e:
                logger.warn(
                    "Plex timed out: retrying in 2 seconds... %s", str(e), exc_info=True
                )
            except Exception as e:
                logger.error("Unknown error: %s",str(e) , exc_info=True)
            time.sleep(2)

    def run(self):
        while self.running:
            self._update_sections()
            self._update_items()
            time.sleep(1)

    def start(self):
        self.running = True
        super().start()

    def stop(self):
        self.running = False
        super().join()

    def _update_items(self):
        items = []
        sections = self.plex.library.sections()
        processed_sections = set()
        max_workers = os.cpu_count() + 1
        for section in sections:
            if section.key in processed_sections and not self._is_wanted_section(
                section
            ):
                continue

            try:
                if not section.refreshing:
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=max_workers, thread_name_prefix="Plex"
                    ) as executor:
                        future_items = {
                            executor.submit(self._create_item, item)
                            for item in section.all()
                        }
                        for future in concurrent.futures.as_completed(future_items):
                            media_item = future.result()
                            if media_item:
                                items.append(media_item)
            except requests.exceptions.ReadTimeout as e:
                logger.error(
                    "Timeout occurred when accessing section %s with Reason: %s",
                    section.title, str(e)
                )
                continue
            except requests.exceptions.ConnectionError as e:
                logger.error(
                    "Connection aborted. Remote end closed connection with response: %s for item %s",
                    str(e), section.title
                )
                continue

            processed_sections.add(section.key)
        matched_items = self.match_items(items)

        if matched_items > 0:
            logger.info(f"Found {matched_items} new items")

    def _update_sections(self):
        """Update plex library section"""
        for section in self.plex.library.sections():
            for item in self.media_items:
                log_string = None
                if section.type == item.type:
                    if item.type == "movie":
                        if (
                            item.state == Symlink
                            and item.get("update_folder") != "updated"
                        ):
                            section.update(item.update_folder)
                            item.set("update_folder", "updated")
                            log_string = item.title
                            break
                    if item.type == "show":
                        for season in item.seasons:
                            if (
                                season.state == Symlink
                                and season.get("update_folder") != "updated"
                            ):
                                section.update(season.episodes[0].update_folder)
                                season.set("update_folder", "updated")
                                log_string = f"{item.title} season {season.number}"
                                break
                            else:
                                for episode in season.episodes:
                                    if (
                                        episode.state == Symlink
                                        and episode.get("update_folder") != "updated"
                                        and episode.parent.get("update_folder")
                                        != "updated"
                                    ):
                                        section.update(episode.update_folder)
                                        episode.set("update_folder", "updated")
                                        log_string = f"{item.title} season {season.number} episode {episode.number}"
                                        break
            if log_string:
                logger.debug("Updated section %s for %s", section.title, log_string)

    def _create_item(self, item):
        new_item = _map_item_from_data(item)
        if new_item and item.type == "show":
            for season in item.seasons():
                if season.seasonNumber != 0:
                    new_season = _map_item_from_data(season)
                    if new_season:
                        new_season_episodes = []
                        for episode in season.episodes():
                            new_episode = _map_item_from_data(episode)
                            if new_episode:
                                new_season_episodes.append(new_episode)
                        new_season.episodes = new_season_episodes
                        new_item.seasons.append(new_season)
        return new_item

    def match_items(self, found_items: List[MediaItem]):
        """Matches items in given mediacontainer that are not in library
        to items that are in library"""
        items_to_update = 0

        for item in self.media_items:
            if type(item.state) != Library:
                for found_item in found_items:
                    if found_item.imdb_id == item.imdb_id:
                        items_to_update += self._update_item(item, found_item)
                        break
            # Leaving this here as a reminder to not forget about deleting items that are removed from plex, needs to be revisited
            # if item.state is MediaItemState.LIBRARY and item not in found_items:
            #     self.media_items.remove(item)
        return items_to_update

    def _update_item(self, item: MediaItem, library_item: MediaItem):
        """Internal method to use with match_items
        It does some magic to update media items according to library
        items found"""
        items_updated = 0
        item.set("guid", library_item.guid)
        item.set("key", library_item.key)
        if item.type == "show":
            for season in item.seasons:
                for episode in season.episodes:
                    if episode.state != Library:
                        for found_season in library_item.seasons:
                            if found_season.number == season.number:
                                for found_episode in found_season.episodes:
                                    if found_episode.number == episode.number:
                                        episode.set("guid", found_episode.guid)
                                        episode.set("key", found_episode.key)
                                        items_updated += 1
                                        break
                                break
        return items_updated

    def _is_wanted_section(self, section):
        return any(self.library_path in location for location in section.locations)
    
    def _oauth(self):
        random_uuid = uuid.uuid4()
        response = get(
            url="https://plex.tv/api/v2/user",
            additional_headers={
                "X-Plex-Product": "Iceberg",
                "X-Plex-Client-Identifier": random_uuid,
                "X-Plex-Token": settings.get("plex.token")
                },
            )
        if not response.ok:
            data = post(
                url="https://plex.tv/api/v2/pins",
                additional_headers={
                    "strong": "true",
                    "X-Plex-Product": "Iceberg",
                    "X-Plex-Client-Identifier": random_uuid,
                    },
            )
            if data.ok:
                pin = data.id


def _map_item_from_data(item):
    """Map Plex API data to MediaItemContainer."""
    file = None
    guid = getattr(item, "guid", None)
    if item.type in ["movie", "episode"]:
        file = getattr(item, "locations", [None])[0].split("/")[-1]
    genres = [genre.tag for genre in getattr(item, "genres", [])]
    title = getattr(item, "title", None)
    key = getattr(item, "key", None)
    season_number = getattr(item, "seasonNumber", None)
    episode_number = getattr(item, "episodeNumber", None)
    art_url = getattr(item, "artUrl", None)
    imdb_id = None
    tvdb_id = None
    aired_at = None

    if item.type in ["movie", "show"]:
        guids = getattr(item, "guids", [])
        imdb_id = next(
            (guid.id.split("://")[-1] for guid in guids if "imdb" in guid.id), None
        )
        aired_at = getattr(item, "originallyAvailableAt", None)

        # All movies have imdb, but not all shows do.
        # This is due to season 0 (specials) not having imdb ids.
        # Attempt to get the imdb id from the tvdb id if we don't have it.
        # Needs more testing..
        # if not imdb_id:
        #     logger.debug("Unable to find imdb, trying tvdb for %s", title)
        #     tvdb_id = next(
        #         (guid.id.split("://")[-1] for guid in guids if "tvdb" in guid.id), None
        #     )
        #     if tvdb_id:
        #         logger.debug("Unable to find imdb, but found tvdb: %s", tvdb_id)
        #         imdb_id = get_imdbid_from_tvdb(tvdb_id)
        #         if imdb_id:
        #             logger.debug("Found imdb from tvdb: %s", imdb_id)

    media_item_data = {
        "title": title,
        "imdb_id": imdb_id,
        "tvdb_id": tvdb_id,
        "aired_at": aired_at,
        "genres": genres,
        "key": key,
        "guid": guid,
        "art_url": art_url,
        "file": file,
    }

    # Instantiate the appropriate subclass based on 'item_type'
    if item.type == "movie":
        return Movie(media_item_data)
    elif item.type == "show":
        return Show(media_item_data)
    elif item.type == "season":
        media_item_data["number"] = season_number
        return Season(media_item_data)
    elif item.type == "episode":
        media_item_data["number"] = episode_number
        media_item_data["season_number"] = season_number
        return Episode(media_item_data)
    else:
        # Specials may end up here..
        logger.error("Unknown Item: %s with type %s", item.title, item.type)
        return None
