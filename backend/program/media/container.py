import os
import shutil
import tempfile
import threading
from copy import deepcopy
from pickle import UnpicklingError
from typing import Dict, Generator, List, Optional

import dill
from program.media.item import Episode, ItemId, MediaItem, Movie, Season, Show
from program.media.state import States
from utils.logger import logger


class ReadWriteLock:
    def __init__(self):
        self._read_ready = threading.Condition(threading.Lock())
        self._readers = 0

    def acquire_read(self):
        with self._read_ready:
            self._readers += 1

    def release_read(self):
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self):
        self._read_ready.acquire()
        while self._readers > 0:
            self._read_ready.wait()

    def release_write(self):
        self._read_ready.release()

    def __enter__(self):
        self.acquire_write()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release_write()


class MediaItemContainer:
    """A container to store media items."""

    def __init__(self):
        self._items: Dict[ItemId, MediaItem] = {}
        self._shows: Dict[ItemId, Show] = {}
        self._seasons: Dict[ItemId, Season] = {}
        self._episodes: Dict[ItemId, Episode] = {}
        self._movies: Dict[ItemId, Movie] = {}
        self.library_file: str = "media.pkl"
        self.lock: ReadWriteLock = ReadWriteLock()

    def __iter__(self) -> Generator[MediaItem, None, None]:
        self.lock.acquire_read()
        try:
            for item in self._items.values():
                yield item
        finally:
            self.lock.release_read()

    def __contains__(self, item) -> bool:
        self.lock.acquire_read()
        try:
            return item in self._items
        finally:
            self.lock.release_read()

    def __len__(self) -> int:
        self.lock.acquire_read()
        try:
            return len(self._items)
        finally:
            self.lock.release_read()

    def __getitem__(self, item_id: ItemId) -> MediaItem:
        self.lock.acquire_read()
        try:
            return self._items[item_id]
        finally:
            self.lock.release_read()

    def get(self, key, default=None) -> MediaItem:
        self.lock.acquire_read()
        try:
            return self._items.get(key, default)
        finally:
            self.lock.release_read()

    @property
    def seasons(self) -> dict[ItemId, Season]:
        return deepcopy(self._seasons)

    @property
    def episodes(self) -> dict[ItemId, Episode]:
        return deepcopy(self._episodes)

    @property
    def shows(self) -> dict[ItemId, Show]:
        return deepcopy(self._shows)

    @property
    def movies(self) -> dict[ItemId, Movie]:
        return deepcopy(self._movies)

    def get_items_with_state(self, state) -> dict[ItemId, MediaItem]:
        """Get items with the specified state"""
        return {
            item_id: self[item_id]
            for item_id, item in self._items.items()
            if item.state == state
        }

    def get_incomplete_items(self) -> dict[ItemId, MediaItem]:
        """Get items that are not completed."""
        self.lock.acquire_read()
        try:
            return {
                item_id: item
                for item_id, item in self._items.items()
                if item.state not in (States.Completed, States.PartiallyCompleted)
            }
        finally:
            self.lock.release_read()

    def get_item(self, identifier: str) -> Optional[MediaItem]:
        """Retrieve an item by its IMDb ID or item ID from the container."""
        self.lock.acquire_read()
        try:
            if identifier.startswith("tt"):
                return self._imdb_index.get(identifier)
            item_id = ItemId(identifier)
            return self._items.get(item_id)
        finally:
            self.lock.release_read()

    def get_episodes(self, show_id: ItemId) -> List[MediaItem]:
        """Get all episodes for a show."""
        self.lock.acquire_read()
        try:
            return self.shows[show_id].episodes
        finally:
            self.lock.release_read()

    def upsert(self, item: MediaItem) -> None:
        """Iterate through the input item and upsert all parents and children."""
        # Use deepcopy so that further modifications made to the input item
        # will not affect the container state
        self._items[item.item_id] = item
        detatched = item.item_id.parent_id is None or item.parent is None
        if isinstance(item, (Season, Episode)) and detatched:
            logger.error(
                "%s item %s is detatched and not associated with a parent, and thus" +
                " it cannot be upserted into the database",
                item.__class__.name, item.log_string
            )
            raise ValueError("Item detached from parent")
        if isinstance(item, Show):
            self._shows[item.item_id] = item
            for season in item.seasons:
                season.parent = item
                self._items[season.item_id] = season
                self._seasons[season.item_id] = season
                for episode in season.episodes:
                    episode.parent = season
                    self._items[episode.item_id] = episode
                    self._episodes[episode.item_id] = episode
        if isinstance(item, Season):
            self._seasons[item.item_id] = item
            # update children
            for episode in item.episodes:
                episode.parent = item
                self._items[episode.item_id] = episode
                self._episodes[episode.item_id] = episode
            # Ensure the parent Show is updated in the container
            container_show: Show = self._items[item.item_id.parent_id]
            parent_index = container_show.get_season_index_by_id(item.item_id)
            if parent_index is not None:
                container_show.seasons[parent_index] = item
        elif isinstance(item, Episode):
            self._episodes[item.item_id] = item
            # Ensure the parent Season is updated in the container
            container_season: Season = self._items[item.item_id.parent_id]
            parent_index = container_season.get_episode_index_by_id(item.item_id)
            if parent_index is not None:
                container_season.episodes[parent_index] = item
        elif isinstance(item, Movie):
            self._movies[item.item_id] = item

    def _index_item(self, item: MediaItem):
        """Index the item and its children in the appropriate dictionaries."""
        self._items[item.item_id] = item
        if isinstance(item, Show):
            for season in item.seasons:
                season.parent = item
                season.item_id.parent_id = item.item_id
                self._index_item(season)
        elif isinstance(item, Season):
            for episode in item.episodes:
                episode.parent = item
                episode.item_id.parent_id = item.item_id
                self._index_item(episode)

    def remove(self, item: MediaItem) -> None:
        """Remove an item, which could be a movie, show, season, or episode."""
        if not item:
            logger.error("Attempted to remove a None item.")
            return

        log_title = item.log_string
        imdb_id = item.imdb_id

        self.lock.acquire_write()
        try:
            def remove_children(item):
                if isinstance(item, Show):
                    for season in item.seasons:
                        remove_children(season)
                elif isinstance(item, Season):
                    for episode in item.episodes:
                        self._remove_item(episode)
                self._remove_item(item)

            remove_children(item)
            logger.debug(f"Removed item: {log_title} (IMDb ID: {imdb_id})")
        except KeyError as e:
            logger.error(f"Failed to remove item: {log_title} (IMDb ID: {imdb_id}). KeyError: {e}")
        except Exception as e:
            logger.error(f"Unexpected error occurred while removing item: {log_title} (IMDb ID: {imdb_id}). Exception: {e}")
        finally:
            self.lock.release_write()

    def _remove_item(self, item: MediaItem) -> None:
        """Helper method to remove an item from the container."""
        item_id: ItemId = item.item_id
        if item_id in self._items:
            del self._items[item_id]
            logger.debug(f"Successfully removed item with ID: {item_id}")
        else:
            logger.error(f"Item ID {item_id} not found in _items.")

    def count(self, state) -> int:
        """Count items with given state in container"""
        return len(self.get_items_with_state(state))

    def save(self, filename: str = "media.pkl") -> None:
        """Save the container to a file."""
        with self.lock, tempfile.NamedTemporaryFile(delete=False, mode="wb") as temp_file:
            try:
                dill.dump(self, temp_file, dill.HIGHEST_PROTOCOL)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            except Exception as e:
                logger.error(f"Failed to serialize data: {e}")
                return

            try:
                backup_filename = filename + ".bak"
                if os.path.exists(filename):
                    shutil.copyfile(filename, backup_filename)
                shutil.move(temp_file.name, filename)
            except Exception as e:
                logger.error(f"Failed to replace old file with new file: {e}")
                try:
                    os.remove(temp_file.name)
                except OSError as remove_error:
                    logger.error(f"Failed to remove temporary file: {remove_error}")

    def load(self, filename: str = "media.pkl") -> None:
        """Load the container from a file."""
        try:
            with open(filename, "rb") as file:
                from_disk = dill.load(file)
                self._items = from_disk._items
                self._movies = from_disk._movies
                self._shows = from_disk._shows
                self._seasons = from_disk._seasons
                self._episodes = from_disk._episodes
        except FileNotFoundError:
            pass
        except (EOFError, UnpicklingError):
            logger.error(f"Failed to unpickle media data at {filename}, wiping cached data")
            os.remove(filename)
            self._items = {}
            self._movies = {}
            self._shows = {}
            self._seasons = {}
            self._episodes = {}

        if self._items:
            logger.success(f"Loaded {len(self._items)} items from {filename}")
