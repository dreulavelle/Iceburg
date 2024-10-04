import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Generator

from sqla_wrapper import Session

from program.db.db import db
from program.media.subtitle import Subtitle
from program.settings.manager import settings_manager
from utils.logger import logger

if TYPE_CHECKING:
    from program.media.item import Episode, MediaItem, Movie, Show

imdbid_pattern = re.compile(r"tt\d+")
season_pattern = re.compile(r"s(\d+)")
episode_pattern = re.compile(r"e(\d+)")

class SymlinkLibrary:
    def __init__(self):
        self.key = "symlinklibrary"
        self.settings = settings_manager.settings.symlink
        self.initialized = self.validate()
        if not self.initialized:
            logger.error("SymlinkLibrary initialization failed due to invalid configuration.")
            return

    def validate(self) -> bool:
        """Validate the symlink library settings."""
        library_path = Path(self.settings.library_path).resolve()
        if library_path == Path.cwd().resolve():
            logger.error("Library path not set or set to the current directory in SymlinkLibrary settings.")
            return False

        required_dirs: list[str] = ["shows", "movies"]
        if self.settings.separate_anime_dirs:
            required_dirs.extend(["anime_shows", "anime_movies"])
        missing_dirs: list[str] = [d for d in required_dirs if not (library_path / d).exists()]

        if missing_dirs:
            available_dirs: str = ", ".join(os.listdir(library_path))
            logger.error(f"Missing required directories in the library path: {', '.join(missing_dirs)}.")
            logger.debug(f"Library directory contains: {available_dirs}")
            return False
        return True

    def run(self) -> list["MediaItem"]:
        """
        Create a library from the symlink paths. Return stub items that should
        be fed into an Indexer to have the rest of the metadata filled in.
        """
        from program.media.item import Movie

        items = []
        for directory, item_type, is_anime in [("shows", "show", False), ("anime_shows", "anime show", True)]:
            if not self.settings.separate_anime_dirs and is_anime:
                continue
            items.extend(process_shows(self.settings.library_path / directory, item_type, is_anime))

        for directory, item_type, is_anime in [("movies", "movie", False), ("anime_movies", "anime movie", True)]:
            if not self.settings.separate_anime_dirs and is_anime:
                continue
            items.extend(process_items(self.settings.library_path / directory, Movie, item_type, is_anime))

        return items

def process_items(directory: Path, item_class, item_type: str, is_anime: bool = False):
    """Process items in the given directory and yield MediaItem instances."""
    items = [
        (Path(root), file)
        for root, _, files in os.walk(directory)
        for file in files
        if not file.endswith('.srt')
    ]
    for path, filename in items:
        if filename.endswith(".srt"):
            continue
        imdb_id = re.search(r"(tt\d+)", filename)
        title = re.search(r"(.+)?( \()", filename)
        if not imdb_id or not title:
            logger.error(f"Can't extract {item_type} imdb_id or title at path {path / filename}")
            continue

        item = item_class({"imdb_id": imdb_id.group(), "title": title.group(1)})
        resolve_symlink_and_set_attrs(item, path / filename)
        find_subtitles(item, path / filename)

        if settings_manager.settings.force_refresh:
            item.set("symlinked", True)
            item.set("update_folder", str(path))
        else:
            item.set("symlinked", True)
            item.set("update_folder", "updated")
        if is_anime:
            item.is_anime = True
        yield item

def resolve_symlink_and_set_attrs(item, path: Path) -> Path:
    # Resolve the symlink path
    resolved_path = (path).resolve()
    item.file = str(resolved_path.stem)
    item.folder = str(resolved_path.parent.stem)
    item.symlink_path = str(path)

def find_subtitles(item, path: Path):
    # Scan for subtitle files
    for file in os.listdir(path.parent):
        if file.startswith(Path(item.symlink_path).stem) and file.endswith(".srt"):
            lang_code = file.split(".")[1]
            item.subtitles.append(Subtitle({lang_code: (path.parent / file).__str__()}))
            logger.debug(f"Found subtitle file {file}.")

def process_shows(directory: Path, item_type: str, is_anime: bool = False) -> Generator["Show", None, None]:
    """Process shows in the given directory and yield Show instances."""
    from program.media.item import Episode, Season, Show
    for show in os.listdir(directory):
        imdb_id = re.search(r"(tt\d+)", show)
        title = re.search(r"(.+)?( \()", show)
        if not imdb_id or not title:
            logger.log("NOT_FOUND", f"Can't extract {item_type} imdb_id or title at path {directory / show}")
            continue
        show_item = Show({"imdb_id": imdb_id.group(), "title": title.group(1)})
        if is_anime:
            show_item.is_anime = True
        seasons = {}
        for season in os.listdir(directory / show):
            if not (season_number := re.search(r"(\d+)", season)):
                logger.log("NOT_FOUND", f"Can't extract season number at path {directory / show / season}")
                continue
            season_item = Season({"number": int(season_number.group())})
            episodes = {}
            for episode in os.listdir(directory / show / season):
                if not (episode_number := re.search(r"s\d+e(\d+)", episode, re.IGNORECASE)):
                    logger.log("NOT_FOUND", f"Can't extract episode number at path {directory / show / season / episode}")
                    # Delete the episode since it can't be indexed
                    os.remove(directory / show / season / episode)
                    continue

                episode_item = Episode({"number": int(episode_number.group(1))})
                resolve_symlink_and_set_attrs(episode_item, Path(directory) / show / season / episode)
                find_subtitles(episode_item, Path(directory) / show / season / episode)
                if settings_manager.settings.force_refresh:
                    episode_item.set("symlinked", True)
                    episode_item.set("update_folder", str(Path(directory) / show / season / episode))
                else:
                    episode_item.set("symlinked", True)
                    episode_item.set("update_folder", "updated")
                if is_anime:
                    episode_item.is_anime = True
                episodes[int(episode_number.group(1))] = episode_item
            if len(episodes) > 0:
                for i in range(1, max(episodes.keys())+1):
                    season_item.add_episode(episodes.get(i, Episode({"number": i})))
                seasons[int(season_number.group())] = season_item
        if len(seasons) > 0:
            for i in range(1, max(seasons.keys())+1):
                show_item.add_season(seasons.get(i, Season({"number": i})))
        yield show_item


def build_file_map(directory: str) -> dict[str, str]:
    """Build a map of filenames to their full paths in the directory."""
    file_map = {}

    def scan_dir(path):
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_file():
                    file_map[entry.name] = entry.path
                elif entry.is_dir():
                    scan_dir(entry.path)

    scan_dir(directory)
    return file_map

def find_broken_symlinks(directory: str) -> list[tuple[str, str]]:
    """Find all broken symlinks in the directory."""
    broken_symlinks = []
    for root, dirs, files in os.walk(directory):
        for name in files + dirs:
            full_path = os.path.join(root, name)
            if os.path.islink(full_path):
                target = os.readlink(full_path)
                if not os.path.exists(os.path.realpath(full_path)):
                    broken_symlinks.append((full_path, target))
    return broken_symlinks

def fix_broken_symlinks(library_path, rclone_path, max_workers=4):
    """Find and fix all broken symlinks in the library path using files from the rclone path."""
    missing_files = 0

    def check_and_fix_symlink(symlink_path, file_map):
        """Check and fix a single symlink."""
        nonlocal missing_files

        if isinstance(symlink_path, tuple):
            symlink_path = symlink_path[0]

        target_path = os.readlink(symlink_path)
        filename = os.path.basename(target_path)
        dirname = os.path.dirname(target_path).split("/")[-1]
        correct_path = file_map.get(filename)
        failed = False

        with db.Session() as session:
            items = get_items_from_filepath(session, symlink_path)
            if not items:
                logger.log("NOT_FOUND", f"Could not find item in database for path: {symlink_path}")
                return

            if correct_path:
                os.remove(symlink_path)
                os.symlink(correct_path, symlink_path)
                try:
                    for item in items:
                        item = session.merge(item)
                        item.file = filename
                        item.folder = dirname
                        item.symlinked = True
                        item.symlink_path = correct_path
                        item.update_folder = correct_path
                        item.store_state()
                        session.merge(item)
                        logger.log("FILES", f"Retargeted broken symlink for {item.log_string} with correct path: {correct_path}")
                except Exception as e:
                    logger.error(f"Failed to fix {item.log_string} with path: {correct_path}: {str(e)}")
                    failed = True
            else:
                os.remove(symlink_path)
                for item in items:
                    item = session.merge(item)
                    item.reset()
                    item.store_state()
                    session.merge(item)
                missing_files += 1
                logger.log("NOT_FOUND", f"Could not find file {filename} in rclone_path")

            session.commit()
            logger.log("FILES", f"Saved items to the database.")

            if failed:
                logger.warning(f"Failed to retarget some broken symlinks, recommended action: reset database.")

    def process_directory(directory, file_map):
        """Process a single directory for broken symlinks."""
        local_broken_symlinks = find_broken_symlinks(directory)
        logger.log("FILES", f"Found {len(local_broken_symlinks)} broken symlinks in {directory}")
        if not local_broken_symlinks:
            return

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(check_and_fix_symlink, symlink_path, file_map) for symlink_path in local_broken_symlinks]
            for future in as_completed(futures):
                future.result()

    start_time = time.time()
    logger.log("FILES", f"Finding and fixing broken symlinks in {library_path} using files from {rclone_path}")

    file_map = build_file_map(rclone_path)
    if not file_map:
        logger.log("FILES", f"No files found in rclone_path: {rclone_path}. Aborting fix_broken_symlinks.")
        return

    logger.log("FILES", f"Built file map for {rclone_path}")

    top_level_dirs = [os.path.join(library_path, d) for d in os.listdir(library_path) if os.path.isdir(os.path.join(library_path, d))]
    logger.log("FILES", f"Found top-level directories: {top_level_dirs}")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_directory, directory, file_map) for directory in top_level_dirs]
        if not futures:
            logger.log("FILES", f"No directories found in {library_path}. Aborting fix_broken_symlinks.")
            return
        for future in as_completed(futures):
            future.result()

    end_time = time.time()
    elapsed_time = end_time - start_time
    logger.log("FILES", f"Finished processing and retargeting broken symlinks. Time taken: {elapsed_time:.2f} seconds.")
    logger.log("FILES", f"Reset {missing_files} items to be rescraped due to missing rclone files.")

def get_items_from_filepath(session: Session, filepath: str) -> list["Movie"] | list["Episode"]:
    """Get items that match the imdb_id or season and episode from a file in library_path"""
    from program.media.item import Episode, Movie, Season, Show
    imdb_id_match = imdbid_pattern.search(filepath)
    season_number_match = season_pattern.search(filepath)
    episode_number_match = episode_pattern.search(filepath)

    if not imdb_id_match:
        logger.log("NOT_FOUND", f"Path missing imdb_id: {filepath}")
        return []

    imdb_id = imdb_id_match.group()
    season_number = int(season_number_match.group(1)) if season_number_match else None
    episode_number = int(episode_number_match.group(1)) if episode_number_match else None

    with session:
        items = []
        if season_number and episode_number:
            episode_numbers = [int(num) for num in re.findall(r"e(\d+)", filepath, re.IGNORECASE)]
            for ep_num in episode_numbers:
                query = (
                    session.query(Episode)
                    .join(Season, Episode.parent_id == Season.id)
                    .join(Show, Season.parent_id == Show.id)
                    .filter(Show.ids["imdb_id"] == imdb_id, Season.number == season_number, Episode.number == ep_num)
                )
                episode_item = query.with_entities(Episode).first()
                if episode_item:
                    items.append(episode_item)
        else:
            query = session.query(Movie).filter_by(imdb_id=imdb_id)
            movie_item = query.first()
            if movie_item:
                items.append(movie_item)

        if len(items) > 1:
            logger.log("FILES", f"Found multiple items in database for path: {filepath}")
            for item in items:
                logger.log("FILES", f"Found (multiple) {item.log_string} from filepath: {filepath}")
        return items