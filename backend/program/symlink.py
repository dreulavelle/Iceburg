import os
import time
from datetime import datetime
from pathlib import Path

from program.media.item import Episode, MediaItem, Movie, Season
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

    def __init__(self):
        self.key = "symlink"
        self.settings = settings_manager.settings.symlink
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

    def on_symlink_deleted(self, symlink_path):
        """Handle a symlink deletion event."""
        src = Path(symlink_path)
        if src.is_symlink():
            dst = src.resolve()
            logger.log("FILES", f"Symlink deleted: {src} -> {dst}")
        else:
            logger.log("FILES", f"Symlink deleted: {src} (target unknown)")
        # TODO: Implement logic to handle deletion..

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

    def run(self, item):
        """Check if the media item exists and create a symlink if it does"""
        try:
            if self._symlink(item):
                item.set("symlinked", True)
                item.set("symlinked_at", datetime.now())
        except Exception as e:
            logger.exception(f"Exception thrown when creating symlink for {item.log_string}: {e}")

        item.set("symlinked_times", item.symlinked_times + 1)
        yield item

    @staticmethod
    def should_submit(item) -> bool:
        """Check if the item should be submitted for symlink creation."""
        if isinstance(item, (Movie, Episode)):
            if Symlinker.file_check(item):
                return True

        # If we've tried 3 times to symlink the file, give up
        if item.symlinked_times >= 3:
            if isinstance(item, (Movie, Episode)):
                item.set("file", None)
                item.set("folder", None)
                item.set("streams", {})  # Ensure rescraping
                item.set("symlinked_times", 0)
                infohash = item.active_stream.get("hash")
                if infohash:
                    hash_cache.blacklist(infohash)
                else:
                    logger.error(f"Failed to retrieve hash for {item.log_string}, unable to blacklist")
            return False

        # If the file doesn't exist, we should wait a bit and try again
        logger.log("NOT_FOUND", f"Retrying file check in 10 seconds: {item.log_string}")
        time.sleep(10)
        return True

    @staticmethod
    def file_check(item: MediaItem) -> bool:
        """Check if the file exists in the rclone path."""
        if not item.file or item.file == "None.mkv":
            logger.error(f"Invalid file for {item.log_string}: {item.file}. Needs to be rescraped.")
            return False

        try:
            if item.folder and item.alternative_folder and item.folder == item.alternative_folder:
                alternative_folder = os.path.splitext(item.file)[0]
                item.set("alternative_folder", alternative_folder)
        except Exception as e:
            logger.error(f"Exception occurred while processing file for {item.log_string}: {e}")
            raise

        rclone_path = Path(settings_manager.settings.symlink.rclone_path)
        
        std_file_path = rclone_path / item.folder / item.file if item.folder else None
        alt_file_path = rclone_path / item.alternative_folder / item.file if item.alternative_folder else None
        thd_file_path = rclone_path / item.file / item.file

        for attempt in range(2):
            if std_file_path and std_file_path.exists():
                return True
            elif alt_file_path and alt_file_path.exists():
                item.set("folder", item.alternative_folder)
                return True
            elif thd_file_path.exists():
                item.set("folder", item.file)
                return True

            if attempt < 1:
                logger.log("FILES", f"File not found for {item.log_string} with file: {item.file}. Retrying in 10 seconds...")
                time.sleep(10)
            else:
                logger.log("FILES", f"File not found for {item.log_string} after 1 attempt. Searching entire rclone_path...")

        # On the 2nd attempt, search the entire rclone_path
        for file_path in rclone_path.rglob(item.file):
            if file_path.exists():
                item.set("folder", str(file_path.parent.relative_to(rclone_path)))
                logger.log("FILES", f"File found for {item.log_string} by searching rclone_path: {file_path}")
                return True

        logger.log("FILES", f"File not found for {item.log_string} with file: {item.file} after searching rclone_path.")
        return False

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

    def _symlink(self, item) -> bool:
        """Create a symlink for the given media item if it does not already exist."""
        if isinstance(item, Season) and all(ep.file and ep.folder for ep in item.episodes):
            success = True
            for episode in item.episodes:
                if not self._symlink_episode(episode):
                    success = False
            return success

        return self._symlink_single(item)

    def _symlink_single(self, item) -> bool:
        """Create a symlink for a single media item."""
        if not item.file or item.file == "None.mkv":
            logger.error(f"Cannot create symlink for {item.log_string}: Invalid file {item.file}. Needs to be rescraped.")
            return False

        extension = os.path.splitext(item.file)[1][1:]
        symlink_filename = f"{self._determine_file_name(item)}.{extension}"
        destination = self._create_item_folders(item, symlink_filename)
        source = os.path.join(self.rclone_path, item.folder, item.file)

        if not os.path.exists(source):
            return False

        try:
            os.symlink(source, destination)
            logger.log("SYMLINKER", f"Created symlink for {item.log_string}")
            item.set("symlinked", True)
            item.set("symlinked_at", datetime.now())
            item.set("symlinked_times", item.symlinked_times + 1)
            return True
        except FileExistsError:
            return True
        except OSError as e:
            logger.error(f"Failed to create symlink for {item.log_string}: {e}")
            return False

    def _symlink_episode(self, episode) -> bool:
        """Create a symlink for an individual episode."""
        return self._symlink_single(episode)

    def _create_item_folders(self, item, filename) -> str:
        """Create necessary folders and determine the destination path for symlinks."""
        if isinstance(item, Movie):
            movie_folder = (
                f"{item.title.replace('/', '-')} ({item.aired_at.year}) "
                + "{imdb-"
                + item.imdb_id
                + "}"
            )
            destination_folder = os.path.join(self.library_path_movies, movie_folder)
            if not os.path.exists(destination_folder):
                os.mkdir(destination_folder)
            destination_path = os.path.join(
                destination_folder, filename.replace("/", "-")
            )
            item.set("update_folder", os.path.join(self.library_path_movies, movie_folder))
        elif isinstance(item, Season):
            show = item.parent
            folder_name_show = (
                f"{show.title.replace('/', '-')} ({show.aired_at.year})"
                + " {"
                + show.imdb_id
                + "}"
            )
            show_path = os.path.join(self.library_path_shows, folder_name_show)
            os.makedirs(show_path, exist_ok=True)
            folder_season_name = f"Season {str(item.number).zfill(2)}"
            season_path = os.path.join(show_path, folder_season_name)
            os.makedirs(season_path, exist_ok=True)
            destination_path = os.path.join(season_path, filename.replace("/", "-"))
            item.set("update_folder", os.path.join(season_path))
        elif isinstance(item, Episode):
            show = item.parent.parent
            folder_name_show = (
                f"{show.title.replace('/', '-')} ({show.aired_at.year})"
                + " {"
                + show.imdb_id
                + "}"
            )
            show_path = os.path.join(self.library_path_shows, folder_name_show)
            os.makedirs(show_path, exist_ok=True)
            season = item.parent
            folder_season_name = f"Season {str(season.number).zfill(2)}"
            season_path = os.path.join(show_path, folder_season_name)
            os.makedirs(season_path, exist_ok=True)
            destination_path = os.path.join(season_path, filename.replace("/", "-"))
            item.set("update_folder", os.path.join(season_path))
        return destination_path

