import asyncio
import contextlib
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from program.media.container import MediaItemContainer
from program.media.item import Episode, ItemId, Movie, Season, Show
from program.settings.manager import settings_manager
from utils.logger import logger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .cache import hash_cache


class DeleteHandler(FileSystemEventHandler):
    """Handles the deletion of symlinks."""

    def __init__(self, symlinker):
        super().__init__()
        self.symlinker = symlinker

    def on_deleted(self, event):
        """Called when a file or directory is deleted."""
        if event.src_path:
            self.symlinker.on_symlink_deleted(event.src_path)


class Symlinker:
    """
    A class that represents a symlinker thread.

    Settings Attributes:
        rclone_path (str): The absolute path of the rclone mount root directory.
        library_path (str): The absolute path of the location we will create our symlinks that point to the rclone_path.
    """

    def __init__(self, media_items: MediaItemContainer):
        self.key = "symlink"
        self.settings = settings_manager.settings.symlink
        self.media_items = media_items
        # we can't delete from rclone if this is enabled
        self.torbox_enabled = settings_manager.settings.downloaders.torbox.enabled
        self.rclone_path = self.settings.rclone_path
        self.initialized = self.validate()
        if not self.initialized:
            logger.error("Symlink initialization failed due to invalid configuration.")
            return
        if self.initialized:
            self.start_monitor()
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
        if not self.rclone_path.is_absolute():
            logger.error(f"rclone_path is not an absolute path: {self.rclone_path}")
            return False
        if not library_path.is_absolute():
            logger.error(f"library_path is not an absolute path: {library_path}")
            return False
        if not self.rclone_path.exists():
            logger.error(f"rclone_path does not exist: {self.rclone_path}")
            return False
        if not library_path.exists():
            logger.error(f"library_path does not exist: {library_path}")
            return False
        return self.create_initial_folders()

    def create_initial_folders(self):
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

    def run(self, item: Union[Movie, Episode, Season]):
        """Check if the media item exists and create a symlink if it does"""
        try:
            if isinstance(item, Show):
                all_symlinked = True
                for season in item.seasons:
                    for episode in season.episodes:
                        if not episode.symlinked and episode.file and episode.folder:
                            if self._symlink(episode):
                                episode.set("symlinked", True)
                                episode.set("symlinked_at", datetime.now())
                            else:
                                all_symlinked = False
                if all_symlinked:
                    logger.log("SYMLINKER", f"Symlinked all episodes for show {item.log_string}")
                else:
                    logger.error(f"Failed to symlink some episodes for show {item.log_string}")
            elif isinstance(item, Season):
                all_symlinked = True
                successfully_symlinked_episodes = []
                for episode in item.episodes:
                    if not episode.symlinked and episode.file and episode.folder:
                        if self._symlink(episode):
                            episode.set("symlinked", True)
                            episode.set("symlinked_at", datetime.now())
                            successfully_symlinked_episodes.append(episode)
                        else:
                            all_symlinked = False
                if all_symlinked:
                    logger.log("SYMLINKER", f"Symlinked all episodes for {item.log_string}")
                else:
                    for episode in successfully_symlinked_episodes:
                        logger.log("SYMLINKER", f"Symlink created for {episode.log_string}")
            elif isinstance(item, (Movie, Episode)):
                if not item.symlinked and item.file and item.folder:
                    if self._symlink(item):
                        logger.log("SYMLINKER", f"Symlink created for {item.log_string}")
                    else:
                        logger.error(f"Failed to create symlink for {item.log_string}")
            item.set("symlinked", True)
            item.set("symlinked_at", datetime.now())
        except Exception as e:
            logger.exception(f"Exception thrown when creating symlink for {item.log_string}: {e}")

        item.set("symlinked_times", item.symlinked_times + 1)
        yield item

    @staticmethod
    def should_submit(item: Union[Movie, Episode, Season]) -> bool:
        """Check if the item should be submitted for symlink creation."""

        if isinstance(item, Show):
            all_episodes_ready = True
            for season in item.seasons:
                for episode in season.episodes:
                    if not episode.file or not episode.folder or episode.file == "None.mkv":
                        logger.warning(f"Cannot submit {episode.log_string} for symlink: Invalid file or folder. Needs to be rescraped.")
                        blacklist_item(episode)
                        all_episodes_ready = False
                    elif not quick_file_check(episode):
                        logger.debug(f"File not found for {episode.log_string} at the moment, waiting for it to become available")
                        if not _wait_for_file(episode):
                            all_episodes_ready = False
                            break  # Give up on the whole season if one episode is not found in 90 seconds
            if not all_episodes_ready:
                for episode in item.episodes:
                    blacklist_item(episode)
                logger.warning(f"Cannot submit season {item.log_string} for symlink: One or more episodes need to be rescraped.")
            return all_episodes_ready

        if isinstance(item, Season):
            all_episodes_ready = True
            for episode in item.episodes:
                if not episode.file or not episode.folder or episode.file == "None.mkv":
                    logger.warning(f"Cannot submit {episode.log_string} for symlink: Invalid file or folder. Needs to be rescraped.")
                    blacklist_item(episode)
                    all_episodes_ready = False
                elif not quick_file_check(episode):
                    logger.debug(f"File not found for {episode.log_string} at the moment, waiting for it to become available")
                    if not _wait_for_file(episode):
                        all_episodes_ready = False
                        break  # Give up on the whole season if one episode is not found in 90 seconds
            if not all_episodes_ready:
                for episode in item.episodes:
                    blacklist_item(episode)
                logger.warning(f"Cannot submit season {item.log_string} for symlink: One or more episodes need to be rescraped.")
            return all_episodes_ready

        if isinstance(item, (Movie, Episode)):
            if not item.file or not item.folder or item.file == "None.mkv":
                logger.warning(f"Cannot submit {item.log_string} for symlink: Invalid file or folder. Needs to be rescraped.")
                blacklist_item(item)
                return False

        if item.symlinked_times < 3:
            if quick_file_check(item):
                logger.log("SYMLINKER", f"File found for {item.log_string}, submitting to be symlinked")
                return True
            else:
                logger.debug(f"File not found for {item.log_string} at the moment, waiting for it to become available")
                if _wait_for_file(item):
                    return True
                return False

        item.set("symlinked_times", item.symlinked_times + 1)

        if item.symlinked_times >= 3:
            rclone_path = Path(settings_manager.settings.symlink.rclone_path)
            if search_file(rclone_path, item):
                logger.log("SYMLINKER", f"File found for {item.log_string}, creating symlink")
                return True
            else:
                logger.log("SYMLINKER", f"File not found for {item.log_string} after 3 attempts, blacklisting")
                blacklist_item(item)
                return False

        logger.debug(f"Item {item.log_string} not submitted for symlink, file not found yet")
        return False

    def _symlink(self, item: Union[Movie, Episode]) -> bool:
        """Create a symlink for the given media item if it does not already exist."""
        extension = os.path.splitext(item.file)[1][1:]
        symlink_filename = f"{self._determine_file_name(item)}.{extension}"
        destination = self._create_item_folders(item, symlink_filename)
        source = os.path.join(self.rclone_path, item.folder, item.file)

        if not os.path.exists(source):
            logger.error(f"Source file does not exist: {source}")
            return False

        try:
            with contextlib.suppress(FileExistsError):
                os.symlink(source, destination)
            item.set("symlinked", True)
            item.set("symlinked_at", datetime.now())
            item.set("symlinked_times", item.symlinked_times + 1)
        except PermissionError as e:
            logger.error(f"Permission denied when creating symlink for {item.log_string}: {e}")
            return False
        except OSError as e:
            logger.error(f"OS error when creating symlink for {item.log_string}: {e}")
            return False

        # Validate the symlink
        if not os.path.islink(destination) or not os.path.exists(destination):
            logger.error(f"Symlink validation failed for {item.log_string}: {destination}")
            return False

        return True

    def _create_item_folders(self, item: Union[Movie, Show, Season, Episode], filename: str) -> str:
        """Create necessary folders and determine the destination path for symlinks."""
        is_anime = hasattr(item, 'is_anime') and item.is_anime

        movie_path = self.library_path_movies
        show_path = self.library_path_shows

        if is_anime:
            if isinstance(item, Movie):
                movie_path = self.library_path_anime_movies
            elif isinstance(item, (Show, Season, Episode)):
                show_path = self.library_path_anime_shows

        def create_folder_path(base_path, *subfolders):
            path = os.path.join(base_path, *subfolders)
            os.makedirs(path, exist_ok=True)
            return path

        if isinstance(item, Movie):
            movie_folder = f"{item.title.replace('/', '-')} ({item.aired_at.year}) {{imdb-{item.imdb_id}}}"
            destination_folder = create_folder_path(movie_path, movie_folder)
            item.set("update_folder", destination_folder)
        elif isinstance(item, Show):
            folder_name_show = f"{item.title.replace('/', '-')} ({item.aired_at.year}) {{imdb-{item.imdb_id}}}"
            destination_folder = create_folder_path(show_path, folder_name_show)
            item.set("update_folder", destination_folder)
        elif isinstance(item, Season):
            show = item.parent
            folder_name_show = f"{show.title.replace('/', '-')} ({show.aired_at.year}) {{imdb-{show.imdb_id}}}"
            show_path = create_folder_path(show_path, folder_name_show)
            folder_season_name = f"Season {str(item.number).zfill(2)}"
            destination_folder = create_folder_path(show_path, folder_season_name)
            item.set("update_folder", destination_folder)
        elif isinstance(item, Episode):
            show = item.parent.parent
            folder_name_show = f"{show.title.replace('/', '-')} ({show.aired_at.year}) {{imdb-{show.imdb_id}}}"
            show_path = create_folder_path(show_path, folder_name_show)
            season = item.parent
            folder_season_name = f"Season {str(season.number).zfill(2)}"
            destination_folder = create_folder_path(show_path, folder_season_name)
            item.set("update_folder", destination_folder)

        destination_path = os.path.join(destination_folder, filename.replace("/", "-"))
        return destination_path

    def start_monitor(self):
        """Starts monitoring the library path for symlink deletions."""
        self.event_handler = DeleteHandler(self)
        self.observer = Observer()
        self.observer.schedule(
            self.event_handler, self.settings.library_path, recursive=True
        )
        self.observer.start()
        logger.log("FILES", "Symlink deletion monitoring started")

    def stop_monitor(self):
        """Stops the directory monitoring."""
        if hasattr(self, "observer"):
            self.observer.stop()
            self.observer.join()
            logger.log("FILES", "Stopped monitoring for symlink deletions")

    def on_symlink_deleted(self, src: str) -> None:
        """Handle the event when a symlink is deleted."""
        logger.info(f"Symlink deleted: {src}")
        src_path = Path(src)
        imdb_id = self.extract_imdb_id(src_path)
        season_number, episode_number = self.extract_season_episode(src_path.name)

        if imdb_id:
            item_id = ItemId(imdb_id)
            if season_number is not None:
                item_id = ItemId(str(season_number), parent_id=item_id)
            if episode_number is not None:
                item_id = ItemId(str(episode_number), parent_id=item_id)

            item = self.media_items.get(item_id)
            if item:
                if isinstance(item, Episode) and not item.file:
                    logger.error(f"File attribute is invalid for item: {item.log_string}")
                    return
                self.delete_item_symlinks(item)
                self.media_items.remove(item)
                logger.log("FILES", f"Successfully removed item: {item.log_string}")
            else:
                logger.error(f"Failed to find item with IMDb ID {imdb_id}, season {season_number}, episode {episode_number}")
        else:
            logger.error(f"IMDb ID not found in path: {src}")

    def extract_imdb_id(self, path: Path) -> Optional[str]:
        """Extract IMDb ID from the file or folder name using regex."""
        match = re.search(r'tt\d+', path.name)
        if match:
            return match.group(0)
        match = re.search(r'tt\d+', path.parent.name)
        if match:
            return match.group(0)
        match = re.search(r'tt\d+', path.parent.parent.name)
        if match:
            return match.group(0)

        logger.error(f"IMDb ID not found in file or folder name: {path}")
        return None

    def extract_season_episode(self, filename: str) -> (Optional[int], Optional[int]):
        """Extract season and episode numbers from the file name using regex."""
        season = episode = None
        match = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
        return season, episode

    def _determine_file_name(self, item) -> str | None:
        """Determine the filename of the symlink."""
        filename = None
        if isinstance(item, Movie):
            filename = f"{item.title} ({item.aired_at.year}) " + "{imdb-" + item.imdb_id + "}"
        elif isinstance(item, Season):
            showname = item.parent.title
            showyear = item.parent.aired_at.year
            filename = f"{showname} ({showyear}) - Season {str(item.number).zfill(2)}"
        elif isinstance(item, Episode):
            episode_string = ""
            episode_number = item.get_file_episodes()
            if episode_number[0] == item.number:
                if len(episode_number) > 1:
                    episode_string = f"e{str(episode_number[0]).zfill(2)}-e{str(episode_number[-1]).zfill(2)}"
                else:
                    episode_string = f"e{str(item.number).zfill(2)}"
            if episode_string != "":
                showname = item.parent.parent.title
                showyear = item.parent.parent.aired_at.year
                filename = f"{showname} ({showyear}) - s{str(item.parent.number).zfill(2)}{episode_string} - {item.title}" 
        return filename

    def delete_item_symlinks(self, item: Union[Movie, Episode, Season, Show]):
        """Delete symlinks and directories based on the item type."""
        if isinstance(item, Show):
            self._delete_show_symlinks(item)
        elif isinstance(item, Season):
            self._delete_season_symlinks(item)
        elif isinstance(item, (Movie, Episode)):
            self._delete_single_symlink(item)
        else:
            logger.error(f"Unsupported item type for deletion: {type(item)}")

    def _delete_show_symlinks(self, show: Show):
        """Delete all symlinks and the directory for the show."""
        show_path = self.library_path_shows / f"{show.title.replace('/', '-')} ({show.aired_at.year}) {{imdb-{show.imdb_id}}}"
        if show_path.exists():
            shutil.rmtree(show_path)
            logger.info(f"Deleted all symlinks and directory for show: {show.log_string}")

    def _delete_season_symlinks(self, season: Season):
        """Delete all symlinks in the season and its directory."""
        show = season.parent
        season_path = self.library_path_shows / f"{show.title.replace('/', '-')} ({show.aired_at.year}) {{imdb-{show.imdb_id}}}" / f"Season {str(season.number).zfill(2)}"
        if season_path.exists():
            shutil.rmtree(season_path)
            logger.info(f"Deleted all symlinks and directory for season: {season.log_string}")

    def _delete_single_symlink(self, item: Union[Movie, Episode]):
        """Delete the specific symlink for a movie or episode."""
        symlink_path = Path(item.update_folder) / f"{self._determine_file_name(item)}.{os.path.splitext(item.file)[1][1:]}"
        if symlink_path.exists() and symlink_path.is_symlink():
            symlink_path.unlink()
            logger.info(f"Deleted symlink for {item.log_string}")


def _wait_for_file(item: Union[Movie, Episode], timeout: int = 90) -> bool:
    """Wrapper function to run the asynchronous wait_for_file function."""
    return asyncio.run(wait_for_file(item, timeout))

async def wait_for_file(item: Union[Movie, Episode], timeout: int = 90) -> bool:
    """Asynchronously wait for the file to become available within the given timeout."""
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        # keep trying to find the file until timeout duration is hit
        if quick_file_check(item):
            logger.log("SYMLINKER", f"File found for {item.log_string}")
            return True
        await asyncio.sleep(5)
        # If 30 seconds have passed, try searching for the file
        if time.monotonic() - start_time >= 30:
            rclone_path = Path(settings_manager.settings.symlink.rclone_path)
            if search_file(rclone_path, item):
                logger.log("SYMLINKER", f"File found for {item.log_string} after searching")
                return True
    logger.log("SYMLINKER", f"File not found for {item.log_string} after waiting for {timeout} seconds, blacklisting")
    blacklist_item(item)
    return False

def quick_file_check(item: Union[Movie, Episode]) -> bool:
    """Quickly check if the file exists in the rclone path."""
    if not isinstance(item, (Movie, Episode)):
        logger.debug(f"Cannot create symlink for {item.log_string}: Not a movie or episode")
        return False

    if not item.file or item.file == "None.mkv":
        logger.log("NOT_FOUND", f"Invalid file for {item.log_string}: {item.file}. Needs to be rescraped.")
        return False

    rclone_path = Path(settings_manager.settings.symlink.rclone_path)
    possible_folders = [item.folder, item.file, item.alternative_folder]

    for folder in possible_folders:
        if folder:
            file_path = rclone_path / folder / item.file
            if file_path.exists():
                item.set("folder", folder)
                return True
    return False

def search_file(rclone_path: Path, item: Union[Movie, Episode]) -> bool:
    """Search for the file in the rclone path."""
    if not isinstance(item, (Movie, Episode)):
        logger.debug(f"Cannot search for file for {item.log_string}: Not a movie or episode")
        return False

    filename = item.file
    if not filename:
        return False
    logger.debug(f"Searching for file {filename} in {rclone_path}")
    try:
        for root, _, files in os.walk(rclone_path):
            if filename in files:
                relative_root = Path(root).relative_to(rclone_path).as_posix()
                item.set("folder", relative_root)
                return True
        logger.debug(f"File {filename} not found in {rclone_path}")
    except Exception as e:
        logger.error(f"Error occurred while searching for file {filename} in {rclone_path}: {e}")
    return False

def blacklist_item(item):
    """Blacklist the item and reset its attributes to be rescraped."""
    infohash = get_infohash(item)
    reset_item(item)
    if infohash:
        hash_cache.blacklist(infohash)
    else:
        logger.error(f"Failed to retrieve hash for {item.log_string}, unable to blacklist")

def reset_item(item):
    """Reset item attributes for rescraping."""
    item.set("file", None)
    item.set("folder", None)
    item.set("streams", {})
    item.set("active_stream", {})
    item.set("symlinked_times", 0)
    item.set("scraped_times", 0)
    logger.debug(f"Item {item.log_string} reset for rescraping")

def get_infohash(item):
    """Retrieve the infohash from the item or its parent."""
    infohash = item.active_stream.get("hash")
    if isinstance(item, Episode) and not infohash:
        infohash = item.parent.active_stream.get("hash")
    if isinstance(item, Movie) and not infohash:
        logger.error(f"Failed to retrieve hash for {item.log_string}, unable to blacklist")
    return infohash
