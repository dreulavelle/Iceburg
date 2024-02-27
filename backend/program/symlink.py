"""Symlinking module"""
import os
from datetime import datetime
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from utils.logger import logger
from program.settings.manager import settings_manager
from program.media.item import Movie, Episode



class DeleteHandler(FileSystemEventHandler):
    """Handles the deletion of symlinks."""

    def __init__(self, symlinker):
        super().__init__()
        self.symlinker = symlinker

    def on_deleted(self, event):
        """Called when a file or directory is deleted."""
        if event.src_path:
            # TODO: Check if its a file or directory and handle accordingly.
            # This is getting called for the file + directory as well..
            # It will first get called on the file, then the parent folder.
            # This is not what we want.. but atleast it's a start.
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
        self.rclone_path = self.settings.rclone_path
        self.initialized = self.validate()
        if not self.initialized:
            logger.error("Symlink initialization failed due to invalid configuration.")
            return
        if self.initialized:
            self.start_monitor()
        logger.info("Rclone path symlinks are pointed to: %s", self.rclone_path)
        logger.info("Symlinks will be placed in: %s", self.settings.library_path)
        logger.info("Symlink initialized!")

    def validate(self):
        """Validate paths and create the initial folders."""
        library_path = self.settings.library_path
        if (
            not self.rclone_path
            or not library_path
            or self.rclone_path == Path(".")
            or library_path == Path(".")
        ):
            logger.error(
                "rclone_path or library_path not provided, is empty, or is set to the current directory."
            )
            return False
        if not self.rclone_path.is_absolute():
            logger.error("rclone_path is not an absolute path: %s", self.rclone_path)
            return False
        if not library_path.is_absolute():
            logger.error("library_path is not an absolute path: %s", library_path)
            return False
        try:
            if (
                all_path := self.settings.rclone_path / "__all__"
            ).exists() and all_path.is_dir():
                logger.debug(
                    "Detected Zurg rclone_path. Using __all__ folder for rclone_path."
                )
                self.rclone_path = all_path
            elif (
                torrent_path := self.settings.rclone_path / "torrents"
            ).exists() and torrent_path.is_dir():
                logger.debug(
                    "Detected standard rclone_path. Using torrents folder for rclone_path."
                )
                self.rclone_path = torrent_path
            if not self.create_initial_folders():
                logger.error(
                    "Failed to create initial library folders in your library_path."
                )
                return False
            return True
        except FileNotFoundError as e:
            logger.error("Path not found: %s", e)
        except PermissionError as e:
            logger.error("Permission denied when accessing path: %s", e)
        except OSError as e:
            logger.error("OS error when validating paths: %s", e)
        return False

    def start_monitor(self):
        """Starts monitoring the library path for symlink deletions."""
        self.event_handler = DeleteHandler(self)
        self.observer = Observer()
        self.observer.schedule(self.event_handler, self.settings.library_path, recursive=True)
        self.observer.start()
        logger.debug("Start monitor for symlink deletions.")

    def stop_monitor(self):
        """Stops the directory monitoring."""
        if hasattr(self, 'observer'):
            self.observer.stop()
            self.observer.join()
            logger.debug("Stopped monitoring for symlink deletions.")

    def on_symlink_deleted(self, path):
        """Handle a symlink deletion event."""
        logger.debug(f"Detected deletion of symlink: {path}")
        # TODO: Implement logic to handle deletion..
        # We should use `update_path` to determine the item,
        # and work with the item (instead of path) to remove from content services..
        # Need to bring in media_items from the main program and remove the item from it..

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
        except PermissionError as e:
            logger.error("Permission denied when creating directory: %s", e)
            return False
        except OSError as e:
            logger.error("OS error when creating directory: %c", e)
            return False
        return True

    def run(self, item):
        """Check if the media item exists and create a symlink if it does"""
        found = False
        rclone_path = Path(self.settings.rclone_path)
        if os.path.exists(rclone_path / item.folder / item.file):
            found = True
        elif os.path.exists(rclone_path / item.alternative_folder / item.file):
            item.set("folder", item.alternative_folder)
            found = True
        elif os.path.exists(rclone_path / item.file / item.file):
            item.set("folder", item.file)
            found = True
        if found:
            self._symlink(item)
        else:
            logger.error(
                "Could not find %s in subdirectories of %s to create symlink,"
                " maybe it failed to download?", item.log_string, rclone_path
            )
        item.symlinked_at = datetime.now()
        item.symlinked_times += 1
        yield item

    @staticmethod
    def should_submit(item):
        return item.symlinked_times < 3

    def _determine_file_name(self, item):
        """Determine the filename of the symlink."""
        filename = None
        if item.type == "movie":
            filename = (
                f"{item.title} ({item.aired_at.year}) " + "{imdb-" + item.imdb_id + "}"
            )
        if item.type == "episode":
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

    def _symlink(self, item):
        """Create a symlink for the given media item"""
        # Symlinks get created on host as: destination -> source
        extension = item.file.split(".")[-1]
        symlink_filename = f"{self._determine_file_name(item)}.{extension}"
        destination = self._create_item_folders(item, symlink_filename)
        source = os.path.join(self.rclone_path, item.folder, item.file)
        if destination:
            try:
                os.remove(destination)
            except FileNotFoundError:
                pass
            os.symlink(
                source,
                destination,
            )
            logger.debug("Created symlink for %s", item.log_string)
            item.symlinked = True
        else:
            logger.debug(
                "Could not create symlink for item_id (%s) to (%s)",
                item.item_id,
                destination,
            )

    def _create_item_folders(self, item, filename) -> str:
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
            item.set(
                "update_folder", os.path.join(self.library_path_movies, movie_folder)
            )
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
