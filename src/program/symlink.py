import asyncio
import os
import random
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path, PurePath
from typing import List, Optional, Union

from sqlalchemy import select

from program.db.db import db
from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.media.state import States
from program.media.stream import Stream
from program.settings.manager import settings_manager
from utils.logger import logger


class Symlinker:
    """
    A class that represents a symlinker thread.

    Settings Attributes:
        rclone_path (str): The absolute path of the rclone mount root directory.
        library_path (str): The absolute path of the location we will create our symlinks that point to the rclone_path.
    """

    def __init__(self):
        self.key = "symlink"
        self.settings = settings_manager.settings.symlink
        self.rclone_path = self.settings.rclone_path
        self.initialized = self.validate()
        if not self.initialized:
            return
        logger.info(f"Rclone path symlinks are pointed to: {self.rclone_path}")
        logger.info(f"Symlinks will be placed in: {self.settings.library_path}")
        logger.success("Symlink initialized!")

    def validate(self):
        """Validate paths and create the initial folders."""
        library_path = self.settings.library_path
        if not self.rclone_path or not library_path:
            logger.error("rclone_path or library_path not provided.")
            return False
        if self.rclone_path == Path(".") or library_path == Path("."):
            logger.error("rclone_path or library_path is set to the current directory.")
            return False
        if not self.rclone_path.exists():
            logger.error(f"rclone_path does not exist: {self.rclone_path}")
            return False
        if not library_path.exists():
            logger.error(f"library_path does not exist: {library_path}")
            return False
        if not self.rclone_path.is_absolute():
            logger.error(f"rclone_path is not an absolute path: {self.rclone_path}")
            return False
        if not library_path.is_absolute():
            logger.error(f"library_path is not an absolute path: {library_path}")
            return False
        return self._create_initial_folders()

    def _create_initial_folders(self):
        """Create the initial library folders."""
        try:
            self.library_path_movies = self.settings.library_path / "movies"
            self.library_path_shows = self.settings.library_path / "shows"
            self.library_path_anime_movies = self.settings.library_path / "anime_movies"
            self.library_path_anime_shows = self.settings.library_path / "anime_shows"
            folders = [
                self.library_path_movies,
                self.library_path_shows,
                self.library_path_anime_movies,
                self.library_path_anime_shows,
            ]
            for folder in folders:
                if not folder.exists():
                    folder.mkdir(parents=True, exist_ok=True)
        except FileNotFoundError as e:
            logger.error(f"Path not found when creating directory: {e}")
            return False
        except PermissionError as e:
            logger.error(f"Permission denied when creating directory: {e}")
            return False
        except OSError as e:
            logger.error(f"OS error when creating directory: {e}")
            return False
        return True

    def run(self, item: Union[Movie, Show, Season, Episode]):
        """Check if the media item exists and create a symlink if it does"""
        items = self._get_items_to_update(item)
        if not self._should_submit(items):
            if item.symlinked_times == 5:
                logger.debug(f"Soft resetting {item.log_string} because required files were not found")
                item.reset(True)
                yield item
            next_attempt = self._calculate_next_attempt(item)
            logger.debug(f"Waiting for {item.log_string} to become available, next attempt in {round((next_attempt - datetime.now()).total_seconds())} seconds")
            item.symlinked_times += 1
            yield (item, next_attempt)
        try:
            for _item in items:
                self._symlink(_item)
            logger.log("SYMLINKER", f"Symlinks created for {item.log_string}")
        except Exception as e:
            logger.error(f"Exception thrown when creating symlink for {item.log_string}: {e}")
        yield item

    def _calculate_next_attempt(self, item: Union[Movie, Show, Season, Episode]) -> datetime:
        base_delay = timedelta(seconds=5)
        next_attempt_delay = base_delay * (2 ** item.symlinked_times)
        next_attempt_time = datetime.now() + next_attempt_delay
        return next_attempt_time

    def _should_submit(self, items: Union[Movie, Show, Season, Episode]) -> bool:
        """Check if the item should be submitted for symlink creation."""
        random_item = random.choice(items)
        if not _get_item_path(random_item):
            return False
        else:
            return True

    def _get_items_to_update(self, item: Union[Movie, Show, Season, Episode]) -> List[Union[Movie, Episode]]:
        items = []
        if item.type in ["episode", "movie"]:
            items.append(item)
            item.set("folder", item.folder)
        elif item.type == "show":
            for season in item.seasons:
                for episode in season.episodes:
                    if episode.state == States.Downloaded:
                        items.append(episode)
        elif item.type == "season":
            for episode in item.episodes:
                if episode.state == States.Downloaded:
                    items.append(episode)
        return items

    def symlink(self, item: Union[Movie, Episode]) -> bool:
        """Create a symlink for the given media item if it does not already exist."""
        return self._symlink(item)

    def _symlink(self, item: Union[Movie, Episode]) -> bool:
        """Create a symlink for the given media item if it does not already exist."""
        if not item:
            logger.error(f"Invalid item sent to Symlinker: {item}")
            return False

        source = _get_item_path(item)
        if not source:
            logger.error(f"Could not find path for {item.log_string}, cannot create symlink.")
            return False

        filename = self._determine_file_name(item)
        if not filename:
            logger.error(f"Symlink filename is None for {item.log_string}, cannot create symlink.")
            return False

        extension = os.path.splitext(item.file)[1][1:]
        symlink_filename = f"{filename}.{extension}"
        destination = self._create_item_folders(item, symlink_filename)

        try:
            if os.path.islink(destination):
                os.remove(destination)
            os.symlink(source, destination)
        except PermissionError as e:
            # This still creates the symlinks, however they will have wrong perms. User needs to fix their permissions.
            # TODO: Maybe we validate symlink class by symlinking a test file, then try removing it and see if it still exists
            logger.exception(f"Permission denied when creating symlink for {item.log_string}: {e}")
        except OSError as e:
            if e.errno == 36:
                # This will cause a loop if it hits this.. users will need to fix their paths
                # TODO: Maybe create an alternative naming scheme to cover this?
                logger.error(f"Filename too long when creating symlink for {item.log_string}: {e}")
            else:
                logger.error(f"OS error when creating symlink for {item.log_string}: {e}")
            return False

        if Path(destination).readlink() != source:
            logger.error(f"Symlink validation failed: {destination} does not point to {source} for {item.log_string}")
            return False

        item.set("symlinked", True)
        item.set("symlinked_at", datetime.now())
        item.set("symlinked_times", item.symlinked_times + 1)
        item.set("symlink_path", destination)
        return True

    def _create_item_folders(self, item: Union[Movie, Show, Season, Episode], filename: str) -> str:
        """Create necessary folders and determine the destination path for symlinks."""
        is_anime: bool = hasattr(item, "is_anime") and item.is_anime

        movie_path: Path = self.library_path_movies
        show_path: Path = self.library_path_shows

        if self.settings.separate_anime_dirs and is_anime:
            if isinstance(item, Movie):
                movie_path = self.library_path_anime_movies
            elif isinstance(item, (Show, Season, Episode)):
                show_path = self.library_path_anime_shows

        def create_folder_path(base_path, *subfolders):
            path = os.path.join(base_path, *subfolders)
            os.makedirs(path, exist_ok=True)
            return path

        if isinstance(item, Movie):
            movie_folder = f"{item.title.replace('/', '-')} ({item.aired_at.year}) {{imdb-{item.ids['imdb_id']}}}"
            destination_folder = create_folder_path(movie_path, movie_folder)
            item.set("update_folder", destination_folder)
        elif isinstance(item, Show):
            folder_name_show = f"{item.title.replace('/', '-')} ({item.aired_at.year}) {{imdb-{item.ids['imdb_id']}}}"
            destination_folder = create_folder_path(show_path, folder_name_show)
            item.set("update_folder", destination_folder)
        elif isinstance(item, Season):
            show = item.parent
            folder_name_show = f"{show.title.replace('/', '-')} ({show.aired_at.year}) {{imdb-{show.ids['imdb_id']}}}"
            show_path = create_folder_path(show_path, folder_name_show)
            folder_season_name = f"Season {str(item.number).zfill(2)}"
            destination_folder = create_folder_path(show_path, folder_season_name)
            item.set("update_folder", destination_folder)
        elif isinstance(item, Episode):
            show = item.parent.parent
            folder_name_show = f"{show.title.replace('/', '-')} ({show.aired_at.year}) {{imdb-{show.ids['imdb_id']}}}"
            show_path = create_folder_path(show_path, folder_name_show)
            season = item.parent
            folder_season_name = f"Season {str(season.number).zfill(2)}"
            destination_folder = create_folder_path(show_path, folder_season_name)
            item.set("update_folder", destination_folder)

        return os.path.join(destination_folder, filename.replace("/", "-"))

    def _determine_file_name(self, item: Union[Movie, Episode]) -> str | None:
        """Determine the filename of the symlink."""
        filename = None
        if isinstance(item, Movie):
            filename = f"{item.title} ({item.aired_at.year}) " + "{imdb-" + item.ids['imdb_id'] + "}"
        elif isinstance(item, Season):
            showname = item.parent.title
            showyear = item.parent.aired_at.year
            filename = f"{showname} ({showyear}) - Season {str(item.number).zfill(2)}"
        elif isinstance(item, Episode):
            episode_string = ""
            episode_number: List[int] = item.get_file_episodes()
            if episode_number and item.number in episode_number:
                if len(episode_number) > 1:
                    episode_string = f"e{str(episode_number[0]).zfill(2)}-e{str(episode_number[-1]).zfill(2)}"
                else:
                    episode_string = f"e{str(item.number).zfill(2)}"
            if episode_string != "":
                showname = item.parent.parent.title
                showyear = item.parent.parent.aired_at.year
                filename = f"{showname} ({showyear}) - s{str(item.parent.number).zfill(2)}{episode_string}"
        return filename

    def delete_item_symlinks(self, item: "MediaItem") -> bool:
        """Delete symlinks and directories based on the item type."""
        if not isinstance(item, (Movie, Show)):
            logger.debug(f"skipping delete symlink for {item.log_string}: Not a movie or show")
            return False
        item_path = None
        if isinstance(item, Show):
            base_path = self.library_path_anime_shows if item.is_anime else self.library_path_shows
            item_path = base_path / f"{item.title.replace('/', '-')} ({item.aired_at.year}) {{imdb-{item.ids['imdb_id']}}}"
        elif isinstance(item, Movie):
            base_path = self.library_path_anime_movies if item.is_anime else self.library_path_movies
            item_path = base_path / f"{item.title.replace('/', '-')} ({item.aired_at.year}) {{imdb-{item.ids['imdb_id']}}}"
        return _delete_symlink(item, item_path)

def _delete_symlink(item: Union[Movie, Show], item_path: Path) -> bool:
    try:
        if item_path.exists():
            shutil.rmtree(item_path)
            logger.debug(f"Deleted symlink Directory for {item.log_string}")
            return True
        else:
            logger.debug(f"Symlink Directory for {item.log_string} does not exist, skipping symlink deletion")
            return True
    except FileNotFoundError as e:
        logger.error(f"File not found error when deleting symlink for {item.log_string}: {e}")
    except PermissionError as e:
        logger.error(f"Permission denied when deleting symlink for {item.log_string}: {e}")
    except Exception as e:
        logger.error(f"Failed to delete symlink for {item.log_string}, error: {e}")
    return False

def _get_item_path(item: Union[Movie, Episode]) -> Optional[Path]:
    """Quickly check if the file exists in the rclone path."""
    if not item.file:
        return None

    rclone_path = Path(settings_manager.settings.symlink.rclone_path)
    possible_folders = [item.folder, item.file, item.alternative_folder]

    for folder in possible_folders:
        if folder:
            file_path = rclone_path / folder / item.file
            if file_path.exists():
                return file_path

    # Not in a folder? Perhaps it's just sitting in the root.
    file = rclone_path / item.file
    if file.exists() and file.is_file():
        return file
    return None